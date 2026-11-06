"""CPU-only AST shutdown tests with the shared gRPC-style shutdown Future."""
import argparse
import ast
import asyncio
import pathlib
import sys
import types
import unittest

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--source", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2] / "sllm_store/server.py")
OPTIONS, TEST_ARGS = parser.parse_known_args()
tree = ast.parse(OPTIONS.source.read_text(encoding="utf-8"))
function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "serve")
CODE = compile(ast.Module(body=[function], type_ignores=[]), str(OPTIONS.source), "exec")


class FakeServer:
    def __init__(self):
        self.shutdown = asyncio.get_running_loop().create_future()
        self.entered = asyncio.Event()
        self.stopping = asyncio.Event()
        self.stop_gate = None
        self.stop_error = None
        self.graces = []

    def add_insecure_port(self, address):
        pass

    async def start(self):
        pass

    async def wait_for_termination(self):
        self.entered.set()
        await self.shutdown

    async def stop(self, grace):
        self.graces.append(grace)
        self.stopping.set()
        if self.stop_error is not None:
            raise self.stop_error
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        if not self.shutdown.done():
            self.shutdown.set_result(None)
        await asyncio.wait_for(asyncio.shield(self.shutdown), timeout=grace)


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.before = asyncio.all_tasks()
        self.server = FakeServer()
        self.release_error = None
        self.release_calls = 0
        def release():
            self.release_calls += 1
            if self.release_error is not None:
                raise self.release_error
        ns = {"asyncio": asyncio, "sys": sys,
              "grpc": types.SimpleNamespace(aio=types.SimpleNamespace(server=lambda: self.server)),
              "storage_pb2_grpc": types.SimpleNamespace(add_StorageServicer_to_server=lambda *args: None),
              "StorageServicer": lambda *args: types.SimpleNamespace(
                  storage=types.SimpleNamespace(release_memory_pool=release)),
              "logger": types.SimpleNamespace(info=lambda *args: None, error=lambda *args: None)}
        exec(CODE, ns)
        self.task = asyncio.create_task(ns["serve"]("127.0.0.1", 8073, "/unused", 1, 1048576, 67108864, True))
        await asyncio.wait_for(self.server.entered.wait(), .5)

    async def asyncTearDown(self):
        if not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await asyncio.sleep(0)
        pending = [task for task in asyncio.all_tasks()
                   if task not in self.before and task is not asyncio.current_task() and not task.done()]
        try:
            self.assertEqual(pending, [], "new shutdown waiter leaked")
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    async def test_single_cancel_stops_normally(self):
        self.task.cancel()
        self.assertIsNone(await asyncio.wait_for(self.task, .5))
        self.assertEqual(self.server.graces, [5])
        self.assertEqual(self.release_calls, 1)
        self.assertFalse(self.server.shutdown.cancelled())

    async def test_natural_termination(self):
        self.server.shutdown.set_result(None)
        self.assertIsNone(await asyncio.wait_for(self.task, .5))
        self.assertEqual(self.server.graces, [])
        self.assertEqual(self.release_calls, 1)

    async def test_stop_original_exception_identity(self):
        injected = RuntimeError("stop original")
        self.server.stop_error = injected
        self.task.cancel()
        with self.assertRaises(RuntimeError) as caught:
            await asyncio.wait_for(self.task, .5)
        self.assertIs(caught.exception, injected)
        self.assertEqual(self.release_calls, 0)

    async def test_second_cancel_remains_failure(self):
        self.server.stop_gate = asyncio.Event()
        self.task.cancel()
        await asyncio.wait_for(self.server.stopping.wait(), .5)
        self.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        self.assertEqual(self.server.graces, [5])
        self.assertEqual(self.release_calls, 0)

    async def test_hanging_stop_test_deadline_is_failure(self):
        self.server.stop_gate = asyncio.Event()
        self.task.cancel()
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(self.task, .05)
        self.assertEqual(self.server.graces, [5])

    async def test_waiter_original_exception_propagates(self):
        injected = RuntimeError("waiter original")
        self.server.shutdown.set_exception(injected)
        with self.assertRaises(RuntimeError) as caught:
            await asyncio.wait_for(self.task, .5)
        self.assertIs(caught.exception, injected)
        self.assertEqual(self.server.graces, [])

    async def test_release_failure_is_not_normal_exit(self):
        injected = RuntimeError("unregister failed")
        self.release_error = injected
        self.task.cancel()
        with self.assertRaises(RuntimeError) as caught:
            await self.task
        self.assertIs(caught.exception, injected)
        self.assertEqual(self.release_calls, 1)

    async def test_natural_termination_release_failure_propagates(self):
        injected = RuntimeError("unregister failed")
        self.release_error = injected
        self.server.shutdown.set_result(None)
        with self.assertRaises(RuntimeError) as caught:
            await self.task
        self.assertIs(caught.exception, injected)
        self.assertEqual(self.release_calls, 1)


if __name__ == "__main__":
    unittest.main(argv=[__file__, *TEST_ARGS], verbosity=2)
