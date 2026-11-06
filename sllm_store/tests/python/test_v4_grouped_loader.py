"""Real adapter/BaseModelLoader and pinned postprocess traversal; CPU fakes only."""
import ast
import contextlib
import gc
import hashlib
import json
from pathlib import Path
import types
import unittest
import weakref
from unittest import mock

from sllm_store import v4_grouped_state as grouped
from sllm_store import v4_state as state
import test_v4_loader as raw_fixture
from test_v4_loader import Tensor
from test_v4_state import identity, rank_fixture


class Device:
    type = "cuda"
    index = 0
    def __str__(self):
        return "cuda:0"


class GroupTensor(Tensor):
    serial = 1000
    def __init__(self, shape, dtype):
        super().__init__(shape, dtype)
        self.bound = None
        self._device = Device()
        GroupTensor.serial += 100000
        pointer = GroupTensor.serial
        size = self.numel() * self.element_size()
        self._storage = types.SimpleNamespace(data_ptr=lambda: pointer, nbytes=lambda: size)
    @property
    def device(self):
        return self._device
    @property
    def data(self):
        return self.bound if self.bound is not None else self
    @data.setter
    def data(self, value):
        self.bound = value if value is not self else None
        self.shape, self.steps, self.dtype = value.shape, value.steps, value.dtype
    def untyped_storage(self):
        return self.bound.untyped_storage() if self.bound is not None else self._storage
    def storage_offset(self):
        return 0
    def is_contiguous(self):
        return True
    def detach(self):
        return self
    def contiguous(self):
        return self
    def to(self, *args, **kwargs):
        return GroupTensor(self.shape, self.dtype)
    def cpu(self):
        return GroupTensor(self.shape, self.dtype)
    def clone(self):
        return GroupTensor(self.shape, self.dtype)


class QuantBase:
    pass


class Attention:
    _non_persistent_buffers_set = set()
    def __init__(self, events):
        self.events = events
    def process_weights_after_loading(self, dtype):
        self.events.append("attention")


class GroupedLoaderTests(unittest.TestCase):
    def setUp(self):
        self.h = raw_fixture.V4LoaderTests("runTest")
        self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.loader = self.h.loader
        self.events, self.loads, self.weak = [], [], {}
        self.fail_at, self.retain_raw, self.fail_sync = None, False, False
        self.model = self.make_model()
        self.h.model = self.model
        self.h.torch.is_tensor = lambda v: isinstance(v, GroupTensor)
        self.h.torch.empty = lambda shape, **kw: GroupTensor([shape] if isinstance(shape, int) else shape, kw["dtype"])
        def sync():
            self.events.append("sync")
            if self.fail_sync:
                raise RuntimeError("synchronize failed")
        self.h.torch.cuda.synchronize = sync
        self.h.torch.cuda.get_device_properties = lambda _: types.SimpleNamespace(total_memory=2**50, uuid="cpu-fixture-device-0")
        self.h.store.load_dict_owned = mock.Mock(side_effect=self.load)
        # Execute the pinned upstream two-pass function, isolating only its imports/context.
        source = Path(__file__).resolve().parents[1] / "fixtures/vllm0201_postprocess_utils.py.txt"
        pins = json.loads((Path(__file__).resolve().parents[2] / "sllm_store/v4_source_pins.json").read_text())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), pins["model_executor/model_loader/utils.py"])
        node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "process_weights_after_loading")
        ns = dict(QuantizeMethodBase=QuantBase, device_loading_context=lambda *a: contextlib.nullcontext(), Attention=Attention, MLAAttention=Attention)
        tree = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
        exec(compile(ast.fix_missing_locations(tree), "pinned-postprocess", "exec"), ns)
        self.native_postprocess = ns["process_weights_after_loading"]
        self.h.base.load_model.__globals__["process_weights_after_loading"] = self.native_postprocess
        self.root = self.h.root / "grouped"
        self.root.mkdir()
        self.h.config.load_format = "sllm_v4_grouped_store"
        self.h.config.model_loader_extra_config["store_path"] = str(self.root)

    def make_model(self):
        owner = self
        class Model:
            _non_persistent_buffers_set = set()
            def named_parameters(self, **kwargs): return list(self.params.items())
            def named_buffers(self, **kwargs): return list(self.buffers.items())
            def named_modules(self): return [("", self)] + list(self.nodes.items()) + [("attention", self.attention)]
            def modules(self): return [m for _, m in self.named_modules()]
            def eval(self): return self
        model = Model()
        model.params = {"hc.scale": GroupTensor([3], "torch.float32")}
        model.buffers, model.nodes = {}, {}
        for n in range(61):
            prefix = f"model.layers.{n}.experts"
            def process(method, module):
                owner.events.append(module.prefix)
                raw = model.params[module.prefix + ".w13_weight"]
                if owner.retain_raw:
                    class Holder:
                        __slots__ = "raw"
                    holder = Holder()
                    view = GroupTensor([0], raw.dtype)
                    view._storage = raw.untyped_storage()
                    holder.raw = view
                    method.cached = holder
                for suffix in grouped.EXPERT_SUFFIXES:
                    name = module.prefix + "." + suffix
                    shape, dtype = ([1], "torch.float8_e8m0fnu") if "scale" in suffix else ([1], "torch.int32")
                    model.params[name] = GroupTensor(shape, dtype)
                method.moe_quant_config = types.SimpleNamespace(scale=model.params[module.prefix + ".w13_weight_scale"])
                if owner.fail_at == module.prefix:
                    raise RuntimeError("postprocess failed")
            cls = type("Mxfp4MoEMethod", (QuantBase,), {"__module__": "quant", "process_weights_after_loading": process})
            method = cls()
            method.mxfp4_backend = "Mxfp4MoeBackend.MARLIN"
            model.nodes[prefix] = types.SimpleNamespace(prefix=prefix, quant_method=method, _non_persistent_buffers_set={"route_buffer"})
            for suffix in grouped.EXPERT_SUFFIXES:
                model.params[prefix + "." + suffix] = GroupTensor([1] if "scale" in suffix else [4], "torch.uint8")
            model.buffers[prefix + ".route_buffer"] = GroupTensor([1], "torch.int64")
        model.attention = Attention(self.events)
        return model

    def populate(self):
        rows = self.loader.alias_metadata(self.model)
        for row in rows.values():
            row["sha256"] = hashlib.sha256(bytes(row["bytes"])).hexdigest()
        methods = self.loader.quant_methods(self.model)
        plan = grouped.partition_groups(rows, methods)
        results = []
        for rank in range(8):
            entries = []
            for group in plan:
                directory = self.root / "groups" / group["id"]
                directory.mkdir(parents=True, exist_ok=True)
                subset = {n: rows[n] for n in group["tensors"]}
                rank_fixture(directory, rank, subset, grouped.group_methods(methods, subset))
                entries.append(dict(group, manifest_sha256=state.file_digest(directory / f"rank_{rank}/raw_state.json")))
            directory = self.root / f"rank_{rank}"
            directory.mkdir()
            state.write_once(directory / "grouped_state.json", dict(format=grouped.FORMAT, identity=identity(), rank=rank, groups=entries, quant_methods=methods))
            results.append(grouped.complete_grouped_export_rank(self.root, rank, identity()))
        grouped.publish_grouped_complete(self.root, identity(), results)

    def load(self, identifier, *args, **kwargs):
        group = Path(identifier).parent.name
        if group != "remainder":
            gc.collect()
            for prior, refs in self.weak.items():
                if prior != "remainder": self.assertTrue(all(r() is None for r in refs), prior)
        self.loads.append(group)
        rows = json.loads((self.h.root / identifier / "raw_state.json").read_text())["tensors"]
        result = {n: GroupTensor(row["shape"], row["dtype"]) for n, row in rows.items() if not row.get("empty") and "alias" not in row}
        self.weak[group] = [weakref.ref(v) for v in result.values()]
        return result

    def restore(self):
        adapter = self.loader.V4GroupedStoreLoader(self.h.config)
        result = adapter.load_model(self.h.vllm_config, self.h.model_config)
        return adapter, result

    def test_grouped_restore_once_fence_owner_retirement_then_attention(self):
        self.populate()
        adapter, model = self.restore()
        self.assertEqual(model._sllm_v4_stage, "postprocessed")
        self.assertEqual(self.loads, ["remainder"] + [f"expert_{i:04d}" for i in range(61)])
        self.assertEqual(self.events, [v for i in range(61)
                         for v in (f"model.layers.{i}.experts", "sync")] + ["attention"])
        gc.collect()
        self.assertTrue(all(r() is None for g, refs in self.weak.items() if g != "remainder" for r in refs))
        self.assertTrue(all(r() is not None for r in self.weak["remainder"]))
        self.assertFalse(self.loader.grouped_restore_contexts())
        self.assertIsNone(adapter.session)
        self.assertTrue(all("process_weights_after_loading" not in vars(n.quant_method) for n in model.nodes.values()))
        self.assertTrue(all("route_buffer" not in name for g in grouped.partition_groups(self.loader.alias_metadata(model), self.loader.quant_methods(model))[1:] for name in g["tensors"]))

    def test_postprocess_failure_retains_owner_and_restores_methods(self):
        self.populate()
        self.fail_at = "model.layers.2.experts"
        with self.assertRaisesRegex(RuntimeError, "postprocess failed"):
            self.restore()
        self.assertEqual(self.model._sllm_v4_stage, "failed_grouped_restore")
        self.assertEqual(self.loads[-1], "expert_0002")
        self.assertTrue(all(r() is not None for r in self.weak["expert_0002"]))
        self.assertTrue(self.loader.grouped_restore_contexts()[0]["failed"])
        self.assertTrue(all("process_weights_after_loading" not in vars(n.quant_method) for n in self.model.nodes.values()))

    def test_empty_raw_view_hidden_in_slotted_config_prevents_release(self):
        self.populate()
        self.retain_raw = True
        with self.assertRaisesRegex(ValueError, "still references"):
            self.restore()
        self.assertEqual(self.loads, ["remainder", "expert_0000"])
        self.assertTrue(all(r() is not None for r in self.weak["expert_0000"]))

    def test_fence_failure_retains_current_raw_owner(self):
        self.populate()
        self.fail_sync = True
        with self.assertRaisesRegex(RuntimeError, "synchronize failed"):
            self.restore()
        self.assertTrue(all(r() is not None for r in self.weak["expert_0000"]))

    def test_lower_bound_failure_before_model_construction(self):
        self.populate()
        self.h.torch.cuda.get_device_properties = lambda _: types.SimpleNamespace(total_memory=1)
        self.h.events.clear()
        with self.assertRaisesRegex(ValueError, "exceeds device HBM"):
            self.restore()
        self.assertFalse(self.h.events)
        self.assertFalse(self.loads)

    def test_cpu_or_uva_skeleton_is_rejected_before_store_load(self):
        self.populate()
        self.model.params["hc.scale"]._vllm_is_uva_offloaded = True
        with self.assertRaisesRegex(ValueError, "without CPU offload"):
            self.restore()
        self.assertFalse(self.loads)

    def test_repeated_native_postprocess_is_rejected_without_reloading_group(self):
        self.populate()
        def repeated(model, *args):
            node = next(iter(model.nodes.values()))
            node.quant_method.process_weights_after_loading(node)
            node.quant_method.process_weights_after_loading(node)
        self.h.base.load_model.__globals__["process_weights_after_loading"] = repeated
        with self.assertRaisesRegex(ValueError, "repeated or reordered"):
            self.restore()
        self.assertEqual(self.loads, ["remainder", "expert_0000"])

    def test_missing_native_postprocess_cannot_mark_model_postprocessed(self):
        self.populate()
        def skip_last(model, *args):
            for node in list(model.nodes.values())[:-1]:
                node.quant_method.process_weights_after_loading(node)
        self.h.base.load_model.__globals__["process_weights_after_loading"] = skip_last
        with self.assertRaisesRegex(ValueError, "Not all expert groups"):
            self.restore()
        self.assertEqual(self.model._sllm_v4_stage, "failed_grouped_restore")

    def test_malformed_owned_state_is_quarantined_before_binding(self):
        self.populate()
        self.h.store.load_dict_owned.side_effect = lambda *args, **kw: {"unexpected": GroupTensor([1], "torch.uint8")}
        with self.assertRaisesRegex(ValueError, "Incomplete or extra"):
            self.restore()
        self.assertTrue(self.loader.grouped_restore_contexts()[0]["failed"])
        self.assertTrue(next(iter(self.loader._GROUPED_CONTEXTS.values())).owners)

    def test_grouped_export_keeps_raw_stage_and_bounded_group_cpu_copies(self):
        canonical = "model.layers.0.experts.w13_weight"
        alias = "model.layers.0.experts.a_alias"
        self.model.params[alias] = self.model.params[canonical]
        exported, refs = [], []
        def save(cpu, directory):
            directory = Path(directory)
            # Earlier CPU group staging must have died before this group is saved.
            self.assertTrue(all(r() is None for r in refs))
            refs[:] = [weakref.ref(t) for t in cpu.values()]
            exported.append(tuple(cpu))
            offset, index = 0, {}
            for name, tensor in cpu.items():
                size = tensor.numel() * tensor.element_size()
                index[name] = [offset, size, list(tensor.shape), tensor.stride(), tensor.dtype]
                offset += size
            (directory / "tensor.data_0").write_bytes(bytes(offset))
            (directory / "tensor_index.json").write_text(json.dumps(index))
        self.h.store.save_dict = save
        root = self.h.root / "grouped-export"
        self.h.config.load_format = "sllm_v4_grouped_export"
        self.h.config.model_loader_extra_config["store_path"] = str(root)
        with mock.patch.object(self.loader, "tensor_digest", side_effect=lambda t: hashlib.sha256(bytes(t.numel() * t.element_size())).hexdigest()):
            adapter = self.loader.V4GroupedExportLoader(self.h.config)
            adapter.load_model(self.h.vllm_config, self.h.model_config)
        document, _, _ = grouped.validate_grouped_rank(root, 0, identity(), True)
        self.assertEqual(len(exported), 62)
        self.assertTrue(all(r() is None for r in refs))
        self.assertIn("native_load", self.h.events)
        self.assertFalse((root / "COMPLETE.json").exists())
        self.assertTrue((root / "rank_0/export_done.json").is_file())
        result = self.loader.grouped_export_result(self.model)
        self.assertEqual(result["rank"], 0)
        json.dumps(result)  # Worker receipt contains no model or Tensor objects.
        raw = json.loads((root / "groups/expert_0000/rank_0/raw_state.json").read_text())["tensors"]
        self.assertEqual(raw[alias]["alias"], canonical)
        self.assertEqual(raw[canonical]["dtype"], "torch.uint8")

    def test_export_postprocess_failure_never_writes_done_receipt(self):
        root = self.h.root / "failed-export"
        self.h.config.model_loader_extra_config["store_path"] = str(root)
        def save_raw(model):
            (root / "raw-saved-marker").write_text("raw files existed before postprocessing")
        def fail(*args):
            raise RuntimeError("native export postprocess failed")
        self.h.base.load_model.__globals__["process_weights_after_loading"] = fail
        adapter = self.loader.V4GroupedExportLoader(self.h.config)
        with mock.patch.object(adapter, "export_raw", side_effect=save_raw):
            with self.assertRaisesRegex(RuntimeError, "export postprocess failed"):
                adapter.load_model(self.h.vllm_config, self.h.model_config)
        self.assertTrue((root / "raw-saved-marker").is_file())
        self.assertFalse((root / "rank_0/export_done.json").exists())
        with self.assertRaisesRegex(ValueError, "has not completed"):
            self.loader.grouped_export_result(self.model)

    def test_failed_context_blocks_retry_before_constructing_another_model(self):
        self.populate()
        self.fail_sync = True
        with self.assertRaisesRegex(RuntimeError, "synchronize failed"):
            self.restore()
        self.h.events.clear()
        with self.assertRaisesRegex(ValueError, "context has not been retired"):
            self.restore()
        self.assertFalse(self.h.events)

    def test_grouped_export_cannot_write_outside_store_root(self):
        path = self.h.root.parent / ("outside-" + self.h.root.name)
        self.h.config.model_loader_extra_config["store_path"] = str(path)
        adapter = self.loader.V4GroupedExportLoader(self.h.config)
        self.h.events.clear()
        with self.assertRaises(ValueError):
            adapter.load_model(self.h.vllm_config, self.h.model_config)
        self.assertFalse(self.h.events)
        self.assertFalse(path.exists())

    def test_shared_quant_method_is_rejected_before_store_load(self):
        self.populate()
        nodes = list(self.model.nodes.values())
        nodes[1].quant_method = nodes[0].quant_method
        with self.assertRaisesRegex(ValueError, "must not be shared"):
            self.restore()
        self.assertFalse(self.loads)

    def test_nonfresh_model_is_not_rebound(self):
        self.populate()
        self.model._sllm_v4_stage = "postprocessed"
        with self.assertRaisesRegex(ValueError, "fresh model"):
            self.restore()
        self.assertFalse(self.loads)

    def test_alias_rebound_to_converted_canonical_storage(self):
        first = "model.layers.0.experts.w13_weight"
        alias = first + "_alias"
        self.model.params[alias] = self.model.params[first]
        self.populate()
        _, model = self.restore()
        self.assertEqual(model.params[alias].untyped_storage().data_ptr(), model.params[first].untyped_storage().data_ptr())
        self.assertEqual(model.params[alias].dtype, "torch.int32")

if __name__ == "__main__":
    unittest.main()
