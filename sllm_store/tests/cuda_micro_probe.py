"""One MiB CUDA probe, optionally eight-rank NCCL; no model, Store, or vLLM."""
import argparse
import hashlib
import json
import os
import uuid
from datetime import timedelta


def tp8_environment(env):
    if env.get("WORLD_SIZE") != "8" or env.get("LOCAL_WORLD_SIZE") != "8":
        raise ValueError("TP8 requires eight ranks on one host")
    rank = int(env["RANK"])
    local_rank = int(env["LOCAL_RANK"])
    if rank != local_rank or not 0 <= rank < 8:
        raise ValueError("rank must equal local rank in 0..7")
    if env.get("TORCHELASTIC_MAX_RESTARTS") != "0" or env.get("TORCHELASTIC_RESTART_COUNT") != "0":
        raise ValueError("torchrun restarts must be disabled")
    visible = env.get("CUDA_VISIBLE_DEVICES", "").split(",")
    if visible != env.get("NVIDIA_VISIBLE_DEVICES", "").split(",") or len(visible) != 8:
        raise ValueError("both visibility variables must bind the same eight UUIDs")
    canonical = []
    for value in visible:
        if not value.startswith("GPU-"):
            raise ValueError("explicit GPU UUID required")
        canonical.append("GPU-" + str(uuid.UUID(value[4:])))
    if len(set(canonical)) != 8:
        raise ValueError("duplicate GPU UUID")
    return rank, canonical


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tp8", action="store_true")
    args = parser.parse_args()
    rank, visible = tp8_environment(os.environ) if args.tp8 else (0, None)
    print(json.dumps({"event": "PYTHON_ENTERED"}), flush=True)
    import torch
    torch.set_num_threads(1)
    if not args.tp8:
        assert torch.cuda.device_count() == 1
        torch.cuda.reset_peak_memory_stats()
        value = torch.full((1 << 20,), 3, dtype=torch.uint8, device="cuda:0")
        torch.cuda.synchronize()
        host = value.cpu()
        assert host.numel() == 1 << 20 and bool((host == 3).all())
        assert int(host.sum()) == 3 * (1 << 20)
        print(json.dumps({"event": "CUDA_MICRO_COMPUTE_PASS", "torch": torch.__version__,
              "cuda": torch.version.cuda, "device": torch.cuda.get_device_name(0),
              "payload_bytes": value.numel() * value.element_size(),
              "allocator_peak_bytes": torch.cuda.max_memory_allocated(),
              "allocator_reserved_peak_bytes": torch.cuda.max_memory_reserved(),
              "scope": "one MiB CUDA arithmetic and CPU copy only; normal exit expected"}), flush=True)
        return
    assert torch.cuda.device_count() == 8
    torch.cuda.set_device(rank)
    physical_uuid = "GPU-" + str(uuid.UUID(str(torch.cuda.get_device_properties(rank).uuid).removeprefix("GPU-")))
    assert physical_uuid == visible[rank], "physical GPU UUID differs from binding"
    torch.cuda.reset_peak_memory_stats(rank)
    before, total = torch.cuda.mem_get_info(rank)
    distributed = torch.distributed
    distributed.init_process_group(backend="nccl", timeout=timedelta(seconds=20))
    try:
        value = torch.full((1 << 18,), rank, dtype=torch.float32, device=f"cuda:{rank}")
        distributed.all_reduce(value)
        torch.cuda.synchronize(rank)
        host = value.cpu()
        assert host.numel() == 1 << 18 and bool((host == 28).all())
        assert int(host.sum()) == 28 * (1 << 18)
        digest = hashlib.sha256(host.numpy().tobytes()).hexdigest()
        held, _ = torch.cuda.mem_get_info(rank)
        allocated = torch.cuda.max_memory_allocated(rank)
        reserved = torch.cuda.max_memory_reserved(rank)
        del value
        torch.cuda.empty_cache()
        torch.cuda.synchronize(rank)
    finally:
        distributed.destroy_process_group()
    after, _ = torch.cuda.mem_get_info(rank)
    print(json.dumps({"event": "TP8_CUDA_NCCL_MICRO_PASS", "rank": rank,
          "local_rank": rank, "world_size": 8, "device_count": 8,
          "device_uuid": physical_uuid, "torch": torch.__version__, "cuda": torch.version.cuda,
          "payload_bytes": 1 << 20, "dtype": "torch.float32", "reduced_value": 28,
          "raw_sha256": digest, "allocator_peak_bytes": allocated,
          "allocator_reserved_peak_bytes": reserved,
          "driver_free_bytes": {"before_collective": before, "tensor_held": held,
                                "after_destroy": after, "total": total},
          "scope": "one MiB per rank NCCL all_reduce only; no model or Store"}), flush=True)


if __name__ == "__main__":
    main()
