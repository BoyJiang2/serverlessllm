"""Historical GLM replay and synthetic rejection tests; no runtime imports."""
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import types
import unittest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/v4-evidence"
spec = importlib.util.spec_from_file_location("v4_evidence", ROOT / "benchmarks/v4_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def read(name):
    return json.loads((FIXTURES / name).read_text())


def response(tokens, text="4", finish="stop", stop=None):
    return {"choices": [{"token_ids": tokens, "message": {"content": text},
                         "finish_reason": finish, "stop_reason": stop}]}


class EvidenceContracts(unittest.TestCase):
    def setUp(self):
        self.record = read("glm-result.json")
        self.phases = read("glm-phases.json")
        self.events = self.phases["raw_events"]
        self.record["business_request_id"] = self.phases["request_id"]
        self.idle = [json.loads(line) for line in (FIXTURES / "glm-idle60.jsonl").read_text().splitlines()]

    def test_fixture_bytes_match_frozen_source_manifest(self):
        for row in read("manifest.json")["files"]:
            self.assertEqual(hashlib.sha256((FIXTURES / row["fixture"]).read_bytes()).hexdigest(), row["sha256"])

    def test_replay_all_recorded_glm_metrics_from_historical_events(self):
        metrics = evidence.timing_metrics(self.record, self.events, True)
        metrics.update(evidence.phase_metrics(self.events, self.record["startup_command_ns"]))
        self.assertEqual(set(metrics), set(self.record["metrics"]))
        for name, value in metrics.items():
            self.assertAlmostEqual(value, self.record["metrics"][name], places=9, msg=name)

    def test_missing_backend_event_does_not_become_http_first_token(self):
        rows = [e for e in self.events if e["name"] != "backend_first_token_observed"]
        with self.assertRaisesRegex(ValueError, "without backend"):
            evidence.timing_metrics(self.record, rows, True)
        self.record.pop("first_token_observed_ns")
        values = evidence.timing_metrics(self.record, rows, True)
        self.assertNotIn("command_to_first_token_s", values)
        self.assertIn("command_to_correct_business_response_s", values)

    def test_failed_business_cannot_retain_correct_response_event(self):
        with self.assertRaisesRegex(ValueError, "complete business"):
            evidence.timing_metrics(self.record, self.events, False)

    def test_preparation_and_registration_failures_need_no_fake_response_time(self):
        record = {"preparation_begin_ns": 1, "preparation_complete_ns": 3}
        self.assertEqual(evidence.timing_metrics(record, [], False), {"preparation_s": 2e-9})
        record["startup_command_ns"] = 4
        self.assertEqual(evidence.timing_metrics(record, [], False), {"preparation_s": 2e-9})

    def test_duplicate_first_token_or_time_reversal_rejected(self):
        first = next(e for e in self.events if e["name"] == "backend_first_token_observed")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evidence.timing_metrics(self.record, self.events + [first], True)
        self.record["business_trigger_ns"] = self.record["correct_business_response_ns"] + 1
        with self.assertRaises(ValueError):
            evidence.timing_metrics(self.record, self.events, True)

    def test_missing_rank_and_duplicate_phase_rejected(self):
        row = next(e for e in self.events if e["name"] == "weight_ingestion_begin")
        for rows in ([e for e in self.events if e is not row], self.events + [row]):
            with self.assertRaisesRegex(ValueError, "rank phase"):
                evidence.phase_metrics(rows, self.record["startup_command_ns"])

    def test_phase_envelope_is_not_sum_of_rank_or_nested_durations(self):
        metrics = evidence.phase_metrics(self.events, self.record["startup_command_ns"])
        phase = self.phases["phases"]["weight_ingestion"]
        total = sum(r["host_seconds"] for r in phase["ranks"].values())
        self.assertNotEqual(metrics["weight_ingestion_span_s"], total)
        self.assertEqual(metrics["weights_pipeline_span_s"],
                         metrics["phase_weights_pipeline_including_allocation_span_s"])

    def test_business_semantics_eos_and_native_parity_remain_separate(self):
        native = response([5, 1])
        checks = evidence.response_checks(response([6, 1], " 4\n"), native, "business")
        self.assertTrue(checks["complete_business_success"])
        self.assertFalse(checks["native_token_parity"])
        wrong = response([5, 1], "wrong")
        checks = evidence.response_checks(wrong, wrong, "business")
        self.assertTrue(checks["native_token_parity"])
        self.assertFalse(checks["complete_business_success"])

    def test_fixed32_prefix_is_never_complete_business_success(self):
        fixed = response(list(range(2, 34)), "4", "length")
        checks = evidence.response_checks(fixed, fixed, "workload32")
        self.assertTrue(checks["fixed32_length_success"])
        self.assertNotIn("complete_business_success", checks)
        self.assertFalse(evidence.response_checks(fixed, fixed, "business")["complete_business_success"])

    def test_actual_pinned_upstream_eos_can_have_no_stop_reason(self):
        tree = ast.parse((FIXTURES / "vllm-check-stop.py.txt").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_stop")
        module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), function], type_ignores=[])
        namespace = {"RequestStatus": types.SimpleNamespace(FINISHED_STOPPED="stop")}
        exec(compile(ast.fix_missing_locations(module), "pinned-upstream-check-stop", "exec"), namespace)
        request = types.SimpleNamespace(pooling_params=None, num_output_tokens=2, output_token_ids=[5, 1],
            stop_reason=None, sampling_params=types.SimpleNamespace(min_tokens=0, eos_token_id=1))
        self.assertTrue(namespace["check_stop"](request, 2048))
        self.assertIsNone(request.stop_reason)
        actual = response(request.output_token_ids, finish=request.status, stop=request.stop_reason)
        self.assertTrue(evidence.response_checks(actual, actual, "business")["natural_eos_observed"])

    def test_actual_pinned_detokenizer_retains_eos_id_while_skipping_its_text(self):
        tree = ast.parse((FIXTURES / "vllm-detokenizer.py.txt").read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BaseIncrementalDetokenizer")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "update")
        namespace = {}
        exec(compile(ast.Module(body=[method], type_ignores=[]), "pinned-detokenizer-update", "exec"), namespace)
        state = types.SimpleNamespace(include_stop_str_in_output=False, output_text="", token_ids=[],
            min_tokens=0, stop=[], decode_next=lambda token: "4")
        namespace["update"](state, [5, 1], True)
        self.assertEqual(state.token_ids, [5, 1])
        self.assertEqual(state.output_text, "4")

    def test_stop_reason_alone_and_length_termination_do_not_prove_eos(self):
        for item in (response([5], stop=1), response([5, 1], finish="length"), response([5, 1], stop=True)):
            self.assertFalse(evidence.response_checks(item, item, "business")["natural_eos_observed"])

    def test_real_glm_release_window_replays_without_claiming_physical_release(self):
        value = evidence.release_coverage(self.idle, self.record["delete_complete_ns"])
        self.assertGreaterEqual(value["sample_span_seconds"], 60)
        self.assertIn("not validated", value["scope"])
        self.assertNotIn("idle_observation_seconds", value)

    def test_release_under_60_seconds_and_missing_sample_rejected(self):
        with self.assertRaises(ValueError):
            evidence.release_coverage(self.idle[:-1], self.record["delete_complete_ns"])
        rows = copy.deepcopy(self.idle)
        rows[-1]["monotonic_ns"] = rows[0]["monotonic_ns"] + 59_999_999_999
        with self.assertRaisesRegex(ValueError, "shorter"):
            evidence.release_coverage(rows, self.record["delete_complete_ns"])

    def test_release_gap_and_stale_gpu_snapshot_rejected(self):
        rows = copy.deepcopy(self.idle)
        rows[20]["monotonic_ns"] = rows[19]["monotonic_ns"] + 6_000_000_000
        with self.assertRaisesRegex(ValueError, "gap"):
            evidence.release_coverage(rows, self.record["delete_complete_ns"])
        rows = copy.deepcopy(self.idle)
        rows[0]["host_gpu_snapshot"]["sample_begin_ns"] = self.record["delete_complete_ns"] - 1
        with self.assertRaisesRegex(ValueError, "post-delete"):
            evidence.release_coverage(rows, self.record["delete_complete_ns"])


if __name__ == "__main__":
    unittest.main()
