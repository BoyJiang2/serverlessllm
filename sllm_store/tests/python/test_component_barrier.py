"""Deterministic CPU-only tests of actual S4a barrier/stop functions.

No subprocess, SDK, server, CUDA, sleeps, or runtime authorization. Fake process
exit/reap events prove protocol ordering only, not native/CUDA release.
"""
import ast
import contextlib
import gc
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import threading
import types
import unittest
import weakref
from unittest import mock

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("s4a_component_runner", HERE / "s4a_component_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
TREE = ast.parse((HERE / "test_kimi_tensor_component.py").read_text(encoding="utf-8"))
HELPERS = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
           and node.name == "wait_server_failure_barrier"]
FINALLY = next(node.finalbody for node in ast.walk(TREE) if isinstance(node, ast.Try)
               and node.finalbody and isinstance(node.finalbody[0], ast.Assign)
               and any(isinstance(t, ast.Name) and t.id == "primary"
                       for t in node.finalbody[0].targets))
BODY = ast.parse("raise primary_error").body
CLEANUP = compile(ast.fix_missing_locations(ast.Module(body=[ast.Try(
    body=BODY, handlers=[], orelse=[], finalbody=FINALLY)], type_ignores=[])), "actual_cleanup", "exec")
HELPER = compile(ast.Module(body=HELPERS, type_ignores=[]), "actual_barrier", "exec")


class Process:
    def __init__(self, label, pid, events, resistant=False, stuck=False):
        self.label, self.pid, self.events = label, pid, events
        self.alive, self.reaped = True, False
        self.resistant, self.stuck = resistant, stuck
        self.returncode = 0

    def poll(self):
        if not self.alive:
            self.reaped = True
            return self.returncode
        return None

    def send_signal(self, sig):
        self.events.append(self.label + ":SIGINT")
        if not self.resistant:
            self.alive = False

    def terminate(self):
        self.events.append(self.label + ":TERM")
        if not self.resistant:
            self.alive = False

    def kill(self):
        self.events.append(self.label + ":KILL")
        if not self.stuck:
            self.alive = False

    def wait(self, timeout):
        if self.alive:
            raise subprocess.TimeoutExpired(self.label, timeout)
        self.reaped = True
        self.events.append(self.label + ":REAP")
        return self.returncode


class BarrierTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.server = Process("server", 11, self.events)
        self.client = Process("client", 12, self.events, resistant=True)
        self.processes = {11: self.server, 12: self.client}
        self.patch = mock.patch.object(runner, "startticks", side_effect=lambda pid:
            7 if self.processes[pid].alive or not self.processes[pid].reaped else None)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.channel = types.SimpleNamespace(sendall=lambda data: self.events.append("ACK"))

    def test_success_server_sigint_zero_and_verified_reap(self):
        self.assertTrue(runner.graceful_server_stop(self.server, (11, 7),
                                                  runner.time.monotonic() + 5))
        self.assertEqual(self.events, ["server:SIGINT", "server:REAP"])
        self.assertTrue(self.server.reaped)

    def test_nonzero_server_never_graceful_pass(self):
        self.server.returncode = -15
        self.assertFalse(runner.graceful_server_stop(self.server, (11, 7),
                                                   runner.time.monotonic() + 5))
        self.assertTrue(self.server.reaped)

    def test_server_sigint_timeout_never_graceful_pass(self):
        self.server.resistant = True
        with self.assertRaises(subprocess.TimeoutExpired):
            runner.graceful_server_stop(self.server, (11, 7), runner.time.monotonic() + 5)
        self.assertTrue(self.server.alive)
        self.assertEqual(self.events, ["server:SIGINT"])

    def test_alive_server_no_ack_and_verified_reap_ack(self):
        self.assertFalse(runner.acknowledge_exit(self.channel, self.server, (11, 7)))
        self.assertNotIn("ACK", self.events)
        self.server.alive = False
        self.assertTrue(runner.acknowledge_exit(self.channel, self.server, (11, 7)))
        self.assertTrue(self.server.reaped)
        self.assertEqual(self.events, ["ACK"])

    def test_delayed_server_killed_reaped_before_ack_and_client_stop(self):
        self.server.resistant = True
        result = runner.failure_order(self.server, (11, 7), self.client, (12, 7),
                                      self.channel, runner.time.monotonic() + 5)
        self.assertEqual(result, "SERVER_REAPED_ACK_SENT_CLIENT_REAPED")
        self.assertLess(self.events.index("server:KILL"), self.events.index("server:REAP"))
        self.assertLess(self.events.index("server:REAP"), self.events.index("ACK"))
        self.assertLess(self.events.index("ACK"), self.events.index("client:TERM"))

    def test_unreapable_server_neither_ack_nor_client_stop(self):
        self.server.resistant = self.server.stuck = True
        self.assertFalse(runner.failure_order(self.server, (11, 7), self.client, (12, 7),
                                             self.channel, runner.time.monotonic() + 5))
        self.assertNotIn("ACK", self.events)
        self.assertNotIn("client:TERM", self.events)
        self.assertNotIn("client:KILL", self.events)

    def test_client_crash_is_not_barrier(self):
        self.client.alive = False
        result = runner.failure_order(self.server, (11, 7), self.client, (12, 7),
                                      self.channel, runner.time.monotonic() + 5)
        self.assertEqual(result, "SERVER_REAPED_CLIENT_ALREADY_EXITED_NO_BARRIER")
        self.assertNotIn("ACK", self.events)

    def test_expired_deadline_still_stops_server_first(self):
        runner.failure_order(self.server, (11, 7), self.client, (12, 7),
                             self.channel, runner.time.monotonic() - 1)
        self.assertLess(self.events.index("server:REAP"), self.events.index("client:TERM"))

    def test_actual_finally_retains_owner_and_primary_until_exit_ack(self):
        notified, ack = threading.Event(), threading.Event()
        primary = ValueError("original failure")
        caught = []
        stderr = io.StringIO()
        server_dead = {"value": False}

        class Owner:
            pass

        class Channel:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def sendall(self, data):
                self.data = data
                notified.set()

            def recv(self, size):
                if not ack.wait(2):
                    raise AssertionError("test ACK not supplied")
                return json.dumps({"server_exited": True, "pid": 11, "startticks": 7}).encode() + b"\n"

        channel = Channel()
        owner = Owner()
        reference = weakref.ref(owner)
        namespace = {"socket": types.SimpleNamespace(socket=lambda **kwargs: channel),
            "sys": sys, "json": json, "require": lambda condition, message:
                None if condition else self.fail(message),
            "server_has_exited": lambda *args: server_dead["value"],
            "args": types.SimpleNamespace(failure_barrier_fd=3, server_pid=11, server_startticks=7),
            "primary_error": primary, "actual": owner, "restored": {"packed": owner},
            "gc": gc, "torch": types.SimpleNamespace(cuda=types.SimpleNamespace(synchronize=lambda: None)),
            "load_attempted": True, "client": types.SimpleNamespace(
                unload_from_cpu=lambda *args: self.fail("must not RPC after server exit"),
                channel=types.SimpleNamespace(close=lambda: None))}
        del owner
        exec(HELPER, namespace)

        def cleanup():
            try:
                exec(CLEANUP, namespace)
            except BaseException as error:
                caught.append(error)

        with contextlib.redirect_stderr(stderr):
            thread = threading.Thread(target=cleanup, daemon=True)
            thread.start()
            try:
                self.assertTrue(notified.wait(2))
                self.assertIsNotNone(reference())
                self.assertEqual(len(namespace["restored"]), 1)
                self.assertIs(namespace["primary"], primary)
                self.assertIsNotNone(primary.__traceback__)
                self.assertFalse(ack.is_set())
                self.assertTrue(thread.is_alive())
            finally:
                server_dead["value"] = True
                ack.set()
                thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(caught, [primary])
        self.assertEqual(namespace["restored"], {})
        self.assertIsNone(namespace["actual"])
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(json.loads(stderr.getvalue())["failure_barrier"], "SERVER_EXIT_VERIFIED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
