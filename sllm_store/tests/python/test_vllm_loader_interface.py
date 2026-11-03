"""Check loader argument forwarding without importing Torch or vLLM."""
import ast
import contextlib
import os
import pathlib
import tempfile
import types
import unittest
from unittest import mock


class LoaderInterfaceTest(unittest.TestCase):
    def test_selected_model_config_and_prefix_reach_initializer(self):
        patch = (pathlib.Path(__file__).resolve().parents[2]
                 / "vllm_patch/sllm_load.patch").read_text()
        marker = "+++ b/vllm/model_executor/model_loader/sllm_loader.py"
        section = patch.split(marker, 1)[1].split("diff --git ", 1)[0]
        source = "".join(line[1:] for line in section.splitlines(True)
                         if line.startswith("+") and not line.startswith("+++"))
        tree = ast.parse(source)
        loader = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef)
                      and node.name == "ServerlessLLMLoader")
        method = next(node for node in loader.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == "load_model")

        class ReachedInitializer(Exception):
            pass

        initialize = mock.Mock(side_effect=ReachedInitializer)
        dtype_context = mock.Mock(return_value=contextlib.nullcontext())
        namespace = {
            "VllmConfig": object, "ModelConfig": object,
            "nn": types.SimpleNamespace(Module=object), "os": os,
            "set_default_torch_dtype": dtype_context,
            "torch": types.SimpleNamespace(
                device=lambda _: contextlib.nullcontext()),
            "initialize_model": initialize,
        }
        exec(compile(ast.Module(body=[method], type_ignores=[]),
                     "<official-sllm-loader>", "exec"), namespace)
        store = types.ModuleType("sllm_store")
        store.__path__ = []
        store_torch = types.ModuleType("sllm_store.torch")
        store_torch.load_dict = mock.Mock(
            side_effect=AssertionError("Store must not run in this test"))
        vllm = types.ModuleType("vllm")
        vllm.__path__ = []
        distributed = types.ModuleType("vllm.distributed")
        distributed.get_tensor_model_parallel_rank = lambda: 0
        modules = {"sllm_store": store, "sllm_store.torch": store_torch,
                   "vllm": vllm, "vllm.distributed": distributed}
        with tempfile.TemporaryDirectory(prefix="sllm-loader-api-") as folder:
            selected = types.SimpleNamespace(model=folder, dtype=object())
            fallback = types.SimpleNamespace(
                model=os.path.join(folder, "must-not-use-fallback"),
                dtype="must-not-use-fallback")
            config = types.SimpleNamespace(model_config=fallback)
            with mock.patch.dict("sys.modules", modules):
                with mock.patch.object(os.path, "join", wraps=os.path.join) as join:
                    with self.assertRaises(ReachedInitializer):
                        namespace["load_model"](
                            object(), vllm_config=config,
                            model_config=selected, prefix="selected_prefix")
                join.assert_any_call(selected.model, "rank_0")
            initialize.assert_called_once_with(
                vllm_config=config, model_config=selected,
                prefix="selected_prefix")
            dtype_context.assert_called_once_with(selected.dtype)
            store_torch.load_dict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
