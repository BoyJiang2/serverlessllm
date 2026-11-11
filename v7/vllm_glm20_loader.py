"""V7 candidate: restore the historical GLM raw payload on pinned vLLM 0.20.1.

Source: GLM's existing vllm_loader.py; the original loader/payload stay unchanged.
Metadata acceptance is not a runtime qualification of the new engine.
"""

import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import dataclasses
import time

import torch
from sllm_store.event_trace import record_event, traced
from vllm.config import ModelConfig, VllmConfig
from vllm.distributed import get_tensor_model_parallel_rank
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader.default_loader import DefaultModelLoader
from vllm.model_executor.model_loader.utils import (
    initialize_model,
    process_weights_after_loading,
)
from vllm.utils.torch_utils import set_default_torch_dtype

FORMAT = "sllm-glm51-fp8-raw-v1"
TARGET_RAW_FORMAT = "sllm-glm51-fp8-raw-v2"
REVISION = "f396cf805182f4ca10fa675e1a99815b3ca384db"
LOAD_FORMAT = "sllm_glm51_raw_0201"
EXPORT_FORMAT = "sllm_glm51_raw_0201_export"
SOURCE_VERSIONS = ("0.19.1.dev1+g43a9b1afb.cu130", "2.10.0+cu130")
TARGET_VERSIONS = ("0.20.1", "2.11.0+cu130")


def validate_target_identity(identity):
    required = {
        "format", "source_revision", "config_sha256", "vllm_version",
        "vllm_commit", "torch_version", "model_type", "quantization", "dtype",
        "tensor_parallel_size", "seed", "VLLM_USE_DEEP_GEMM_E8M0",
    }
    if (
        set(identity) != required
        or identity["format"] != TARGET_RAW_FORMAT
        or identity["source_revision"] != REVISION
        or identity["vllm_commit"] != "132765e3560659ff63ebd236203672e991b70e08"
        or identity["model_type"] != "glm_moe_dsa"
        or identity["quantization"] != "fp8"
        or identity["dtype"] != "torch.bfloat16"
        or identity["tensor_parallel_size"] != 8
        or identity["seed"] != 20261024
        or identity["VLLM_USE_DEEP_GEMM_E8M0"] != "0"
        or (identity["vllm_version"], identity["torch_version"])
        != TARGET_VERSIONS
        or len(identity["config_sha256"]) != 64
    ):
        raise ValueError("Target-version GLM raw identity mismatch")


def validate_target_raw_state(metadata, rank, expected, aliases, identity):
    validate_target_identity(identity)
    if (
        metadata["format"] != TARGET_RAW_FORMAT
        or metadata["rank"] != rank
        or metadata["identity"] != identity
        or metadata["state"] != "before_quantization_postprocessing"
    ):
        raise ValueError("Target-version GLM raw checkpoint identity mismatch")
    rows = metadata["tensors"]
    if set(rows) != set(expected):
        missing = sorted(set(expected) - set(rows))
        extra = sorted(set(rows) - set(expected))
        raise ValueError(
            "Target GLM tensor key mismatch: "
            f"expected={len(expected)} actual={len(rows)} "
            f"missing={missing[:12]} extra={extra[:12]}"
        )
    for name, (shape, stride, dtype, size) in expected.items():
        row = rows[name]
        if (row["shape"], row["stride"], row["dtype"], row["bytes"]) != (
            shape, stride, dtype, size
        ):
            raise ValueError(f"Target GLM tensor metadata mismatch: {name}")
        if bool(row.get("empty")) != (size == 0):
            raise ValueError(f"Target GLM empty-state mismatch: {name}")
        if row.get("alias") != aliases.get(name):
            raise ValueError(f"Target GLM alias mismatch: {name}")


def _tensor_metadata(tensors):
    return {
        name: dict(
            shape=list(tensor.shape), stride=list(tensor.stride()),
            dtype=str(tensor.dtype),
            bytes=tensor.numel() * tensor.element_size(),
        )
        for name, tensor in tensors.items()
    }


def _save_target_raw_state(model, root, rank, identity):
    from sllm_store.torch import save_dict

    validate_target_identity(identity)
    directory = Path(root) / f"rank_{rank}"
    directory.mkdir(exist_ok=False)
    tensors = _named_tensors(model)
    rows = _tensor_metadata(tensors)
    cpu, storages = {}, {}
    for name, tensor in tensors.items():
        if not tensor.is_contiguous():
            raise ValueError(f"Unsupported GLM target raw stride: {name}")
        row = rows[name]
        if not tensor.numel():
            row["empty"] = True
            row["sha256"] = hashlib.sha256(b"").hexdigest()
            continue
        storage = (str(tensor.device), tensor.untyped_storage().data_ptr())
        view = (
            tensor.storage_offset(), tuple(tensor.shape),
            tuple(tensor.stride()), tensor.dtype,
        )
        if storage in storages:
            canonical, prior = storages[storage]
            if prior != view:
                raise ValueError(f"Unsupported GLM target overlapping views: {name}")
            row["alias"] = canonical
            row["sha256"] = rows[canonical]["sha256"]
            continue
        copied = tensor.detach().cpu().contiguous()
        if copied.storage_offset() or copied.untyped_storage().nbytes() != row["bytes"]:
            copied = copied.clone()
        cpu[name] = copied
        storages[storage] = (name, view)
        row["sha256"] = _digest(copied)
    save_dict(cpu, str(directory))
    del cpu
    gc.collect()
    for path in directory.iterdir():
        with path.open("rb") as stream:
            os.fsync(stream.fileno())
        if path.name.startswith("tensor.data"):
            _drop_page_cache([path])
    document = dict(
        format=TARGET_RAW_FORMAT, rank=rank, identity=identity,
        state="before_quantization_postprocessing", tensors=rows,
    )
    with (directory / "raw_state.json").open("x") as stream:
        json.dump(document, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def validate_raw_state(metadata, rank, expected, aliases):
    if (
        metadata["format"] != FORMAT
        or metadata["rank"] != rank
        or metadata["state"] != "before_quantization_postprocessing"
        or metadata["tensor_parallel_size"] != 8
        or metadata["VLLM_USE_DEEP_GEMM_E8M0"] != "0"
        or metadata["source_revision"] != REVISION
        or (metadata["vllm_version"], metadata["torch_version"])
        != SOURCE_VERSIONS
    ):
        raise ValueError("Historical raw checkpoint identity mismatch")
    rows = metadata["tensors"]
    if set(rows) != set(expected):
        missing = sorted(set(expected) - set(rows))
        extra = sorted(set(rows) - set(expected))
        raise ValueError(
            "Raw checkpoint tensor key mismatch: "
            f"expected={len(expected)} actual={len(rows)} "
            f"missing={missing[:12]} extra={extra[:12]}"
        )
    for name, (shape, stride, dtype, size) in expected.items():
        row = rows[name]
        if (row["shape"], row["stride"], row["dtype"], row["bytes"]) != (shape, stride, dtype, size):
            raise ValueError(f"Raw tensor metadata mismatch: {name}")
        if bool(row.get("empty")) != (size == 0):
            raise ValueError(f"Raw tensor empty-state mismatch: {name}")
        if row.get("alias") != aliases.get(name):
            raise ValueError(f"Raw tensor alias mismatch: {name}")


def _named_tensors(model):
    tensors = dict(model.named_parameters(remove_duplicate=False))
    tensors.update(model.named_buffers(remove_duplicate=False))
    return tensors


def _digest(tensor):
    data = tensor.detach().contiguous().reshape(-1).view(torch.uint8).cpu()
    return hashlib.sha256(memoryview(data.numpy())).hexdigest()


def _drop_page_cache(paths):
    """Release clean page cache for files that are no longer being read.

    The export path keeps one full TP shard per rank in anonymous memory while
    the same checkpoint is also resident as page cache. On this 2 TiB host the
    first export attempt (2026-11-10 14:26 +08) was killed at 85% of the save.
    Dropping clean pages does not touch the loaded weights: it only asks the
    kernel to forget file cache for files whose contents are already on the GPU
    or already fsynced.
    """
    dropped = 0
    for path in paths:
        try:
            handle = os.open(path, os.O_RDONLY)
        except OSError:
            continue
        try:
            os.posix_fadvise(handle, 0, 0, os.POSIX_FADV_DONTNEED)
            dropped += 1
        except OSError:
            pass
        finally:
            os.close(handle)
    return dropped


def _await_export_barrier(directory, ranks=8, timeout=1800):
    """Wait for every rank to finish native loading before releasing read cache.

    Without the barrier an early rank would drop cache that the remaining ranks
    are still reading, which only costs time; the barrier keeps the export at
    one SSD read pass.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    rank = get_tensor_model_parallel_rank()
    marker = root / f"rank-{rank}"
    if not marker.exists():
        marker.write_text(str(time.monotonic_ns()))
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if len(list(root.glob("rank-*"))) >= ranks:
            break
        time.sleep(min(1.0, timeout))
    return len(list(root.glob("rank-*")))


class ServerlessLLMLoader(BaseModelLoader):
    def __init__(self, load_config):
        super().__init__(load_config)
        extra = load_config.model_loader_extra_config
        if extra:
            if set(extra) != {"store_path", "identity"}:
                raise ValueError(
                    "Target GLM Store loader requires store_path and identity"
                )
            self.target_root = Path(extra["store_path"])
            self.target_identity = dict(extra["identity"])
            validate_target_identity(self.target_identity)
        else:
            self.target_root = None
            self.target_identity = None

    def download_model(self, model_config: ModelConfig) -> None:
        if not Path(model_config.model).is_dir():
            raise ValueError(
                "ServerlessLLM raw checkpoint must already exist locally"
            )

    @traced("full_weight_load", values_from=lambda args, kwargs: dict(
        rank=get_tensor_model_parallel_rank()))
    def load_model(
        self, vllm_config: VllmConfig, model_config: ModelConfig, prefix=""
    ):
        from sllm_store.vllm_v4_loader import check_engine_source

        if (importlib.metadata.version("vllm"), torch.__version__) != TARGET_VERSIONS:
            raise ValueError("V7 GLM loader requires the pinned target environment")
        check_engine_source()
        if self.target_identity is not None:
            validate_target_identity(self.target_identity)
        if (
            model_config.hf_config.model_type != "glm_moe_dsa"
            or model_config.quantization != "fp8"
            or model_config.dtype != torch.bfloat16
            or vllm_config.parallel_config.tensor_parallel_size != 8
            or os.environ.get("VLLM_USE_DEEP_GEMM_E8M0") != "0"
        ):
            raise ValueError(
                "this raw loader is scoped to GLM FP8 TP8 with E8M0 off"
            )
        device = torch.device(vllm_config.device_config.device)
        with set_default_torch_dtype(model_config.dtype):
            record_event("raw_skeleton_allocation_begin", rank=get_tensor_model_parallel_rank())
            with device:
                model = initialize_model(
                    vllm_config=vllm_config,
                    model_config=model_config,
                    prefix=prefix,
                )
            record_event("raw_skeleton_allocation_end", rank=get_tensor_model_parallel_rank())
            tensors = _named_tensors(model)
            self.expected = {
                name: (list(t.shape), list(t.stride()), str(t.dtype),
                       t.numel() * t.element_size())
                for name, t in tensors.items()
            }
            # Compare aliases before releasing constructor storage; zero-sized
            # tensors have no meaningful storage identity.
            self.aliases, storages = {}, {}
            for name, tensor in tensors.items():
                if not tensor.numel():
                    continue
                key = (str(tensor.device), tensor.untyped_storage().data_ptr())
                view = (tensor.storage_offset(), tuple(tensor.shape),
                        tuple(tensor.stride()), tensor.dtype)
                if key in storages:
                    canonical, prior = storages[key]
                    if view != prior:
                        raise ValueError("Unsupported overlapping constructor views")
                    self.aliases[name] = canonical
                else:
                    storages[key] = (name, view)
            # Preserve parameter subclasses and attributes; release GPU storage.
            for tensor in tensors.values():
                tensor.data = torch.empty(
                    0, device=tensor.device, dtype=tensor.dtype
                )
            del tensors
            gc.collect()
            torch.cuda.empty_cache()
            record_event("raw_skeleton_storage_released", rank=get_tensor_model_parallel_rank())
            self.load_weights(model, model_config)
            record_event("quantization_postprocess_begin", rank=get_tensor_model_parallel_rank())
            process_weights_after_loading(model, model_config, device)
            record_event("quantization_postprocess_end", rank=get_tensor_model_parallel_rank())
        return model.eval()

    @traced("weight_ingestion", values_from=lambda args, kwargs: dict(
        rank=get_tensor_model_parallel_rank()))
    def load_weights(self, model, model_config):
        from vllm.distributed import get_tensor_model_parallel_rank
        from sllm_store.torch import load_dict_owned

        rank = get_tensor_model_parallel_rank()
        directory = Path(model_config.model) / f"rank_{rank}"
        metadata = json.loads((directory / "raw_state.json").read_text())
        if self.target_identity is None:
            validate_raw_state(metadata, rank, self.expected, self.aliases)
        else:
            validate_target_raw_state(
                metadata, rank, self.expected, self.aliases,
                self.target_identity,
            )
        rows = metadata["tensors"]
        storage = Path(os.environ["STORAGE_PATH"]).resolve()
        identifier = str(directory.resolve().relative_to(storage))
        loaded = load_dict_owned(
            identifier,
            {"": torch.cuda.current_device()},
            storage_path=str(storage),
        )
        canonical = {
            name
            for name, row in rows.items()
            if not row.get("empty") and "alias" not in row
        }
        if set(loaded) != canonical:
            raise ValueError("Store returned an incomplete or extra tensor set")
        # Quantization may replace the shared CUDA allocation's owning tensor.
        model._sllm_storage_owners = tuple(loaded.values())
        verify = os.environ.get("SLLM_VERIFY_WEIGHTS") == "1"
        for name, tensor in _named_tensors(model).items():
            row = rows[name]
            if row.get("empty"):
                value = torch.empty(
                    row["shape"], device=tensor.device, dtype=tensor.dtype
                )
            else:
                value = loaded[row.get("alias", name)]
                if (
                    list(value.shape) != row["shape"]
                    or str(value.dtype) != row["dtype"]
                    or list(value.stride()) != row["stride"]
                ):
                    raise ValueError(
                        f"Store tensor shape/dtype mismatch: {name}"
                    )
                if verify and _digest(value) != row["sha256"]:
                    raise ValueError(f"Store tensor bytes mismatch: {name}")
            tensor.data = value


class NativeExportLoader(ServerlessLLMLoader):
    def __init__(self, load_config):
        super().__init__(load_config)
        if self.target_identity is None or self.target_root is None:
            raise ValueError("Native GLM export requires a target Store identity")
        self.native = DefaultModelLoader(dataclasses.replace(
            load_config, load_format="safetensors",
            model_loader_extra_config={},
        ))

    def load_model(self, vllm_config, model_config, prefix=""):
        from sllm_store.vllm_v4_loader import check_engine_source

        if (importlib.metadata.version("vllm"), torch.__version__) != TARGET_VERSIONS:
            raise ValueError("V7 GLM export requires the pinned target environment")
        check_engine_source()
        validate_target_identity(self.target_identity)
        if (
            model_config.hf_config.model_type != "glm_moe_dsa"
            or model_config.quantization != "fp8"
            or model_config.dtype != torch.bfloat16
            or vllm_config.parallel_config.tensor_parallel_size != 8
            or os.environ.get("VLLM_USE_DEEP_GEMM_E8M0") != "0"
        ):
            raise ValueError(
                "this raw export is scoped to GLM FP8 TP8 with E8M0 off"
            )
        # Native SafeTensors loading needs the normally allocated destination
        # parameters. Only the Store restore path releases the skeleton early.
        return BaseModelLoader.load_model(
            self, vllm_config, model_config, prefix=prefix,
        )

    def load_weights(self, model, model_config):
        self.native.load_weights(model, model_config)
        barrier = os.environ.get("SLLM_EXPORT_BARRIER")
        if barrier:
            # Every rank holds its shard on the GPU at this point; no rank is
            # still reading the checkpoint only after the last one reports in.
            _await_export_barrier(barrier)
        _drop_page_cache(sorted(Path(model_config.model).glob("*.safetensors")))
        _save_target_raw_state(
            model, self.target_root, get_tensor_model_parallel_rank(),
            self.target_identity,
        )


def register():
    register_model_loader(LOAD_FORMAT)(ServerlessLLMLoader)
    register_model_loader(EXPORT_FORMAT)(NativeExportLoader)
