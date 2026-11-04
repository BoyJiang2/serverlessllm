"""CPU ordering checks of the actual official blocking/nonblocking function AST.

Mocks establish protocol order only. They do not prove CUDA/IPC release, native
close behavior, or cleanup after ambiguous RPC/confirmation failures.
"""
import argparse
import ast
import io
import json
import os
import pathlib
import time
import types
import unittest
from unittest import mock

parser = argparse.ArgumentParser(add_help=False)
parser.add_argument("--source", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2] / "sllm_store" / "torch.py")
OPTIONS, TEST_ARGS = parser.parse_known_args()
TREE = ast.parse(OPTIONS.source.read_text(encoding="utf-8"))
FUNCTIONS = [node for node in TREE.body if isinstance(node, ast.FunctionDef)
             and node.name in ("load_dict", "load_dict_non_blocking")]
for function in FUNCTIONS:
    function.returns = None
    for arg in (*function.args.args, *function.args.kwonlyargs):
        arg.annotation = None
CODE = compile(ast.Module(body=FUNCTIONS, type_ignores=[]), str(OPTIONS.source), "exec")


class OrderingTests(unittest.TestCase):
    def call(self, blocking=True, failure=None):
        events = []
        status = {"borrowed": False, "restored_while_borrowed": None, "raw_context_live": False}
        injected = RuntimeError("injected " + str(failure))
        state = {"packed": object()}

        def allocate(*args):
            status["raw_context_live"] = True
            events.append("allocate")
            return {0: 1}

        def gpu(*args):
            events.append("submit")
            # False can mean a lost reply after the server opened IPC handles.
            status["borrowed"] = True
            if failure == "rpc_exception":
                raise injected
            return failure != "rpc_false"

        def confirm(*args):
            events.append("confirm")
            if failure == "confirm":
                raise injected
            status["borrowed"] = False
            events.append("closed_ack")

        def restore(*args):
            events.append("restore")
            status["restored_while_borrowed"] = status["borrowed"]
            if failure == "restore":
                # Models the existing native consume/unwind contract only.
                events.append("native_owner_free")
                status["raw_context_live"] = False
                raise injected
            return state

        namespace = {"SllmStoreClient": lambda address: types.SimpleNamespace(
            load_into_cpu=lambda path: True, load_into_gpu=gpu, confirm_model_loaded=confirm),
            "os": os, "json": json, "time": time,
            "_expand_tensor_name": lambda *args: {},
            "calculate_device_memory": lambda *args: {0: 8257544},
            "allocate_cuda_memory": allocate, "restore_tensors": restore,
            "get_cuda_memory_handles": lambda *args: {0: "handle"},
            "get_device_uuid_map": lambda: {0: "device"},
            "calculate_tensor_device_offsets": lambda *args: ({0: {}}, {0: []}),
            "_get_uuid": lambda: "replica",
            "logger": types.SimpleNamespace(debug=lambda *args: None, info=lambda *args: None)}
        exec(CODE, namespace)
        error, result = None, None
        index = {"packed": [0, 4, [1], [1], "torch.int32"]}
        with mock.patch("builtins.open", return_value=io.StringIO(json.dumps(index))):
            try:
                function = namespace["load_dict" if blocking else "load_dict_non_blocking"]
                result = function("archive", {"": 0}, "/store")
            except Exception as caught:
                error = caught
        return events, status, result, error, injected, state

    def test_blocking_confirm_before_restore(self):
        events, status, result, error, _, state = self.call()
        self.assertIsNone(error)
        self.assertIs(result, state)
        self.assertFalse(status["restored_while_borrowed"], events)
        self.assertLess(events.index("closed_ack"), events.index("restore"))

    def test_native_restore_exception_free_only_after_closed_ack(self):
        events, status, _, error, injected, _ = self.call(failure="restore")
        self.assertIs(error, injected)
        self.assertFalse(status["borrowed"], events)
        self.assertLess(events.index("closed_ack"), events.index("native_owner_free"))

    def test_confirm_failure_never_restore_or_free_raw_context(self):
        events, status, _, error, injected, _ = self.call(failure="confirm")
        self.assertIs(error, injected)
        self.assertNotIn("restore", events)
        self.assertTrue(status["borrowed"])
        self.assertTrue(status["raw_context_live"])

    def test_rpc_false_never_restore_or_free_raw_context(self):
        events, status, _, error, _, _ = self.call(failure="rpc_false")
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "Failed to load model archive into GPU")
        self.assertNotIn("confirm", events)
        self.assertNotIn("restore", events)
        self.assertTrue(status["borrowed"])
        self.assertTrue(status["raw_context_live"])

    def test_rpc_exception_preserved_without_restore(self):
        events, status, _, error, injected, _ = self.call(failure="rpc_exception")
        self.assertIs(error, injected)
        self.assertNotIn("restore", events)
        self.assertTrue(status["raw_context_live"])

    def test_nonblocking_default_contract_unchanged(self):
        events, status, result, error, _, state = self.call(blocking=False)
        self.assertIsNone(error)
        self.assertEqual(result[0], "replica")
        self.assertIs(result[1], state)
        self.assertNotIn("confirm", events)
        self.assertTrue(status["restored_while_borrowed"])

    def test_public_blocking_confirms_once(self):
        events, _, _, error, _, _ = self.call()
        self.assertIsNone(error)
        self.assertEqual(events.count("confirm"), 1)


if __name__ == "__main__":
    unittest.main(argv=[__file__, *TEST_ARGS], verbosity=2)
