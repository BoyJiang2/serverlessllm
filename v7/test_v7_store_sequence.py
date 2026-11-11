"""CPU tests of the real full-cohort evidence gate, not a model preexperiment."""
import ast
import collections
import json
from pathlib import Path
import re
import tempfile
import types
import unittest

source = Path(__file__).resolve().parents[3] / "benchmarks/v7_store_sequence.py"
node = next(n for n in ast.parse(source.read_text()).body
            if isinstance(n, ast.FunctionDef) and n.name == "coverage")


class CoverageTest(unittest.TestCase):
    def fixture(self):
        events = []
        for rank in range(8):
            for index, suffix in enumerate(("begin", "end")):
                events.append(dict(name="full_weight_load_" + suffix, rank=rank,
                                   pid=99999999+rank, process_start_ticks=1,
                                   boot_id="test", time_namespace="test", clock_id="CLOCK_MONOTONIC",
                                   monotonic_ns=1000+rank*10+index*5, sequence=index))
        return events

    def check(self, events, confirms=None):
        namespace = dict(collections=collections, json=json, pathlib=__import__("pathlib"),
                         re=re, P=dict(model_paths=dict(kimi=[f"rank_{i}" for i in range(8)])))
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "events").mkdir()
            (output / "events/trace.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
            log = output / "native.log"
            paths = confirms if confirms is not None else [f"rank_{i}" for i in range(8)]
            log.write_text("".join(f"Confirm model {p} replica id success\n" for p in paths))
            return namespace["coverage"]("kimi", output, log)

    def test_complete_eight_rank_endpoints(self):
        result = self.check(self.fixture())
        self.assertEqual(result["gpu_confirmed_paths"], 8)
        self.assertEqual(len(result["rank_load_spans"]), 8)
        self.assertAlmostEqual(result["cohort_host_span_seconds"], 75/1e9)

    def test_missing_rank_cannot_be_inferred(self):
        with self.assertRaises(AssertionError):
            self.check([e for e in self.fixture() if e["rank"] != 7])

    def test_duplicate_endpoint_fails(self):
        events = self.fixture()
        events.append(dict(events[-1]))
        with self.assertRaises(AssertionError):
            self.check(events)

    def test_native_confirm_set_must_match(self):
        with self.assertRaises(AssertionError):
            self.check(self.fixture(), ["rank_0"] * 8)

    def test_distinct_rank_clock_domains_cannot_be_combined(self):
        events = self.fixture()
        for event in events:
            if event["rank"] == 7:
                event["time_namespace"] = "other"
        with self.assertRaises(AssertionError):
            self.check(events)

    def test_store_only_pid_does_not_hide_retained_model_memory(self):
        idle = next(n for n in ast.parse(source.read_text()).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'idle')
        memory = [2387]*8
        ticks = iter(range(0, 200*10**9, 10**9))
        written = {}
        namespace = dict(store_gpu_baseline=None, store_pid=100, P={'store_idle_gpu_tolerance_mib': 256},
                         O=Path('/output'), time=types.SimpleNamespace(sleep=lambda _: None),
                         store_identity=lambda: dict(ns=next(ticks)),
                         write=lambda p, value: written.update({p.name: value}),
                         call=lambda *args: '\n'.join(f'{i}, {n}' for i, n in enumerate(memory))
                         if '--query-gpu=index,memory.used' in args else '100, 2387')
        exec(compile(ast.Module(body=[idle], type_ignores=[]), str(source), 'exec'), namespace)
        namespace['idle'](Path('/output/empty.json'))
        self.assertEqual(namespace['store_gpu_baseline'], [2387]*8)
        memory[0] = 100000
        with self.assertRaisesRegex(AssertionError, 'GPU memory'):
            namespace['idle'](Path('/output/leaked.json'))
        self.assertNotIn('leaked.json', written)


if __name__ == "__main__":
    unittest.main()
