"""Execute the adapter and pinned BaseModelLoader with CPU-only stand-ins.

No Torch/vLLM/native import is performed. These tests prove control/data binding,
not real float8 arithmetic, CUDA ownership, engine startup or model correctness.
"""

import abc
import ast
import contextlib
import dataclasses
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

from sllm_store import v4_state as state
from test_v4_state import identity, rank_fixture


class Tensor:
    def __init__(self, shape, dtype):
        self.shape, self.dtype = list(shape), dtype
        step, self.steps = 1, []
        for dim in reversed(shape):
            self.steps.insert(0, step)
            step *= max(dim, 1)
        self.weight_loader = object()
        self.bound = self

    def stride(self):
        return self.steps

    def numel(self):
        return math.prod(self.shape)

    def element_size(self):
        return state.DTYPE_BYTES[self.dtype]

    @property
    def device(self):
        return "cpu-standin"

    @property
    def data(self):
        return self.bound

    @data.setter
    def data(self, value):
        self.bound = value
        self.shape, self.steps, self.dtype = value.shape, value.steps, value.dtype


class Model:
    def __init__(self):
        self.weight = Tensor([2], "torch.uint8")
        self.scale = Tensor([1], "torch.float8_e8m0fnu")
        self.fp8 = Tensor([2], "torch.float8_e4m3fn")
        self.hc = Tensor([1], "torch.float32")
        self.empty = Tensor([2, 0], "torch.int32")
        self.buffer = Tensor([1], "torch.bfloat16")
        self.generated = Tensor([1], "torch.bfloat16")
        self._non_persistent_buffers_set = {"generated"}

    def named_parameters(self, **kwargs):
        return [("w13_weight", self.weight), ("w13_alias", self.weight),
                ("w13_weight_scale", self.scale), ("nonexpert.weight", self.fp8),
                ("hc_attn_scale", self.hc), ("empty", self.empty)]

    def named_buffers(self, **kwargs):
        return [("buffer", self.buffer), ("generated", self.generated)]

    def named_modules(self):
        return [("", self)]

    def modules(self):
        return [self]

    def eval(self):
        return self


@dataclasses.dataclass
class LoadConfig:
    load_format: str
    model_loader_extra_config: dict
    device: object = None


class V4LoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = Model()
        self.events, self.prefixes = [], []
        torch = types.ModuleType("torch")
        torch.__version__, torch.bfloat16 = "2.11.0+cpu_test_standin", "torch.bfloat16"
        torch.device = lambda _: contextlib.nullcontext()
        torch.empty = lambda shape, **kw: Tensor([shape] if isinstance(shape, int) else shape, kw["dtype"])
        torch.cuda = types.SimpleNamespace(
            empty_cache=lambda: self.events.append("empty_cache"), current_device=lambda: 0,
            get_device_capability=lambda: (9, 0), get_device_name=lambda: "H20 stand-in",
            get_device_properties=lambda _: types.SimpleNamespace(total_memory=2048),
        )
        self.torch = torch
        fixtures = Path(__file__).resolve().parents[1] / "fixtures"
        (self.root / "config.json").write_bytes((fixtures / "v4_config.json").read_bytes())
        pins = json.loads((Path(__file__).resolve().parents[2]
                           / "sllm_store/v4_source_pins.json").read_text())
        base_source = (fixtures / "vllm0201_base_loader.py.txt").read_bytes()
        registry_source = (fixtures / "vllm0201_loader_registry.py.txt").read_bytes()
        self.assertEqual(hashlib.sha256(base_source).hexdigest(), pins["model_executor/model_loader/base_loader.py"])
        self.assertEqual(hashlib.sha256(registry_source).hexdigest(), pins["model_executor/model_loader/__init__.py"])

        def initialize(**kwargs):
            self.events.append("construct")
            self.prefixes.append(kwargs["prefix"])
            return self.model

        def postprocess(model, *args):
            self.events.append("postprocess")
            if self.post_error:
                raise self.post_error

        self.post_error = None
        namespace = dict(ABC=abc.ABC, abstractmethod=abc.abstractmethod,
                         torch=torch, nn=types.SimpleNamespace(Module=Model),
                         VllmConfig=object, ModelConfig=object, LoadConfig=object,
                         instrument=lambda **kw: lambda fn: fn, logger=mock.Mock(),
                         initialize_model=initialize, log_model_inspection=lambda _: None,
                         set_default_torch_dtype=lambda _: contextlib.nullcontext(),
                         process_weights_after_loading=postprocess,
                         current_platform=types.SimpleNamespace(is_cuda=lambda: False))
        nodes = [node for node in ast.parse(base_source).body
                 if isinstance(node, ast.ClassDef)
                 or isinstance(node, ast.FunctionDef) and node.name == "_has_online_quant"]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-BaseModelLoader>", "exec"), namespace)
        self.base = namespace["BaseModelLoader"]
        registry = dict(BaseModelLoader=self.base, logger=mock.Mock(), _LOAD_FORMAT_TO_MODEL_LOADER={})
        nodes = [node for node in ast.parse(registry_source).body
                 if isinstance(node, ast.FunctionDef) and node.name == "register_model_loader"]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "<pinned-registry>", "exec"), registry)
        self.registry = registry["_LOAD_FORMAT_TO_MODEL_LOADER"]
        modules = {}
        for name in ("vllm", "vllm.distributed", "vllm.model_executor",
                     "vllm.model_executor.model_loader",
                     "vllm.model_executor.model_loader.base_loader",
                     "vllm.model_executor.model_loader.default_loader"):
            modules[name] = types.ModuleType(name)
            modules[name].__path__ = []
        modules["vllm"].__file__ = str(self.root / "vllm/__init__.py")
        distributed = modules["vllm.distributed"]
        distributed.get_tensor_model_parallel_rank = lambda: 0
        distributed.get_world_group = lambda: types.SimpleNamespace(world_size=8, rank=0)
        modules["vllm.model_executor.model_loader"].register_model_loader = registry["register_model_loader"]
        modules["vllm.model_executor.model_loader.base_loader"].BaseModelLoader = self.base
        events = self.events

        class NativeLoader:
            def __init__(self, config):
                assert config.load_format == "safetensors"
                assert config.model_loader_extra_config == {}

            def load_weights(self, *args):
                events.append("native_load")

        modules["vllm.model_executor.model_loader.default_loader"].DefaultModelLoader = NativeLoader
        self.store = types.ModuleType("sllm_store.torch")
        self.store.load_dict_owned = mock.Mock(side_effect=self.load_store)
        modules.update({"torch": torch, "sllm_store.torch": self.store})
        self.module_patch = mock.patch.dict(sys.modules, modules)
        self.module_patch.start()
        self.addCleanup(self.module_patch.stop)
        path = Path(__file__).resolve().parents[2] / "sllm_store/vllm_v4_loader.py"
        spec = importlib.util.spec_from_file_location("tested_v4_loader", path)
        self.loader = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.loader)
        self.real_source_guard = self.loader.check_engine_source
        source_guard = mock.patch.object(self.loader, "check_engine_source")
        self.source_guard = source_guard.start()
        self.addCleanup(source_guard.stop)
        versions = dict(state.KERNEL_PACKAGES, vllm="0.20.1")
        version = mock.patch.object(self.loader.importlib.metadata, "version", side_effect=versions.__getitem__)
        version.start()
        self.addCleanup(version.stop)
        self.env = mock.patch.dict("os.environ", {"STORAGE_PATH": str(self.root)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.rows = self.loader.tensor_metadata(self.model)
        for row in self.rows.values():
            row["sha256"] = hashlib.sha256(bytes(row["bytes"])).hexdigest()
            if not row["bytes"]:
                row["empty"] = True
        self.rows["w13_alias"]["alias"] = "w13_weight"
        for rank in range(8):
            rank_fixture(self.root, rank, self.rows)
        state.publish_complete(self.root, identity())
        self.loaded = {name: Tensor(row["shape"], row["dtype"])
                       for name, row in self.rows.items()
                       if not row.get("empty") and "alias" not in row}
        self.config = LoadConfig("sllm_v4_store", dict(store_path=str(self.root), identity=identity()))
        self.model_config = types.SimpleNamespace(
            model=str(self.root), revision=state.REVISION, tokenizer_revision=state.REVISION, dtype=torch.bfloat16,
            quantization="deepseek_v4_fp8", seed=20261105, max_model_len=2048, enforce_eager=True,
            hf_config=types.SimpleNamespace(model_type="deepseek_v4", expert_dtype="fp4",
                                            quantization_config={"scale_fmt": "ue8m0"},
                                            n_routed_experts=384, hc_mult=4))
        self.vllm_config = types.SimpleNamespace(
            load_config=self.config, device_config=types.SimpleNamespace(device="cpu-standin"),
            parallel_config=types.SimpleNamespace(tensor_parallel_size=8, pipeline_parallel_size=1,
                                                  data_parallel_size=1, enable_expert_parallel=False,
                                                  distributed_executor_backend="mp"),
            scheduler_config=types.SimpleNamespace(max_num_seqs=1, max_num_batched_tokens=2048),
            cache_config=types.SimpleNamespace(enable_prefix_caching=False, cache_dtype="fp8"),
            attention_config=types.SimpleNamespace(use_fp4_indexer_cache=False), speculative_config=None)

    def load_store(self, *args, **kwargs):
        self.events.append("store_complete")
        self.assertEqual(self.model.weight.shape, [0])
        self.assertEqual(self.model.generated.shape, [0])
        return self.loaded

    def restore(self, **kwargs):
        adapter = self.loader.V4StoreLoader(self.config)
        model = adapter.load_model(self.vllm_config, self.model_config, **kwargs)
        return adapter, model

    def test_real_base_flow_preserves_alias_buffer_parameter_and_postprocesses_once(self):
        original = self.model.weight
        loader_attribute = original.weight_loader
        adapter, model = self.restore(prefix="test.prefix")
        self.assertIs(model.weight, original)
        self.assertIs(model.weight.weight_loader, loader_attribute)
        self.assertIs(model.weight.data, self.loaded["w13_weight"])
        self.assertIs(model.buffer.data, self.loaded["buffer"])
        self.assertIs(model.generated.data, self.loaded["generated"])
        self.assertEqual(model.empty.shape, [2, 0])
        self.assertEqual(model.scale.dtype, "torch.float8_e8m0fnu")
        self.assertEqual(model.fp8.dtype, "torch.float8_e4m3fn")
        self.assertEqual(self.prefixes, ["test.prefix"])
        self.assertEqual(self.events, ["construct", "empty_cache", "store_complete", "postprocess"])
        self.assertEqual(model._sllm_v4_stage, "postprocessed")
        with self.assertRaisesRegex(ValueError, "fresh model"):
            adapter.load_weights(model, self.model_config)

    def test_effective_runtime_profile_rejected_before_device_or_restore(self):
        cases = [(self.model_config, "revision", None),
                 (self.model_config, "tokenizer_revision", "main"),
                 (self.model_config, "quantization", "fp8"),
                 (self.model_config, "seed", 0),
                 (self.vllm_config.parallel_config, "distributed_executor_backend", None),
                 (self.vllm_config.cache_config, "cache_dtype", "auto"),
                 (self.vllm_config.cache_config, "cache_dtype", "bfloat16"),
                 (self.vllm_config.attention_config, "use_fp4_indexer_cache", True)]
        for target, field, value in cases:
            with self.subTest(field=field, value=value), mock.patch.object(target, field, value):
                with mock.patch.object(self.torch.cuda, "get_device_capability",
                                       side_effect=AssertionError("device touched before contract")):
                    with self.assertRaisesRegex(ValueError, "requires"):
                        self.restore()
                self.assertEqual(self.events, [])

    def test_bad_complete_or_topology_fails_before_skeleton_construction(self):
        (self.root / "COMPLETE.json").unlink()
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(self.events, [])
        self.vllm_config.parallel_config.enable_expert_parallel = True
        with self.assertRaises(ValueError):
            self.restore()
        self.assertEqual(self.events, [])

    def test_missing_scale_from_store_never_calls_postprocess(self):
        del self.loaded["w13_weight_scale"]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.restore()
        self.assertNotIn("postprocess", self.events)

    def test_copy_error_or_wrong_layout_never_marks_ready(self):
        self.store.load_dict_owned.side_effect = RuntimeError("copy/confirm failed")
        with self.assertRaisesRegex(RuntimeError, "copy/confirm failed"):
            self.restore()
        self.assertNotIn("postprocess", self.events)
        self.assertFalse(hasattr(self.model, "_sllm_v4_stage"))

    def test_wrong_loaded_e8m0_dtype_is_not_silently_reinterpreted(self):
        self.loaded["w13_weight_scale"].dtype = "torch.uint8"
        with self.assertRaisesRegex(ValueError, "layout"):
            self.restore()
        self.assertNotIn("postprocess", self.events)

    def test_selected_engine_source_change_is_rejected(self):
        directory = self.root / "vllm/model_executor/model_loader"
        directory.mkdir(parents=True)
        (directory / "base_loader.py").write_text("modified target source")
        with self.assertRaisesRegex(ValueError, "pinned target"):
            self.real_source_guard()

    def test_hbm_lower_bound_rejection_precedes_model_construction(self):
        self.torch.cuda.get_device_properties = lambda _: types.SimpleNamespace(total_memory=1)
        with self.assertRaisesRegex(ValueError, "exceeds device HBM"):
            self.restore()
        self.assertEqual(self.events, [])

    def test_postprocess_failure_is_not_a_postprocessed_model(self):
        self.post_error = RuntimeError("postprocessing failed")
        with self.assertRaisesRegex(RuntimeError, "postprocessing failed"):
            self.restore()
        self.assertEqual(self.model._sllm_v4_stage, "restored_raw")

    def test_export_delegates_native_load_before_raw_save_and_original_config_survives(self):
        export_root = self.root / "new-conversion"
        self.config.model_loader_extra_config["store_path"] = str(export_root)
        self.config.load_format = "sllm_v4_export"
        def save(model, *args):
            self.assertNotIn("postprocess", self.events)
            self.events.append("save_raw")
        with mock.patch.object(self.loader, "save_raw_model", side_effect=save):
            adapter = self.loader.V4ExportLoader(self.config)
            adapter.load_model(self.vllm_config, self.model_config)
        self.assertEqual(self.events, ["construct", "native_load", "save_raw", "postprocess"])
        self.assertEqual(self.config.load_format, "sllm_v4_export")
        self.assertIn("identity", self.config.model_loader_extra_config)

    def test_plugin_registers_two_concrete_loaders_using_pinned_registry(self):
        self.loader.register()
        self.assertEqual(set(self.registry), {"sllm_v4_export", "sllm_v4_store",
                                               "sllm_v4_grouped_export", "sllm_v4_grouped_store"})
        self.assertTrue(issubclass(self.registry["sllm_v4_store"], self.base))


if __name__ == "__main__":
    unittest.main()
