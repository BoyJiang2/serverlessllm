"""GLM-5.1 FP8 raw TP checkpoints for the pinned vLLM integration experiment."""

import gc
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path

import torch
from sllm_store.event_trace import record_event, traced
from vllm.config import ModelConfig, VllmConfig
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader.utils import (
    initialize_model,
    process_weights_after_loading,
)
from vllm.utils.torch_utils import set_default_torch_dtype

FORMAT = "sllm-glm51-fp8-raw-v1"
REVISION = "f396cf805182f4ca10fa675e1a99815b3ca384db"


def _named_tensors(model):
    tensors = dict(model.named_parameters(remove_duplicate=False))
    tensors.update(model.named_buffers(remove_duplicate=False))
    return tensors


def _digest(tensor):
    data = tensor.detach().contiguous().reshape(-1).view(torch.uint8).cpu()
    return hashlib.sha256(memoryview(data.numpy())).hexdigest()


def save_raw_model(model, output_path):
    """Save TP weights before vLLM quantization postprocessing."""
    from vllm.distributed import (
        get_tensor_model_parallel_rank,
        get_tensor_model_parallel_world_size,
    )
    from sllm_store.torch import save_dict

    if os.environ.get("VLLM_USE_DEEP_GEMM_E8M0") != "0":
        raise ValueError("raw FP8 integration requires E8M0 requantization off")
    if get_tensor_model_parallel_world_size() != 8:
        raise ValueError("raw export requires TP8")
    if os.environ.get("SLLM_MODEL_REVISION") != REVISION:
        raise ValueError("raw export source revision is not frozen")
    rank = get_tensor_model_parallel_rank()
    directory = Path(output_path) / f"rank_{rank}"
    directory.mkdir(parents=True, exist_ok=False)
    cpu, rows, storages = {}, {}, {}
    for name, tensor in _named_tensors(model).items():
        if not tensor.is_contiguous():
            raise ValueError(f"unsupported pre-process tensor stride: {name}")
        row = {
            "shape": list(tensor.shape),
            "stride": list(tensor.stride()),
            "dtype": str(tensor.dtype),
            "bytes": tensor.numel() * tensor.element_size(),
        }
        if not tensor.numel():
            row.update(empty=True, sha256=hashlib.sha256(b"").hexdigest())
        else:
            storage = (str(tensor.device), tensor.untyped_storage().data_ptr())
            view = (
                tensor.storage_offset(),
                tuple(tensor.shape),
                tuple(tensor.stride()),
                tensor.dtype,
            )
            if storage in storages:
                canonical, prior = storages[storage]
                if prior != view:
                    raise ValueError(
                        "unsupported overlapping raw tensor views: "
                        f"{canonical}, {name}"
                    )
                row.update(alias=canonical, sha256=rows[canonical]["sha256"])
            else:
                copied = tensor.detach().cpu().contiguous()
                if (
                    copied.storage_offset()
                    or copied.untyped_storage().nbytes() != row["bytes"]
                ):
                    copied = copied.clone()
                assert copied.untyped_storage().nbytes() == row["bytes"]
                cpu[name] = copied
                row["sha256"] = _digest(copied)
                storages[storage] = (name, view)
        rows[name] = row
    if not {"torch.float8_e4m3fn", "torch.float32", "torch.bfloat16"}.issubset(
        {row["dtype"] for row in rows.values()}
    ):
        raise ValueError("expected official mixed FP8/F32/BF16 state")
    save_dict(cpu, str(directory))
    del cpu
    gc.collect()
    for path in directory.iterdir():
        with path.open("rb") as file:
            os.fsync(file.fileno())
    document = {
        "format": FORMAT,
        "rank": rank,
        "tensor_parallel_size": 8,
        "state": "before_quantization_postprocessing",
        "tensors": rows,
        "torch_version": torch.__version__,
        "vllm_version": importlib.metadata.version("vllm"),
        "source_revision": REVISION,
        "VLLM_USE_DEEP_GEMM_E8M0": "0",
    }
    with (directory / "raw_state.json").open("x") as file:
        json.dump(document, file, indent=2)
        file.flush()
        os.fsync(file.fileno())


class ServerlessLLMLoader(BaseModelLoader):
    def download_model(self, model_config: ModelConfig) -> None:
        if not Path(model_config.model).is_dir():
            raise ValueError(
                "ServerlessLLM raw checkpoint must already exist locally"
            )

    def load_model(
        self, vllm_config: VllmConfig, model_config: ModelConfig, prefix=""
    ):
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
            record_event("raw_skeleton_allocation_begin")
            with device:
                model = initialize_model(
                    vllm_config=vllm_config,
                    model_config=model_config,
                    prefix=prefix,
                )
            record_event("raw_skeleton_allocation_end")
            tensors = _named_tensors(model)
            self.expected = {
                name: (list(t.shape), list(t.stride()), str(t.dtype))
                for name, t in tensors.items()
            }
            # Preserve parameter subclasses and attributes; release GPU storage.
            for tensor in tensors.values():
                tensor.data = torch.empty(
                    0, device=tensor.device, dtype=tensor.dtype
                )
            del tensors
            gc.collect()
            torch.cuda.empty_cache()
            record_event("raw_skeleton_storage_released")
            self.load_weights(model, model_config)
            record_event("quantization_postprocess_begin")
            process_weights_after_loading(model, model_config, device)
            record_event("quantization_postprocess_end")
        return model.eval()

    @traced("weight_ingestion")
    def load_weights(self, model, model_config):
        from vllm.distributed import get_tensor_model_parallel_rank
        from sllm_store.torch import load_dict

        rank = get_tensor_model_parallel_rank()
        directory = Path(model_config.model) / f"rank_{rank}"
        metadata = json.loads((directory / "raw_state.json").read_text())
        if (
            metadata["format"] != FORMAT
            or metadata["rank"] != rank
            or metadata["state"] != "before_quantization_postprocessing"
            or metadata["tensor_parallel_size"] != 8
            or metadata["VLLM_USE_DEEP_GEMM_E8M0"] != "0"
            or metadata["source_revision"] != REVISION
            or metadata["vllm_version"] != importlib.metadata.version("vllm")
            or metadata["torch_version"] != torch.__version__
        ):
            raise ValueError("raw checkpoint state or ABI mismatch")
        rows = metadata["tensors"]
        if set(rows) != set(self.expected):
            raise ValueError("raw checkpoint tensor key mismatch")
        for name, (shape, stride, dtype) in self.expected.items():
            if (
                rows[name]["shape"] != shape
                or rows[name]["stride"] != stride
                or rows[name]["dtype"] != dtype
            ):
                raise ValueError(f"raw tensor metadata mismatch: {name}")
        storage = Path(os.environ["STORAGE_PATH"]).resolve()
        identifier = str(directory.resolve().relative_to(storage))
        loaded = load_dict(
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
                ):
                    raise ValueError(
                        f"Store tensor shape/dtype mismatch: {name}"
                    )
                if verify and _digest(value) != row["sha256"]:
                    raise ValueError(f"Store tensor bytes mismatch: {name}")
            tensor.data = value


def register():
    register_model_loader("serverless_llm")(ServerlessLLMLoader)
