"""V4 pre-postprocessing Store adapter, derived from the GLM raw loader.

GLM reference SHA256:
36a40e20f8d2d6b2e7b3346ec5e1c54d5598359b1b1756348ef97723e34fa502.
Only cold full-model TP8 restore is supported; no in-place reload or MTP.
"""

import dataclasses
import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import types

import torch
import vllm
from vllm.distributed import (
    get_tensor_model_parallel_rank,
    get_world_group,
)
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader.default_loader import DefaultModelLoader

from sllm_store.event_trace import traced

from sllm_store.v4_state import (
    CONFIG_SHA256,
    KERNEL_PACKAGES,
    REVISION,
    check_monolithic_capacity,
    file_digest,
    validate_complete,
    validate_expected,
    validate_identity,
    validate_rows,
    write_once,
)


def named_tensors(model):
    tensors = dict(model.named_parameters(remove_duplicate=False))
    tensors.update(model.named_buffers(remove_duplicate=False))
    return tensors


def tensor_metadata(model):
    parameters = dict(model.named_parameters(remove_duplicate=False))
    generated = {prefix + "." + name if prefix else name
                 for prefix, module in model.named_modules()
                 for name in module._non_persistent_buffers_set}
    return {
        name: {
            "shape": list(tensor.shape), "stride": list(tensor.stride()),
            "dtype": str(tensor.dtype),
            "bytes": tensor.numel() * tensor.element_size(),
            "kind": "parameter" if name in parameters else "buffer",
            "persistent": None if name in parameters else name not in generated,
        }
        for name, tensor in named_tensors(model).items()
    }


def quant_methods(model):
    return {
        name: {
            "class": type(module.quant_method).__module__ + "."
            + type(module.quant_method).__qualname__,
            "backends": {
                field: str(getattr(module.quant_method, field, None))
                for field in (
                    "backend", "mxfp4_backend", "fp8_backend",
                    "marlin_input_dtype",
                )
            },
        }
        for name, module in model.named_modules()
        if getattr(module, "quant_method", None) is not None
    }


def tensor_digest(tensor):
    data = tensor.detach().contiguous().reshape(-1).view(torch.uint8).cpu()
    return hashlib.sha256(memoryview(data.numpy())).hexdigest()


def check_engine_source():
    # Bind selected API/model/gate sources; this does not check the wheel ABI.
    path = Path(__file__).with_name("v4_source_pins.json")
    pins = json.loads(path.read_text())
    root = Path(vllm.__file__).parent
    for relative, expected in pins.items():
        if file_digest(root / relative) != expected:
            raise ValueError(
                f"V4 engine source differs from the pinned target: {relative}"
            )


def save_raw_model(model, root, rank, identity, names=None):
    from sllm_store.torch import save_dict

    validate_identity(identity)
    directory = Path(root) / f"rank_{rank}"
    directory.mkdir(exist_ok=False)
    tensors = named_tensors(model)
    if names is not None:
        selected = set(names)
        if selected - set(tensors):
            raise ValueError("Unknown tensor in raw export group")
        tensors = {name: tensor for name, tensor in tensors.items() if name in selected}
    rows = {name: row for name, row in tensor_metadata(model).items() if name in tensors}
    cpu, storages = {}, {}
    for name, tensor in tensors.items():
        if not tensor.is_contiguous():
            raise ValueError(f"Unsupported V4 raw tensor stride: {name}")
        row = rows[name]
        row["sha256"] = tensor_digest(tensor)
        if not tensor.numel():
            row["empty"] = True
            continue
        storage = (str(tensor.device), tensor.untyped_storage().data_ptr())
        view = (
            tensor.storage_offset(), tuple(tensor.shape),
            tuple(tensor.stride()), tensor.dtype,
        )
        if storage in storages:
            canonical, prior_view = storages[storage]
            if prior_view != view:
                raise ValueError(
                    f"Unsupported overlapping V4 views: {canonical}, {name}"
                )
            row["alias"] = canonical
        else:
            copied = tensor.detach().cpu().contiguous()
            if (copied.storage_offset()
                    or copied.untyped_storage().nbytes() != row["bytes"]):
                copied = copied.clone()
            cpu[name] = copied
            storages[storage] = (name, view)
    validate_rows(rows)
    save_dict(cpu, str(directory))
    del cpu
    gc.collect()
    files = {}
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file():
            raise ValueError("Unexpected linked/non-file Store payload")
        with path.open("rb") as file:
            os.fsync(file.fileno())
        files[path.name] = {
            "bytes": path.stat().st_size, "sha256": file_digest(path),
        }
    device = torch.cuda.get_device_properties(torch.cuda.current_device())
    write_once(directory / "raw_state.json", {
        "identity": identity, "rank": rank, "tensors": rows,
        "quant_methods": (quant_methods(model) if names is None else {
            prefix: method for prefix, method in quant_methods(model).items()
            if any(name.startswith(prefix + ".") for name in tensors)}),
        "files": files,
        "coordinates": dict(global_rank=rank, tp=rank, pp=0, dp=0),
        "device_uuid": str(device.uuid),
    })


class V4RawLoader(BaseModelLoader):
    def __init__(self, load_config):
        super().__init__(load_config)
        extra = load_config.model_loader_extra_config
        if set(extra) != {"store_path", "identity"}:
            raise ValueError("V4 loader requires only store_path and identity")
        self.identity = dict(extra["identity"])
        validate_identity(self.identity)
        self.root = Path(extra["store_path"])
        self.expected = None

    def download_model(self, model_config):
        if not Path(model_config.model).is_dir():
            raise ValueError("V4 Store requires local, preverified model assets")
        config_path = Path(model_config.model) / "config.json"
        if (file_digest(config_path) != CONFIG_SHA256
                or getattr(model_config, "hf_overrides", None)
                not in (None, {})):
            raise ValueError("V4 config differs from the frozen official input")

    def load_model(self, vllm_config, model_config, prefix=""):
        self.download_model(model_config)
        check_engine_source()
        parallel = vllm_config.parallel_config
        hf = model_config.hf_config
        if (model_config.revision != REVISION
                or model_config.tokenizer_revision != REVISION
                or hf.model_type != "deepseek_v4" or hf.expert_dtype != "fp4"
                or hf.n_routed_experts != 384 or hf.hc_mult != 4
                or hf.quantization_config.get("scale_fmt") != "ue8m0"
                or model_config.quantization != "deepseek_v4_fp8"
                or model_config.seed != 20261105
                or model_config.dtype != torch.bfloat16
                or parallel.tensor_parallel_size != 8
                or parallel.pipeline_parallel_size != 1
                or parallel.data_parallel_size != 1
                or parallel.enable_expert_parallel
                or parallel.distributed_executor_backend != "mp"
                or vllm_config.speculative_config is not None
                or model_config.max_model_len != 2048
                or not model_config.enforce_eager
                or vllm_config.scheduler_config.max_num_seqs != 1
                or vllm_config.scheduler_config.max_num_batched_tokens != 2048
                or vllm_config.cache_config.enable_prefix_caching
                or vllm_config.cache_config.cache_dtype not in ("fp8", "fp8_ds_mla")
                or vllm_config.attention_config.use_fp4_indexer_cache is not False):
            raise ValueError(
                "V4 raw loader requires FP4/FP8 TP8, EP/PP/DP/MTP off"
            )
        if (self.identity["torch_version"] != torch.__version__
                or importlib.metadata.version("vllm") != "0.20.1"):
            raise ValueError("V4 Store package version mismatch")
        if any(importlib.metadata.version(name) != expected
               for name, expected in KERNEL_PACKAGES.items()):
            raise ValueError("V4 kernel package versions differ from the profile")
        if (torch.cuda.get_device_capability() != (9, 0)
                or "H20" not in torch.cuda.get_device_name()):
            raise ValueError("This V4 adapter profile requires H20/SM90")
        world = get_world_group()
        self.rank = get_tensor_model_parallel_rank()
        if world.world_size != 8 or world.rank != self.rank:
            raise ValueError("V4 Store participants must be the TP8 world")
        self.prepare_store()
        model = super().load_model(vllm_config, model_config, prefix=prefix)
        self.finish_model(model)
        model._sllm_v4_stage = "postprocessed"
        return model

    def finish_model(self, model):
        pass

    def prepare_store(self):
        if isinstance(self, V4StoreLoader):
            storage = Path(os.environ["STORAGE_PATH"]).resolve()
            self.root.resolve().relative_to(storage)
            validate_complete(self.root, self.identity)
            manifest = self.root / f"rank_{self.rank}" / "raw_state.json"
            metadata = json.loads(manifest.read_text())
            device = torch.cuda.get_device_properties(torch.cuda.current_device())
            check_monolithic_capacity(metadata, device.total_memory)
        else:
            self.root.mkdir(parents=True, exist_ok=True)
            if (self.root / "COMPLETE.json").exists():
                raise ValueError("Cannot overwrite a completed V4 conversion")


class V4ExportLoader(V4RawLoader):
    def __init__(self, load_config):
        super().__init__(load_config)
        self.native = DefaultModelLoader(dataclasses.replace(
            load_config, load_format="safetensors", model_loader_extra_config={}
        ))

    def load_weights(self, model, model_config):
        self.native.load_weights(model, model_config)
        if any(
            getattr(getattr(module, "quant_method", None), "uses_meta_device", False)
            for module in model.modules()
        ):
            raise ValueError("V4 raw export rejects online quantization")
        self.export_raw(model)

    def export_raw(self, model):
        save_raw_model(model, self.root, self.rank, self.identity)


class V4StoreLoader(V4RawLoader):
    def load_weights(self, model, model_config):
        from sllm_store.torch import load_dict_owned

        if getattr(model, "_sllm_v4_stage", None) is not None:
            raise ValueError("V4 raw restore requires a fresh model")
        manifest = self.root / f"rank_{self.rank}" / "raw_state.json"
        metadata = json.loads(manifest.read_text())
        self.expected = tensor_metadata(model)
        validate_expected(metadata["tensors"], self.expected)
        if metadata["quant_methods"] != quant_methods(model):
            raise ValueError("V4 quantization method/backend mismatch")
        tensors = named_tensors(model)
        for tensor in tensors.values():
            tensor.data = torch.empty(
                0, device=tensor.device, dtype=tensor.dtype
            )
        del tensors
        gc.collect()
        torch.cuda.empty_cache()
        storage = Path(os.environ["STORAGE_PATH"]).resolve()
        directory = (self.root / f"rank_{self.rank}").resolve()
        identifier = str(directory.relative_to(storage))
        loaded = load_dict_owned(
            identifier, {"": torch.cuda.current_device()},
            storage_path=str(storage),
        )
        rows = metadata["tensors"]
        canonical = {name for name, row in rows.items()
                     if not row.get("empty") and "alias" not in row}
        if set(loaded) != canonical:
            raise ValueError("Store returned incomplete or extra V4 tensors")
        # Keep raw storage alive while native postprocessing replaces parameters.
        model._sllm_storage_owners = tuple(loaded.values())
        for name, tensor in named_tensors(model).items():
            row = rows[name]
            value = (
                torch.empty(
                    row["shape"], device=tensor.device, dtype=tensor.dtype
                ) if row.get("empty") else loaded[row.get("alias", name)]
            )
            if (list(value.shape) != row["shape"]
                    or str(value.dtype) != row["dtype"]
                    or list(value.stride()) != row["stride"]):
                raise ValueError(f"Wrong restored V4 tensor layout: {name}")
            tensor.data = value
        model._sllm_v4_stage = "restored_raw"


def register():
    register_model_loader("sllm_v4_export")(V4ExportLoader)
    register_model_loader("sllm_v4_store")(V4StoreLoader)
    register_model_loader("sllm_v4_grouped_export")(V4GroupedExportLoader)
    register_model_loader("sllm_v4_grouped_store")(V4GroupedStoreLoader)
    from sllm_store.v4_trace import install_v4_trace_hooks
    install_v4_trace_hooks()


def alias_metadata(model):
    rows, storages = tensor_metadata(model), {}
    for name, tensor in named_tensors(model).items():
        if not tensor.numel():
            continue
        key = str(tensor.device), tensor.untyped_storage().data_ptr()
        view = tensor.storage_offset(), tuple(tensor.shape), tuple(tensor.stride()), tensor.dtype
        if key in storages:
            canonical, previous = storages[key]
            if view != previous:
                raise ValueError("Grouped Store rejects overlapping distinct views")
            rows[name]["alias"] = canonical
        else:
            storages[key] = name, view
    return rows


class V4GroupedExportLoader(V4ExportLoader):
    def prepare_store(self):
        self.root.resolve().relative_to(Path(os.environ["STORAGE_PATH"]).resolve())
        super().prepare_store()

    def finish_model(self, model):
        from sllm_store.v4_grouped_state import complete_grouped_export_rank
        model._sllm_grouped_export_result = complete_grouped_export_rank(
            self.root, self.rank, self.identity
        )

    def export_raw(self, model):
        from sllm_store.v4_grouped_state import FORMAT, partition_groups

        methods = quant_methods(model)
        groups = partition_groups(alias_metadata(model), methods)
        entries = []
        for group in groups:
            root = self.root / "groups" / group["id"]
            root.mkdir(parents=True, exist_ok=True)
            save_raw_model(model, root, self.rank, self.identity, group["tensors"])
            path = root / f"rank_{self.rank}" / "raw_state.json"
            entries.append(dict(group, manifest_sha256=file_digest(path)))
        directory = self.root / f"rank_{self.rank}"
        directory.mkdir(exist_ok=False)
        write_once(directory / "grouped_state.json", dict(
            format=FORMAT, identity=self.identity, rank=self.rank,
            groups=entries, quant_methods=methods,
        ))


_GROUPED_CONTEXTS = {}
_NO_METHOD_OVERRIDE = object()


def grouped_export_result(model):
    """Small worker result for the existing engine apply_model/collective path."""
    if getattr(model, "_sllm_v4_stage", None) != "postprocessed":
        raise ValueError("Export worker has not completed native postprocessing")
    return dict(model._sllm_grouped_export_result)


def grouped_restore_contexts():
    return tuple(dict(conversion_id=key[0], rank=key[1],
                      active_group=value.active_group, failed=value.failed)
                 for key, value in _GROUPED_CONTEXTS.items())


def assert_no_raw_references(model, loaded):
    ranges = [(name, t.untyped_storage().data_ptr(), t.untyped_storage().data_ptr()
               + t.untyped_storage().nbytes()) for name, t in loaded.items() if t.numel()]
    pending, seen = [("model", model)], set()
    while pending:
        path, value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if torch.is_tensor(value):
            storage = value.untyped_storage()
            begin, size = storage.data_ptr(), storage.nbytes()
            for name, a, b in ranges:
                if size and begin < b and a < begin + size:
                    raise ValueError("Postprocessed model still references expert raw storage: "
                                     f"{path}; raw={name}; dtype={value.dtype}; "
                                     f"shape={tuple(value.shape)}; device={value.device}; "
                                     f"storage=[{begin:#x},{begin + size:#x}); raw_range=[{a:#x},{b:#x})")
        elif isinstance(value, dict):
            for key, item in value.items():
                label = repr(key) if isinstance(key, (str, int)) else type(key).__name__
                pending.extend(((path + ".key[" + label + "]", key),
                                (path + "[" + label + "]", item)))
        elif isinstance(value, (list, tuple, set)):
            pending.extend((f"{path}[{index}]", item) for index, item in enumerate(value))
        elif not isinstance(value, (types.FunctionType, types.MethodType, type)):
            if hasattr(value, "__dict__"):
                pending.extend((path + "." + name, item) for name, item in vars(value).items())
            for cls in type(value).__mro__:
                slots = vars(cls).get("__slots__", ())
                if isinstance(slots, str):
                    slots = (slots,)
                for name in slots:
                    if name not in ("__weakref__", "__dict__") and hasattr(value, name):
                        pending.append((path + "." + name, getattr(value, name)))


class _GroupedRestoreSession:
    def __init__(self, loader, model):
        from sllm_store.v4_grouped_state import partition_groups

        self.loader, self.model = loader, model
        self.completed, self.hooks, self.owners = [], [], {}
        self.active_group, self.failed = None, False
        self.key = loader.identity["conversion_id"], loader.rank
        if self.key in _GROUPED_CONTEXTS:
            raise ValueError("Previous grouped restore context has not been retired")
        expected = alias_metadata(model)
        methods = quant_methods(model)
        self.groups = partition_groups(expected, methods)
        stored = loader.grouped_metadata
        plan = [{k: g[k] for k in ("id", "module", "tensors")} for g in stored["groups"]]
        if self.groups != plan or methods != stored["quant_methods"]:
            raise ValueError("Grouped state differs from fresh native topology/order")
        self.documents = loader.group_documents
        for group in self.groups:
            stored_rows = self.documents[group["id"]]["tensors"]
            if any(stored_rows[name].get("alias") != expected[name].get("alias")
                   for name in group["tensors"]):
                raise ValueError("Grouped aliases differ from the fresh model")
            validate_expected(self.documents[group["id"]]["tensors"], {
                name: {k: v for k, v in expected[name].items() if k != "alias"}
                for name in group["tensors"]
            })
        self.modules = dict(model.named_modules())
        methods = [self.modules[g["module"]].quant_method for g in self.groups[1:]]
        if len({id(method) for method in methods}) != len(methods):
            raise ValueError("Expert quantization method objects must not be shared")
        _GROUPED_CONTEXTS[self.key] = self

    def begin(self):
        tensors = named_tensors(self.model)
        if any(t.device.type != "cuda" or t.device.index != torch.cuda.current_device()
               or getattr(t, "_vllm_is_uva_offloaded", False) for t in tensors.values()):
            raise ValueError("Grouped restore requires CUDA skeleton without CPU offload")
        for tensor in tensors.values():
            tensor.data = torch.empty(0, device=tensor.device, dtype=tensor.dtype)
        del tensors
        gc.collect()
        torch.cuda.empty_cache()
        self.model._sllm_v4_stage = "restoring_grouped_raw"
        remainder = self.load_group(self.groups[0])
        self.model._sllm_storage_owners = tuple(remainder.values())
        self.owners = {}
        for group in self.groups[1:]:
            method = self.modules[group["module"]].quant_method
            previous = vars(method).get("process_weights_after_loading", _NO_METHOD_OVERRIDE)
            self.hooks.append((method, previous))
            method.process_weights_after_loading = self.wrap(group, method.process_weights_after_loading)

    @traced("grouped_store_copy", values_from=lambda args, kwargs: dict(
        rank=args[0].loader.rank, group_id=(args[1] if len(args) > 1 else kwargs["group"])["id"]))
    def load_group(self, group):
        from sllm_store.torch import load_dict_owned

        self.active_group = group["id"]
        storage = Path(os.environ["STORAGE_PATH"]).resolve()
        directory = self.loader.root / "groups" / group["id"] / f"rank_{self.loader.rank}"
        loaded = load_dict_owned(str(directory.resolve().relative_to(storage)),
                                 {"": torch.cuda.current_device()}, storage_path=str(storage))
        self.owners = loaded  # Retain even malformed returned state on any exception.
        rows = self.documents[group["id"]]["tensors"]
        canonical = {n for n, row in rows.items() if not row.get("empty") and "alias" not in row}
        if set(loaded) != canonical:
            raise ValueError("Incomplete or extra grouped Store tensors")
        targets = named_tensors(self.model)
        for name, row in rows.items():
            tensor = targets[name]
            value = (torch.empty(row["shape"], device=tensor.device, dtype=tensor.dtype)
                     if row.get("empty") else loaded[row.get("alias", name)])
            if (list(value.shape) != row["shape"] or str(value.dtype) != row["dtype"]
                    or list(value.stride()) != row["stride"]):
                raise ValueError("Grouped tensor dtype/shape/stride mismatch")
            tensor.data = value
        return loaded

    def wrap(self, group, original):
        original = traced("grouped_expert_postprocess_host", values_from=lambda args, kwargs:
                          dict(rank=self.loader.rank, group_id=group["id"]))(original)
        def process(module):
            if group["id"] in self.completed or self.groups[len(self.completed) + 1] != group:
                raise ValueError("Expert postprocessing was repeated or reordered")
            loaded = self.load_group(group)
            original(module)
            tensors = named_tensors(self.model)
            for name, row in self.documents[group["id"]]["tensors"].items():
                if "alias" in row:
                    tensors[name].data = tensors[row["alias"]].data
            del tensors
            torch.cuda.synchronize()
            try:
                assert_no_raw_references(self.model, loaded)
            except ValueError as error:
                raise ValueError(f"{error}; group={group['id']}; rank={self.loader.rank}") from error
            self.completed.append(group["id"])
            loaded.clear()
            self.owners = {}
            self.active_group = None
        return process

    def restore_methods(self):
        for method, previous in self.hooks:
            if previous is _NO_METHOD_OVERRIDE:
                vars(method).pop("process_weights_after_loading", None)
            else:
                method.process_weights_after_loading = previous
        self.hooks.clear()

    def finish(self):
        if len(self.completed) != 61:
            raise ValueError("Not all expert groups were postprocessed")
        self.model._sllm_grouped_completed = tuple(self.completed)
        self.restore_methods()
        _GROUPED_CONTEXTS.pop(self.key)
        self.loader.session = None

    def fail(self):
        self.failed = True
        self.model._sllm_v4_stage = "failed_grouped_restore"
        self.restore_methods()


class V4GroupedStoreLoader(V4RawLoader):
    def prepare_store(self):
        from sllm_store.v4_grouped_state import validate_grouped_complete, validate_grouped_rank

        self.session = None
        if (self.identity["conversion_id"], self.rank) in _GROUPED_CONTEXTS:
            raise ValueError("Previous grouped restore context has not been retired")
        self.root.resolve().relative_to(Path(os.environ["STORAGE_PATH"]).resolve())
        validate_grouped_complete(self.root, self.identity)
        self.grouped_metadata, _, _ = validate_grouped_rank(self.root, self.rank, self.identity)
        self.group_documents, sizes = {}, {}
        for group in self.grouped_metadata["groups"]:
            path = self.root / "groups" / group["id"] / f"rank_{self.rank}" / "raw_state.json"
            document = json.loads(path.read_text())
            self.group_documents[group["id"]] = document
            sizes[group["id"]] = sum(row["bytes"] for row in document["tensors"].values()
                                     if not row.get("empty") and "alias" not in row)
        # Necessary lower bound only; transient/other runtime allocations still need admission.
        lower_bound = sum(sizes.values()) + max(v for k, v in sizes.items() if k != "remainder")
        if lower_bound > torch.cuda.get_device_properties(torch.cuda.current_device()).total_memory:
            raise ValueError("Grouped raw+Marlin lower bound exceeds device HBM")

    def load_weights(self, model, model_config):
        if getattr(model, "_sllm_v4_stage", None) is not None:
            raise ValueError("Grouped restore requires a fresh model")
        self.session = _GroupedRestoreSession(self, model)
        self.session.begin()

    def finish_model(self, model):
        self.session.finish()

    def load_model(self, *args, **kwargs):
        try:
            return super().load_model(*args, **kwargs)
        except BaseException:
            if getattr(self, "session", None) is not None:
                self.session.fail()
            raise
