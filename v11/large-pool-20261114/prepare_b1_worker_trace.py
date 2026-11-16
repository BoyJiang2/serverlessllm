"""Build isolated B1 worker overlays from pinned installed Python sources."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import time


WORKER = "gpu_worker.py"
RUNNER = "gpu_model_runner.py"
ADAPTER = "vllm_multi_process_adapter.py"
HELPER = "v11_b1_worker_trace.py"
MODULE = "sllm_store.v11_b1_worker_trace"


def record(name, **fields):
    from sllm_store.event_trace import record_event

    record_event(name, **fields)


def storage_bytes(tensors):
    from sllm_store.v11_hbm_receipt import storage_ranges, union_bytes

    return union_bytes(storage_ranges(tensors))


def trace_enabled():
    return bool(os.environ.get("SLLM_EVENT_DIR"))


def weight_ready(worker):
    if not trace_enabled():
        return
    ready_ns = time.monotonic_ns()
    runner = worker.model_runner
    runner._sllm_v11_rank = worker.rank
    model = runner.get_model()
    from sllm_store.v11_hbm_receipt import storage_ranges, union_bytes

    weights = storage_ranges(model.parameters())
    buffers = storage_ranges(model.buffers())
    sizes = dict(parameter_storage_bytes=union_bytes(weights),
                 buffer_storage_bytes=union_bytes(buffers),
                 parameter_and_buffer_storage_bytes=union_bytes(weights | buffers))
    record("b1_weight_ready_storage", rank=worker.rank, ready_ns=ready_ns,
           storage_sample_end_ns=time.monotonic_ns(), **sizes)


def kv_budget(worker, config):
    if not trace_enabled():
        return
    record("b1_kv_budget", rank=worker.rank,
           requested_bytes=worker.cache_config.kv_cache_memory_bytes,
           available_bytes=getattr(worker, "available_kv_cache_memory_bytes", None),
           gpu_memory_utilization=worker.cache_config.gpu_memory_utilization,
           layout_bytes=sum(layout.size for layout in config.kv_cache_tensors),
           num_blocks=config.num_blocks)


def kv_worker_ready(worker):
    if trace_enabled():
        record("b1_kv_worker_ready", rank=worker.rank, ready_ns=time.monotonic_ns())


def timed_call(name, fn, fields, *args, **kwargs):
    begin = time.monotonic_ns()
    try:
        result = fn(*args, **kwargs)
    except Exception as error:
        end = time.monotonic_ns()
        record(name, begin_ns=begin, end_ns=end,
               status="failed", error_type=type(error).__name__, **fields)
        raise
    end = time.monotonic_ns()
    record(name, begin_ns=begin, end_ns=end, status="ok", **fields)
    return result, end


def initialize_tensors(runner, config, kernel_block_sizes, is_profiling):
    initialize = runner.initialize_kv_cache_tensors
    if is_profiling or not trace_enabled():
        return initialize(config, kernel_block_sizes)
    rank = runner._sllm_v11_rank
    caches, end = timed_call("b1_kv_tensor_init", initialize, {"rank": rank},
                             config, kernel_block_sizes)
    cross_layers = getattr(runner, "cross_layers_kv_cache", None)
    tensors = list(caches.values())
    if cross_layers is not None:
        tensors.append(cross_layers)
    allocated = storage_bytes(tensors)
    record("b1_kv_initialized_storage", rank=rank, initialized_ns=end,
           runner_kv_storage_bytes=allocated)
    return caches


def release_kv_references(runner):
    cleanup = runner._cleanup_profiling_kv_cache
    if not trace_enabled() or not hasattr(runner, "_sllm_v11_rank"):
        return cleanup()
    tensors = list(getattr(runner, "kv_caches", ()))
    cross_layers = getattr(runner, "cross_layers_kv_cache", None)
    if cross_layers is not None:
        tensors.append(cross_layers)
    storage_before = storage_bytes(t for t in tensors if t is not None)
    del tensors, cross_layers
    timed_call("b1_kv_reference_cleanup", cleanup,
               {"rank": runner._sllm_v11_rank,
                "runner_kv_storage_bytes_before": storage_before,
                "physical_release_begin_ns": None, "physical_release_end_ns": None,
                "physical_release_bytes": None})


def register_transfer(adapter, rpc, *args, **kwargs):
    if not trace_enabled():
        return rpc(*args, **kwargs)
    attempt = getattr(adapter, "_sllm_v11_register_attempt", 0) + 1
    adapter._sllm_v11_register_attempt = attempt
    fields = {"rank": adapter.parallel_strategy.vllm_worker_id,
              "instance_id": adapter.instance_id, "attempt": attempt,
              "initial_registration": attempt == 1}
    result, _ = timed_call("b1_lmcache_register_transfer", rpc, fields, *args, **kwargs)
    return result


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"expected exactly one source anchor: {old!r}")
    return source.replace(old, new)


def patch_sources(sources):
    worker = replace(
        sources[WORKER],
        "            self.model_runner.load_model(load_dummy_weights=load_dummy_weights)\n",
        "            self.model_runner.load_model(load_dummy_weights=load_dummy_weights)\n"
        "        if not load_dummy_weights:\n"
        f"            from {MODULE} import weight_ready as _v11_weight_ready\n\n"
        "            _v11_weight_ready(self)\n",
    )
    worker = replace(
        worker,
        "            self.model_runner._init_kv_zero_meta()\n"
        "\n"
        '    @instrument(span_name="Warmup (GPU)")\n',
        "            self.model_runner._init_kv_zero_meta()\n"
        f"        from {MODULE} import kv_worker_ready as _v11_kv_worker_ready\n\n"
        "        _v11_kv_worker_ready(self)\n\n"
        '    @instrument(span_name="Warmup (GPU)")\n',
    )
    worker = replace(
        worker,
        '        """Allocate GPU KV cache with the specified kv_cache_config."""\n',
        '        """Allocate GPU KV cache with the specified kv_cache_config."""\n'
        f"        from {MODULE} import kv_budget as _v11_kv_budget\n\n"
        "        _v11_kv_budget(self, kv_cache_config)\n",
    )
    runner = replace(
        sources[RUNNER],
        "        kv_caches = self.initialize_kv_cache_tensors(\n"
        "            kv_cache_config, kernel_block_sizes\n"
        "        )\n",
        f"        from {MODULE} import initialize_tensors as _v11_initialize_tensors\n\n"
        "        kv_caches = _v11_initialize_tensors(\n"
        "            self, kv_cache_config, kernel_block_sizes, is_profiling\n"
        "        )\n",
    )
    runner = replace(
        runner,
        "        # Calls torch.accelerator.synchronize()\n"
        "        self._cleanup_profiling_kv_cache()\n"
        "        self.compilation_config.static_forward_context.clear()\n",
        f"        from {MODULE} import release_kv_references as _v11_release_kv_references\n\n"
        "        _v11_release_kv_references(self)\n"
        "        self.compilation_config.static_forward_context.clear()\n",
    )
    adapter = replace(
        sources[ADAPTER],
        "            transfer_ctx.register(\n",
        f"            from {MODULE} import register_transfer as _v11_register_transfer\n\n"
        "            _v11_register_transfer(self, transfer_ctx.register,\n",
    )
    return {WORKER: worker, RUNNER: runner, ADAPTER: adapter}


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare(source_dir, pins_file, dest):
    bases = {name: (source_dir / name).read_bytes() for name in (WORKER, RUNNER, ADAPTER)}
    hashes = {name: sha256(raw) for name, raw in bases.items()}
    if hashes != json.loads(pins_file.read_text(encoding="utf-8")):
        raise ValueError("worker overlay inputs differ from the supplied SHA-256 pins")
    patched = {name: source.encode() for name, source in patch_sources(
        {name: raw.decode() for name, raw in bases.items()}).items()}
    patched[HELPER] = Path(__file__).read_bytes()
    for name, raw in patched.items():
        compile(raw, name, "exec")
    dest.mkdir()
    for name, raw in patched.items():
        (dest / name).write_bytes(raw)
    (dest / "worker-trace-manifest.json").write_text(json.dumps(
        {"source_sha256": hashes, "overlay_sha256": {
            name: sha256(raw) for name, raw in patched.items()}}, indent=2) + "\n",
        encoding="utf-8")
    print(dest)


def self_test():
    import sys
    from types import SimpleNamespace
    from unittest.mock import patch

    import v11_hbm_receipt

    def tensor(address, size, device="cuda:0"):
        return SimpleNamespace(is_cuda=True, device=device, untyped_storage=lambda:
                               SimpleNamespace(data_ptr=lambda: address, nbytes=lambda: size))

    rows = []
    mod = __name__
    with (patch.dict(os.environ, {"SLLM_EVENT_DIR": "/tmp/mock-b1-events"}),
          patch.dict(sys.modules, {"sllm_store.v11_hbm_receipt": v11_hbm_receipt}),
          patch(f"{mod}.record", side_effect=lambda name, **values: rows.append((name, values)))):
        for rank in range(8):
            cache = {"layer_a": tensor(1000, 64), "layer_b": tensor(1000, 64)}
            model = SimpleNamespace(parameters=lambda: [tensor(2000, 64), tensor(2000, 64)],
                                    buffers=lambda: [tensor(2032, 64), tensor(2000, 64, "cuda:1")])
            runner = SimpleNamespace(
                get_model=lambda: model, initialize_kv_cache_tensors=lambda *_: cache,
                cross_layers_kv_cache=tensor(1000, 64), kv_caches=list(cache.values()))
            runner._cleanup_profiling_kv_cache = runner.kv_caches.clear
            worker = SimpleNamespace(rank=rank, model_runner=runner, cache_config=SimpleNamespace(
                kv_cache_memory_bytes=None, gpu_memory_utilization=0.90))
            config = SimpleNamespace(num_blocks=1, kv_cache_tensors=[SimpleNamespace(size=64)])
            adapter = SimpleNamespace(parallel_strategy=SimpleNamespace(vllm_worker_id=rank),
                                      instance_id=rank + 100)
            before = len(rows)
            assert initialize_tensors(runner, None, None, True) is cache
            assert len(rows) == before
            weight_ready(worker)
            kv_budget(worker, config)
            assert initialize_tensors(runner, None, None, False) is cache
            assert register_transfer(adapter, lambda: rank) == rank
            try:
                register_transfer(adapter, lambda: int("failed"))
            except ValueError:
                pass
            else:
                raise AssertionError("registration errors must propagate")
            kv_worker_ready(worker)
            release_kv_references(runner)
            assert not runner.kv_caches
        for name in {phase for phase, _ in rows}:
            assert {r["rank"] for phase, r in rows if phase == name} == set(range(8))
        for phase, values in rows:
            if "begin_ns" in values:
                assert values["begin_ns"] <= values["end_ns"]
            if phase == "b1_lmcache_register_transfer":
                assert values["initial_registration"] == (values["attempt"] == 1)
                assert values["status"] == ("ok" if values["attempt"] == 1 else "failed")
            if phase == "b1_kv_initialized_storage":
                assert values["runner_kv_storage_bytes"] == 64
            if phase == "b1_weight_ready_storage":
                assert values["parameter_storage_bytes"] == 64
                assert values["buffer_storage_bytes"] == 128
                assert values["parameter_and_buffer_storage_bytes"] == 160
            if phase == "b1_kv_budget":
                assert values["requested_bytes"] is None and values["layout_bytes"] == 64
            if phase == "b1_kv_reference_cleanup":
                assert values["runner_kv_storage_bytes_before"] == 64
                assert values["physical_release_bytes"] is None
    print(f"validated {len(rows)} mocked events across 8 ranks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("source_dir", type=Path, nargs="?")
    parser.add_argument("pins_file", type=Path, nargs="?")
    parser.add_argument("destination", type=Path, nargs="?")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        if not all((args.source_dir, args.pins_file, args.destination)):
            parser.error("provide source_dir, pins_file and destination")
        prepare(args.source_dir, args.pins_file, args.destination)
