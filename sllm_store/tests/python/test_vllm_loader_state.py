"""CPU round trips of the actual loader class extracted from the official patch."""
import ast
import collections
import contextlib
import gc
import os
import pathlib
import tempfile
import types
import unittest
from typing import Dict, Optional
from unittest import mock

import torch
from torch import nn


class LoaderStateTest(unittest.TestCase):
    def round_trip(self, saved_model, restored_model):
        patch = (pathlib.Path(__file__).resolve().parents[2]
                 / "vllm_patch/sllm_load.patch").read_text()
        section = patch.split(
            "+++ b/vllm/model_executor/model_loader/sllm_loader.py", 1
        )[1].split("diff --git ", 1)[0]
        source = "".join(line[1:] for line in section.splitlines(True)
                         if line.startswith("+") and not line.startswith("+++"))
        loader = next(node for node in ast.parse(source).body
                      if isinstance(node, ast.ClassDef)
                      and node.name == "ServerlessLLMLoader")
        archive = {}
        store = types.ModuleType("sllm_store.torch")
        store.save_dict = lambda state, path: archive.update(
            (key, tensor.clone()) for key, tensor in state.items())
        store.load_dict = lambda *args: archive
        store_package = types.ModuleType("sllm_store")
        store_package.__path__ = []
        vllm = types.ModuleType("vllm")
        vllm.__path__ = []
        distributed = types.ModuleType("vllm.distributed")
        distributed.get_tensor_model_parallel_rank = lambda: 0
        namespace = dict(
            BaseModelLoader=object, LoadConfig=object, VllmConfig=object,
            ModelConfig=object, Dict=Dict, Optional=Optional, nn=nn,
            collections=collections, os=os, gc=gc,
            initialize_model=lambda **kwargs: restored_model,
            set_default_torch_dtype=lambda _: contextlib.nullcontext(),
            torch=types.SimpleNamespace(
                Tensor=torch.Tensor, nn=nn,
                device=lambda _: torch.device("cpu"),
                empty=lambda *a, **kw: torch.empty(
                    *a, **dict(kw, device="cpu")),
                cuda=types.SimpleNamespace(current_device=lambda: 0)),
        )
        exec(compile(ast.Module(body=[loader], type_ignores=[]),
                     "<official-sllm-loader>", "exec"), namespace)
        cls = namespace["ServerlessLLMLoader"]
        original_to = torch.Tensor.to

        def cpu_to(tensor, *args, **kwargs):
            if args and str(args[0]).startswith("cuda"):
                args = ("cpu",) + args[1:]
            return original_to(tensor, *args, **kwargs)

        with tempfile.TemporaryDirectory(prefix="sllm-loader-state-") as folder:
            config = types.SimpleNamespace(model=folder, dtype=torch.bfloat16)
            with mock.patch.dict("sys.modules", {
                "sllm_store": store_package, "sllm_store.torch": store,
                "vllm": vllm, "vllm.distributed": distributed,
            }), mock.patch.object(torch.Tensor, "to", cpu_to):
                cls.save_model(saved_model, folder)
                result = cls.__new__(cls).load_model(
                    vllm_config=object(), model_config=config)
        return result, archive

    def test_marlin_empty_g_idx_round_trip(self):
        saved, restored = nn.Module(), nn.Module()
        names = ("w13_weight_g_idx", "w2_weight_g_idx",
                 "w13_g_idx_sort_indices", "w2_g_idx_sort_indices")
        for name in names:
            saved.register_parameter(name, nn.Parameter(
                torch.empty(384, 0, dtype=torch.int32), requires_grad=False))
            restored.register_parameter(name, nn.Parameter(
                torch.zeros(384, 8, dtype=torch.int32), requires_grad=False))
        result, archive = self.round_trip(saved, restored)
        for name in names:
            self.assertIn(name, archive)
            self.assertEqual(tuple(getattr(result, name).shape), (384, 0))
            self.assertEqual(getattr(result, name).dtype, torch.int32)

    def test_persistent_attention_scale_round_trip(self):
        saved, restored = nn.Module(), nn.Module()
        saved.register_buffer("_k_scale", torch.tensor(0.25))
        restored.register_buffer("_k_scale", torch.tensor(1.0))
        result, archive = self.round_trip(saved, restored)
        self.assertEqual(result._k_scale.item(), 0.25)
        self.assertEqual(result._k_scale.untyped_storage().data_ptr(),
                         archive["_k_scale"].untyped_storage().data_ptr())

    def test_same_parameter_module_alias_round_trip(self):
        def model(value):
            module = nn.Module()
            module.fused_qkv_a_proj = nn.Linear(2, 2, bias=False)
            module.fused_qkv_a_proj.weight.data.fill_(value)
            module.mla_attn = nn.Module()
            module.mla_attn.fused_qkv_a_proj = module.fused_qkv_a_proj
            return module
        saved, restored = model(7.0), model(0.0)
        parameter = restored.fused_qkv_a_proj.weight
        parameter.weight_loader = object()
        result, archive = self.round_trip(saved, restored)
        self.assertIs(result.fused_qkv_a_proj.weight, parameter)
        self.assertIs(result.mla_attn.fused_qkv_a_proj.weight, parameter)
        self.assertTrue(torch.equal(parameter, torch.full((2, 2), 7.0)))
        self.assertEqual(parameter.untyped_storage().data_ptr(),
                         next(iter(archive.values())).untyped_storage().data_ptr())
        self.assertTrue(hasattr(parameter, "weight_loader"))


if __name__ == "__main__":
    unittest.main()
