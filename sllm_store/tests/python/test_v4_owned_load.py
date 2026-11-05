"""CPU fault injection into the actual owned-load functions, with no CUDA."""

import ast
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


class V4OwnedLoadTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[2] / "sllm_store/torch.py"
        names = {"load_dict_owned", "owned_load_quarantine", "_finish_owned_load"}
        nodes = [node for node in ast.parse(path.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
        self.events = []
        self.state = {"opaque_owner": object()}

        def event(name, result):
            def call(*args, **kwargs):
                self.events.append(name)
                return result
            return call

        self.namespace = {
            "restore_tensors": event("adopt", self.state),
            "get_cuda_memory_handles": event("handles", {0: "opaque-ipc-handle"}),
            "get_device_uuid_map": event("devices", {0: "device-0"}),
            "_owned_load_quarantine": {},
            "load_dict_non_blocking": mock.Mock(return_value=("replica", self.state)),
        }
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<owned-load-functions>", "exec"), self.namespace)
        self.client = types.SimpleNamespace(
            load_into_gpu=mock.Mock(side_effect=event("dispatch", True)),
            confirm_model_loaded=mock.Mock(side_effect=event("confirm", None)),
        )

    def finish(self):
        return self.namespace["_finish_owned_load"](
            self.client, "model/rank_0", "replica-1", {}, {0: "raw-allocation"},
            {0: {}}, {0: []},
        )

    def test_adoption_precedes_handles_dispatch_and_confirm_before_return(self):
        replica, result = self.finish()
        self.assertIs(result, self.state)
        self.assertEqual(replica, "replica-1")
        self.assertEqual(self.events, ["adopt", "handles", "devices", "dispatch", "confirm"])
        self.assertEqual(self.namespace["owned_load_quarantine"](), ())

    def test_public_owned_path_requires_the_blocking_and_adoption_flags(self):
        native = types.ModuleType("sllm_store._C")
        native.v4_raw_restore_contract = 1
        with mock.patch.dict(sys.modules, {"sllm_store._C": native}):
            result = self.namespace["load_dict_owned"]("model", {"": 0}, "/store")
        self.assertIs(result, self.state)
        self.namespace["load_dict_non_blocking"].assert_called_once_with(
            "model", {"": 0}, "/store", _confirm_before_restore=True, _own_before_dispatch=True)

    def test_old_native_extension_is_rejected_before_any_allocation(self):
        native = types.ModuleType("sllm_store._C")
        with mock.patch.dict(sys.modules, {"sllm_store._C": native}):
            with self.assertRaisesRegex(RuntimeError, "lacks the V4"):
                self.namespace["load_dict_owned"]("model", {"": 0}, "/store")
        self.namespace["load_dict_non_blocking"].assert_not_called()

    def test_predispatch_failure_does_not_quarantine_an_unshared_allocation(self):
        self.namespace["get_cuda_memory_handles"] = mock.Mock(side_effect=RuntimeError("handle failed"))
        with self.assertRaisesRegex(RuntimeError, "handle failed"):
            self.finish()
        self.assertEqual(self.namespace["owned_load_quarantine"](), ())
        self.client.load_into_gpu.assert_not_called()

    def test_dispatch_false_raise_or_confirm_error_retains_the_owner(self):
        for failure in ("dispatch_false", "dispatch_raise", "confirm_false", "confirm_raise"):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == "dispatch_false":
                    self.client.load_into_gpu.side_effect = None
                    self.client.load_into_gpu.return_value = False
                elif failure == "dispatch_raise":
                    self.client.load_into_gpu.side_effect = RuntimeError("dispatch failed")
                elif failure == "confirm_false":
                    self.client.confirm_model_loaded.side_effect = None
                    self.client.confirm_model_loaded.return_value = False
                else:
                    self.client.confirm_model_loaded.side_effect = RuntimeError("IPC close unknown")
                with self.assertRaises(RuntimeError):
                    self.finish()
                owner = self.namespace["_owned_load_quarantine"]["replica-1"]
                self.assertEqual(owner[0], "model/rank_0")
                self.assertIs(owner[1], self.state)
                self.assertEqual(self.namespace["owned_load_quarantine"](), (
                    {"replica_uuid": "replica-1", "model_path": "model/rank_0"},))


if __name__ == "__main__":
    unittest.main()
