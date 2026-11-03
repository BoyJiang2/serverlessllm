"""ConfirmModel errors must propagate through the public load_dict call."""
import ast
import os
from pathlib import Path
import types
from typing import Dict, Optional, Union
import unittest
from unittest import mock


def extract(source, name):
    tree = ast.parse(source)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == name)
    return ast.Module(body=[node], type_ignores=[])


class RpcError(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status

    def code(self):
        return self.status


class ConfirmModelErrorTest(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2] / "sllm_store"
        namespace = {
            "logger": mock.Mock(),
            "grpc": types.SimpleNamespace(
                RpcError=RpcError,
                StatusCode=types.SimpleNamespace(CANCELLED="cancelled")),
            "storage_pb2": types.SimpleNamespace(
                ConfirmModelRequest=mock.Mock(return_value=object()),
                DeviceType=types.SimpleNamespace(DEVICE_TYPE_GPU=1)),
        }
        exec(compile(extract((root / "client.py").read_text(),
                             "confirm_model_loaded"), "<client>", "exec"),
             namespace)
        self.confirm = namespace["confirm_model_loaded"]
        self.load_node = extract((root / "torch.py").read_text(), "load_dict")

    def client(self, error=None):
        stub = mock.Mock()
        stub.ConfirmModel.side_effect = error
        client = types.SimpleNamespace(stub=stub)
        client.confirm_model_loaded = types.MethodType(self.confirm, client)
        return client

    def test_success_contract_is_unchanged(self):
        client = self.client()
        self.assertIsNone(client.confirm_model_loaded("component", "replica"))
        client.stub.ConfirmModel.assert_called_once()

    def test_cancelled_and_internal_preserve_original_error(self):
        for status in ("cancelled", "internal"):
            with self.subTest(status=status):
                error = RpcError(status)
                with self.assertRaises(RuntimeError) as caught:
                    self.client(error).confirm_model_loaded("component", "replica")
                self.assertIs(caught.exception.__cause__, error)
                self.assertIn("component", str(caught.exception))

    def test_public_load_dict_does_not_return_state_after_failed_confirmation(self):
        error = RpcError("internal")
        client = self.client(error)
        state = object()
        namespace = {
            "os": os, "Dict": Dict, "Optional": Optional, "Union": Union,
            "load_dict_non_blocking": mock.Mock(return_value=("replica", state)),
            "SllmStoreClient": lambda _: client,
        }
        exec(compile(self.load_node, "<torch-load-dict>", "exec"), namespace)
        with self.assertRaises(RuntimeError) as caught:
            namespace["load_dict"]("component", {"": 0})
        self.assertIs(caught.exception.__cause__, error)


if __name__ == "__main__":
    unittest.main()
