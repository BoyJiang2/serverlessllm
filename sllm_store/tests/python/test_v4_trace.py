"""Real recorder/process clocks and actual application AST; no inference runtime."""
import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from sllm_store import event_trace as trace
from sllm_store.v4_trace import install_v4_trace_hooks
import sllm_store


@contextmanager
def fake_hook_runtime(rank):
    gpu, base = types.ModuleType("gpu_worker"), types.ModuleType("base_loader")
    class Default:
        def load_weights(self, *args):
            return "weights"
    class Store:
        def load_weights(self, *args):
            return "restored"
    class Grouped:
        def load_weights(self, *args):
            return "remainder"
    class Worker:
        def __init__(self):
            self.rank = rank
        def init_device(self):
            gpu.init_worker_distributed_environment(None, self.rank)
        def load_model(self):
            Default().load_weights(None, None)
            base.process_weights_after_loading(None, None, None)
        def initialize_from_config(self):
            return "kv"
        def compile_or_warm_up_model(self):
            return "warm"
    gpu.Worker = Worker
    gpu.init_worker_distributed_environment = lambda config, rank: None
    base.process_weights_after_loading = lambda *args: None
    loader = types.SimpleNamespace(DefaultModelLoader=Default, V4StoreLoader=Store,
        V4GroupedStoreLoader=Grouped, check_engine_source=mock.Mock())
    modules = {
        "sllm_store.vllm_v4_loader": loader,
        "vllm": types.ModuleType("vllm"),
        "vllm.distributed": types.SimpleNamespace(get_tensor_model_parallel_rank=lambda: rank),
        "vllm.model_executor.model_loader": types.SimpleNamespace(base_loader=base),
        "vllm.v1.worker": types.SimpleNamespace(gpu_worker=gpu),
    }
    with mock.patch.dict(sys.modules, modules), mock.patch.object(sllm_store, "vllm_v4_loader", loader, create=True):
        yield gpu, base, loader


def cpu_worker(rank):
    with fake_hook_runtime(rank) as (gpu, base, loader):
        install_v4_trace_hooks()
        instance = gpu.Worker()
        instance.init_device()
        instance.load_model()
        instance.initialize_from_config()
        instance.compile_or_warm_up_model()


class RecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        patch = mock.patch.dict(os.environ, SLLM_EVENT_DIR=str(self.path), SLLM_EVENT_RUN_ID="cpu-trace")
        patch.start()
        self.addCleanup(patch.stop)
        trace._reset_process()

    def rows(self):
        return [json.loads(line) for p in self.path.glob("*.jsonl") for line in p.read_text().splitlines()]

    def window(self, **kwargs):
        clock = trace.process_identity()
        return trace.read_event_window(self.path, "cpu-trace", 0, time.monotonic_ns(),
            boot_id=kwargs.get("boot_id", clock["boot_id"]),
            time_namespace=kwargs.get("time_namespace", clock["time_namespace"]))

    def test_disabled_tracing_does_not_access_proc_or_files(self):
        with mock.patch.dict(os.environ, SLLM_EVENT_DIR=""), mock.patch.object(trace, "process_identity", side_effect=AssertionError):
            self.assertIsNone(trace.record_event("disabled"))
        self.assertFalse(list(self.path.iterdir()))

    def test_identity_fields_and_backend_creation_clock_match_the_glm_boundary(self):
        trace.record_event("backend_engine_create_begin")
        row = self.window()[0]
        self.assertEqual(row["pid"], os.getpid())
        self.assertEqual(row["run_id"], "cpu-trace")
        self.assertGreater(row["process_start_ticks"], 0)
        self.assertIn("boottime_minus_monotonic_ns", row)
        self.assertEqual(row["clock_id"], "CLOCK_MONOTONIC")

    def test_threaded_partial_writes_still_produce_complete_json_rows(self):
        original = os.write
        def partial(descriptor, data):
            return original(descriptor, data[:11])
        with mock.patch.object(trace.os, "write", side_effect=partial):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda i: trace.record_event("thread_event", index=i), range(40)))
        rows = self.rows()
        self.assertEqual(sorted(r["index"] for r in rows), list(range(40)))
        self.assertEqual([r["sequence"] for r in rows], list(range(40)))

    def test_run_clock_or_corrupt_data_cannot_be_read_as_a_valid_window(self):
        trace.record_event("valid")
        with self.assertRaisesRegex(ValueError, "clock domain"):
            self.window(boot_id="another-boot")
        with self.assertRaisesRegex(ValueError, "clock domain"):
            self.window(time_namespace="another-clock")
        path = next(self.path.glob("*.jsonl"))
        with path.open("a") as file:
            file.write("truncated\n")
        with self.assertRaises(json.JSONDecodeError):
            self.window()

    def test_unbound_runs_and_identity_override_are_rejected(self):
        with mock.patch.dict(os.environ, SLLM_EVENT_RUN_ID=""):
            with self.assertRaises(ValueError):
                trace.record_event("unbound")
        with self.assertRaisesRegex(ValueError, "replace"):
            trace.record_event("tampered", monotonic_ns=0)
        with self.assertRaises(TypeError):
            trace.record_event("not_json", tensor=object())
        self.assertFalse(self.rows())

    def test_reused_pid_or_namespace_cannot_share_one_legacy_process_key(self):
        trace.record_event("process_event")
        path = next(self.path.glob("*.jsonl"))
        row = self.rows()[0]
        row["pid_namespace"] = "another-pid-namespace"
        with path.open("a") as file:
            file.write(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.window()

    def test_reused_glm_trace_source_has_the_verified_reference_hash(self):
        path = Path(__file__).resolve().parents[1] / "fixtures/glm_c23_event_trace.py.txt"
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "e1ab869bc51cbc42f46430f2296d1a9e4a48fc45aec57cf7908d88ae565c6c6c")

    def test_failed_phase_has_no_end_and_preserves_the_original_error(self):
        @trace.traced("phase")
        def failure():
            raise RuntimeError("original")
        with self.assertRaisesRegex(RuntimeError, "original"):
            failure()
        self.assertEqual([r["name"] for r in self.rows()], ["phase_begin", "phase_failed"])
        original = trace.record_event
        def broken(name, **values):
            if name.endswith("failed"):
                raise OSError("trace failed")
            return original(name, **values)
        with mock.patch.object(trace, "record_event", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "original") as caught:
                failure()
        self.assertEqual(caught.exception.sllm_trace_error, "OSError")

    def test_async_span_waits_for_awaited_work(self):
        @trace.traced("async_phase")
        async def work():
            await asyncio.sleep(0.01)
            return 7
        self.assertEqual(asyncio.run(work()), 7)
        rows = self.rows()
        self.assertEqual([r["name"] for r in rows], ["async_phase_begin", "async_phase_end"])
        self.assertGreater(rows[1]["monotonic_ns"], rows[0]["monotonic_ns"])

    def test_hooks_are_idempotent_and_require_the_source_guard(self):
        with fake_hook_runtime(3) as (gpu, base, loader):
            loader.check_engine_source.side_effect = ValueError("source changed")
            original = gpu.Worker.init_device
            with self.assertRaisesRegex(ValueError, "source changed"):
                install_v4_trace_hooks()
            self.assertIs(gpu.Worker.init_device, original)
            loader.check_engine_source.side_effect = None
            install_v4_trace_hooks()
            installed = gpu.Worker.init_device
            install_v4_trace_hooks()
            self.assertIs(gpu.Worker.init_device, installed)
            item = gpu.Worker()
            item.init_device()
            item.load_model()
            item.initialize_from_config()
            item.compile_or_warm_up_model()
        rows = self.rows()
        self.assertEqual(len(rows), 14)
        self.assertEqual({r["rank"] for r in rows}, {3})

    def test_eight_cpu_processes_supply_all_glm_phase_pairs_with_a_shared_clock(self):
        begin = time.monotonic_ns()
        trace.record_event("backend_engine_create_begin")
        for rank in range(8):
            subprocess.run([sys.executable, str(Path(__file__).resolve()), "--cpu-worker", str(rank)],
                           check=True, capture_output=True, text=True, timeout=10)
        trace.record_event("backend_engine_ready")
        trace.record_event("router_instance_published")
        rows = self.window()
        evidence = runpy.run_path(str(Path(__file__).resolve().parents[3] / "benchmarks/v4_evidence.py"))
        metrics = evidence["phase_metrics"](rows, begin)
        self.assertTrue(all(value >= 0 for value in metrics.values()))
        self.assertEqual(len({r["pid"] for r in rows if r.get("rank") is not None}), 8)
        rows.append(dict(rows[0], name="weight_ingestion_failed"))
        with self.assertRaisesRegex(ValueError, "failed startup"):
            evidence["phase_metrics"](rows, begin)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "--cpu-worker":
    cpu_worker(int(sys.argv[2]))
elif __name__ == "__main__":
    unittest.main()
