"""Worker-local grouped Store admission; no Torch, vLLM or CUDA imports."""
import json
from pathlib import Path

from sllm_store.v4_grouped_state import validate_grouped_complete


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
