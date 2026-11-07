"""Worker-local grouped Store admission; no Torch, vLLM or CUDA imports."""
import json
from pathlib import Path

from sllm_store.v4_grouped_state import validate_grouped_complete
from sllm_store.v4_state import REVISION


def grouped_registration_plan(backend_config, storage_path):
    if (backend_config.get("load_format") != "sllm_v4_grouped_store"
            or type(backend_config.get("tensor_parallel_size")) is not int
            or backend_config["tensor_parallel_size"] != 8):
        raise ValueError("Grouped service registration requires the TP8 restore loader")
    extra = backend_config.get("model_loader_extra_config", {})
    if set(extra) != {"store_path", "identity"}:
        raise ValueError("Grouped registration requires store_path and identity")
    storage = Path(storage_path).resolve()
    requested = Path(extra["store_path"])
    if not requested.is_absolute():
        raise ValueError("Grouped store_path must be absolute on the worker")
    root = requested.resolve()
    root.relative_to(storage)
    # Expensive full payload hashing belongs to admission, before any registration.
    validate_grouped_complete(root, extra["identity"], verify_payload=True)
    plan = []
    for rank in range(8):
        manifest = json.loads((root / f"rank_{rank}/grouped_state.json").read_text())
        for group in manifest["groups"]:
            directory = root / "groups" / group["id"] / f"rank_{rank}"
            raw = json.loads((directory / "raw_state.json").read_text())
            # Native Model::Initialize counts tensor.data_N only, stopping at a gap.
            partitions = {name for name in raw["files"] if name.startswith("tensor.data_")}
            expected = {f"tensor.data_{n}" for n in range(len(partitions))}
            size = sum(raw["files"][name]["bytes"] for name in partitions)
            if partitions != expected or not partitions or size <= 0:
                raise ValueError("Grouped native tensor partitions must be contiguous and nonempty")
            plan.append((str(directory.relative_to(storage)), size))
    return plan


def is_v4_registration(config):
    backend = config.get("backend_config", {})
    loader = backend.get("load_format")
    return config.get("backend") == "vllm" and (
        backend.get("tokenizer_mode") == "deepseek_v4"
        or isinstance(loader, str) and loader.startswith("sllm_v4_"))


def _config_signature(config):
    return json.dumps(config, sort_keys=True, allow_nan=False)


def _validate_v4_topology(config):
    backend = config["backend_config"]
    allowed_scaling = [{"min_instances": 0, "max_instances": 1},
                       {"min_instances": 0, "max_instances": 1, "keep_alive": 3600}]
    if _config_signature(config.get("auto_scaling_config")) not in map(_config_signature, allowed_scaling):
        raise ValueError("V4 service requires min_instances=0, max_instances=1 and optional keep_alive=3600")
    profile = {"revision": REVISION, "tokenizer_revision": REVISION,
               "kv_cache_dtype": "fp8", "seed": 20261105,
               "distributed_executor_backend": "mp",
               "attention_config": {"use_fp4_indexer_cache": False},
               "torch_dtype": "bfloat16", "enforce_eager": True,
               "enable_prefix_caching": False, "max_model_len": 2048,
               "max_num_seqs": 1, "max_num_batched_tokens": 2048,
               "pretrained_model_name_or_path": "/mnt/raid/models/DeepSeek-V4-Pro"}
    for name, expected in profile.items():
        value = backend.get(name)
        if (type(value) is not type(expected)
                or _config_signature(value) != _config_signature(expected)):
            raise ValueError(f"V4 service requires explicit {name}={expected!r}")
    allowed = set(profile) | {"load_format", "model_loader_extra_config", "tokenizer_mode",
                              "tensor_parallel_size", "pipeline_parallel_size",
                              "data_parallel_size", "enable_expert_parallel"}
    unknown = set(backend) - allowed
    if unknown:
        raise ValueError(f"Unreviewed V4 service backend fields: {sorted(unknown)}")
    if backend.get("load_format") not in ("safetensors", "sllm_v4_grouped_store"):
        raise ValueError("V4 service requires safetensors or grouped Store loading")
    coordinates = (("num_gpus", config.get("num_gpus"), 8),
                   ("tensor_parallel_size", backend.get("tensor_parallel_size"), 8),
                   ("pipeline_parallel_size", backend.get("pipeline_parallel_size", 1), 1),
                   ("data_parallel_size", backend.get("data_parallel_size", 1), 1))
    for name, value, expected in coordinates:
        if type(value) is not int or value != expected:
            raise ValueError(f"V4 service requires integer {name}={expected}")
    if backend.get("enable_expert_parallel", False) is not False:
        raise ValueError("V4 grouped service requires expert parallel disabled")
    if backend.get("tokenizer_mode") != "deepseek_v4":
        raise ValueError("V4 service requires explicit deepseek_v4 tokenizer_mode")


def freeze_registration_config(config):
    if not is_v4_registration(config):
        return config
    config = json.loads(_config_signature(config))
    _validate_v4_topology(config)
    return config


def validate_active_registration(current, requested):
    if (is_v4_registration(current) or is_v4_registration(requested)) and (
            _config_signature(current) != _config_signature(requested)):
        raise ValueError("Active V4 registration configuration changed; delete the old route first")


def bind_v4_registration(config, bindings, registered):
    """Keep the prepared Store binding across the two frozen loading modes."""
    name = config.get("model")
    if not is_v4_registration(config) and name not in bindings:
        return config
    if not isinstance(name, str) or not name or config.get("backend") != "vllm":
        raise ValueError("V4 registration requires a named vLLM model")
    config = json.loads(_config_signature(config))
    _validate_v4_topology(config)
    common = json.loads(_config_signature(config))
    backend = common["backend_config"]
    loader = backend.pop("load_format", None)
    extra = backend.pop("model_loader_extra_config", None)
    if loader not in ("safetensors", "sllm_v4_grouped_store"):
        raise ValueError("V4 service requires safetensors or grouped Store loading")
    if loader == "safetensors" and extra not in (None, {}):
        raise ValueError("Native V4 registration cannot use grouped loader metadata")
    signature = _config_signature(common)
    store = _config_signature(extra)
    if name not in bindings:
        if loader != "sllm_v4_grouped_store" or name in registered:
            raise ValueError("V4 service requires a fresh prepared grouped Store binding first")
        bindings[name] = dict(common=signature, store=store)
    elif (bindings[name]["common"] != signature
          or loader == "sllm_v4_grouped_store" and bindings[name]["store"] != store):
        raise ValueError("V4 registration configuration or Store identity/path changed")
    if loader == "safetensors" and name not in registered:
        raise RuntimeError("V4 grouped Store preparation is incomplete")
    return config
