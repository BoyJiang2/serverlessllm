"""Journal replay/export contracts; all new observations are CPU fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import test_v4_attempts as fixture
import v4_results as results


class ResultReplayContracts(unittest.TestCase):
    def setUp(self):
        self.case = fixture.AttemptJournalContracts()
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.root = self.case.root

    def replay(self):
        return results.replay(fixture.PROTOCOL, fixture.SHA, [self.root])

    def full(self):
        self.case.test_complete_synthetic_evidence_still_cannot_award_runtime_pass()

    def fail(self, after_startup=False, timeout=False):
        journal = self.case.start()
        if after_startup:
            self.case.startup(journal)
        self.case.now = journal.record["attempt_deadline_ns"] if timeout else 200
        journal.fail(TimeoutError("fixture timeout") if timeout else ValueError("fixture prepare"))
        return journal

    def mutate(self, stage, change):
        previous = None
        for path in sorted((self.root / fixture.ID).iterdir()):
            row = json.loads(path.read_bytes())
            if row["stage"] == stage:
                change(row)
            row["previous_sha256"] = previous
            raw = (json.dumps(row, sort_keys=True) + '\n').encode()
            path.write_bytes(raw)
            previous = results.digest(raw)

    def test_complete_flat_record_recomputes_all_glm_metric_fields(self):
        self.full()
        records, summary, entries = self.replay()
        record = records[0]
        business = entries[0]["journal"][3]["data"]
        self.assertEqual(record["metrics"], business["metrics"])
        self.assertEqual(record["correct_business_response_ns"], 200)
        self.assertEqual(record["first_token_observed_ns"], 196)
        self.assertEqual(record["delete_complete_ns"], 220)
        self.assertEqual(record["code_commit"], fixture.SOURCE["code_commit"])
        self.assertFalse(record["success"])
        self.assertFalse(record["runtime_qualified"])
        self.assertEqual(record["missing_evidence"], [])
        counts = summary["conditions"][record["condition"]]
        self.assertEqual((counts["preregistered"], counts["started"], counts["unstarted"]), (3, 1, 2))
        self.assertEqual(counts["evidence_complete_unqualified"], 1)
        self.assertEqual(counts["formal_successes"], 0)
        self.assertIsNone(counts["formal_success_rate"])

    def test_prepare_failure_is_in_started_denominator_without_response_latency(self):
        self.fail()
        records, summary, _ = self.replay()
        record = records[0]
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["metrics"], {})
        self.assertNotIn("correct_business_response_ns", record)
        self.assertIsNone(record["startup_censor_elapsed_seconds"])
        self.assertEqual(summary["conditions"][record["condition"]]["failed_after_stage"], {"origin": 1})

    def test_timeout_censor_is_not_a_completed_business_latency(self):
        self.fail(after_startup=True, timeout=True)
        record = self.replay()[0][0]
        self.assertTrue(record["right_censored"])
        self.assertNotIn("command_to_correct_business_response_s", record["metrics"])
        self.assertNotIn("correct_business_response_ns", record)
        self.assertGreater(record["startup_censor_elapsed_seconds"], 0)

    def test_late_failure_after_business_keeps_observed_latency_not_censoring(self):
        journal = self.case.start()
        self.case.through_business(journal)
        self.case.now = journal.record["attempt_deadline_ns"]
        journal.fail(TimeoutError("release incomplete"))
        record = self.replay()[0][0]
        self.assertEqual(record["status"], "timeout")
        self.assertFalse(record["right_censored"])
        self.assertEqual(record["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertNotIn("evidence_complete_ns", record)

    def trace_failure(self, native_tokens=None):
        journal = self.case.start()
        self.case.startup(journal)
        self.case.now = 180
        journal.trigger(175, "cpu-business")
        self.case.now = 200
        response = {"choices": [{"token_ids": [1], "message": {"content": "4"}, "finish_reason": "stop"}]}
        native = json.loads(json.dumps(response))
        if native_tokens:
            native["choices"][0]["token_ids"] = native_tokens
        try:
            with journal:
                journal.business(response, native, self.root / "missing-events")
        except ValueError:
            pass
        return journal

    def test_new_trace_failure_retains_http_proof_but_no_fake_first_token(self):
        self.trace_failure()
        record = self.replay()[0][0]
        self.assertTrue(record["correct_business_response_observed"])
        self.assertEqual(record["correct_business_response_ns"], 200)
        self.assertEqual(record["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertNotIn("first_token_observed_ns", record)
        self.assertIn("business_events", record["missing_evidence"])

    def test_legacy_trace_failure_marks_unrecoverable_observation_unknown(self):
        self.trace_failure()
        self.mutate("result", lambda r: (r["data"].pop("record"), r["data"].pop("business_observation")))
        records, summary, _ = self.replay()
        record = records[0]
        self.assertIsNone(record["correct_business_response_observed"])
        self.assertIsNone(record["right_censored"])
        self.assertNotIn("correct_business_response_ns", record)
        self.assertNotIn("command_to_correct_business_response_s", record["metrics"])
        self.assertEqual(summary["conditions"][record["condition"]]["unknown_business_observations"], 1)

    def failed_event_gap(self, missing):
        journal = self.case.start()
        self.case.startup(journal)
        self.case.now = 180
        journal.trigger(175, "cpu-business")
        directory = self.case.business_events(journal)
        path = directory / "process-cpu-events.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if missing == "first":
            rows = [r for r in rows if r["name"] != "backend_first_token_observed"]
        else:
            rows = [r for r in rows if not (r.get("rank") == 7
                    and r["name"] == "weight_ingestion_end")]
        path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        self.case.now = 200
        response = {"choices": [{"token_ids": [1], "message": {"content": "4"}, "finish_reason": "stop"}]}
        try:
            with journal:
                journal.business(response, response, directory)
        except ValueError:
            pass
        return self.replay()[0][0]

    def test_failed_missing_first_token_retains_correct_response_latency(self):
        record = self.failed_event_gap("first")
        self.assertIn("business_first_token", record["missing_evidence"])
        self.assertEqual(record["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertNotIn("command_to_first_token_s", record["metrics"])
        self.assertEqual(record["status"], "failed")

    def test_failed_partial_rank_phases_keep_response_and_first_token_only(self):
        record = self.failed_event_gap("rank")
        self.assertTrue(any(v.startswith("startup_phases:") for v in record["missing_evidence"]))
        self.assertEqual(record["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertEqual(record["metrics"]["command_to_first_token_s"], 26 / 1e9)
        self.assertNotIn("phase_weight_ingestion_span_s", record["metrics"])
        self.assertFalse(record["success"])

    def test_native_parity_failure_does_not_erase_correct_business_response(self):
        self.trace_failure([2])
        record = self.replay()[0][0]
        self.assertTrue(record["component_checks"]["business"]["complete_business_success"])
        self.assertFalse(record["component_checks"]["business"]["native_token_parity"])
        self.assertTrue(record["correct_business_response_observed"])
        self.assertEqual(record["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertFalse(record["success"])

    def test_empty_claim_remains_consumed_incomplete_and_unstarted_stays_separate(self):
        (self.root / fixture.ID).mkdir()
        records, summary, _ = self.replay()
        self.assertEqual(records[0]["status"], "incomplete")
        self.assertEqual(records[0]["missing_evidence"], ["origin"])
        self.assertTrue(records[0]["consumed"])
        self.assertFalse(records[1]["consumed"])
        self.assertEqual(sum(c["started"] for c in summary["conditions"].values()), 1)
        self.assertEqual(sum(c["unstarted"] for c in summary["conditions"].values()), 11)

    def test_recomputed_metric_mismatch_is_rejected_even_with_consistent_hash_chain(self):
        self.full()
        self.mutate("business", lambda r: r["data"]["metrics"].update(command_to_first_token_s=999))
        with self.assertRaisesRegex(ValueError, "replay"):
            self.replay()

    def test_mixed_clock_or_out_of_window_business_events_are_rejected(self):
        self.full()
        self.mutate("business", lambda r: r["data"]["events"][0].update(boot_id="wrong"))
        with self.assertRaisesRegex(ValueError, "clock"):
            self.replay()

    def test_out_of_window_event_is_rejected(self):
        self.full()
        self.mutate("business", lambda r: r["data"]["events"][0].update(monotonic_ns=201))
        with self.assertRaisesRegex(ValueError, "out-of-window"):
            self.replay()

    def test_failure_snapshot_cannot_replace_original_deadline(self):
        self.fail()
        self.mutate("result", lambda r: r["data"]["record"].update(attempt_deadline_ns=10**30))
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.replay()

    def test_forged_censor_or_early_completion_are_rejected(self):
        self.fail(after_startup=True, timeout=True)
        self.mutate("result", lambda r: r["data"].update(right_censored=False))
        with self.assertRaisesRegex(ValueError, "censor"):
            self.replay()

    def test_fixed32_checks_are_recomputed(self):
        self.full()
        self.mutate("workload32", lambda r: r["data"]["response"]["choices"][0].update(token_ids=[1]))
        with self.assertRaisesRegex(ValueError, "fixed32"):
            self.replay()

    def test_export_creates_derived_result_files_with_input_and_output_hashes(self):
        self.full()
        with tempfile.TemporaryDirectory() as parent:
            out = Path(parent) / "export"
            results.export_results(fixture.PROTOCOL, fixture.SHA, [self.root], out)
            self.assertEqual(len(list(out.glob('*/result.json'))), 12)
            manifest = json.loads((out / "manifest.json").read_bytes())
            self.assertEqual(manifest["artifact_kind"], "derived_not_execution")
            for row in manifest["output_files"]:
                self.assertEqual(results.digest((out / row["path"]).read_bytes()), row["sha256"])
            before = (out / "summary.json").read_bytes()
            with self.assertRaises(FileExistsError):
                results.export_results(fixture.PROTOCOL, fixture.SHA, [self.root], out)
            self.assertEqual((out / "summary.json").read_bytes(), before)

    def test_export_cannot_pollute_execution_roots(self):
        self.fail()
        with self.assertRaisesRegex(ValueError, "outside"):
            results.export_results(fixture.PROTOCOL, fixture.SHA, [self.root], self.root / "derived")
        self.assertFalse((self.root / "derived").exists())

    def test_changed_input_during_replay_does_not_create_output(self):
        self.fail()
        original = results.replay
        def changing(*args):
            value = original(*args)
            p = self.root / fixture.ID / "00-origin.json"
            p.write_bytes(p.read_bytes() + b" ")
            return value
        with tempfile.TemporaryDirectory() as parent:
            out = Path(parent) / "export"
            with mock.patch.object(results, "replay", side_effect=changing):
                with self.assertRaisesRegex(ValueError, "changed during"):
                    results.export_results(fixture.PROTOCOL, fixture.SHA, [self.root], out)
            self.assertFalse(out.exists())

    def test_cli_exports_only_cpu_derived_data(self):
        self.full()
        with tempfile.TemporaryDirectory() as parent:
            out = Path(parent) / "derived"
            command = [sys.executable, str(fixture.ROOT / "benchmarks/v4_results.py"),
                       "--preregistration", str(fixture.PROTOCOL), "--prereg-sha256", fixture.SHA,
                       "--execution-root", str(self.root), "--output", str(out)]
            run = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30,
                                 env=dict(os.environ, PYTHONPATH=str(fixture.ROOT / "sllm_store")))
            self.assertFalse(json.loads(run.stdout)["runtime_qualified"])
            record = json.loads((out / fixture.ID / "result.json").read_bytes())
            self.assertFalse(record["success"])
            self.assertEqual(record["status"], "evidence_complete_unqualified")

    def test_unqualified_completion_must_still_respect_attempt_deadline(self):
        self.full()
        def change(row):
            row["data"]["completed_ns"] = 10**30
        self.mutate("result", change)
        with self.assertRaisesRegex(ValueError, "completion"):
            self.replay()

    def test_archived_prior_cpu_journal_replays_without_upgrading_qualification(self):
        base = fixture.ROOT / 'docs/evidence/v4-attempts-p3/cpu-attempt-check-20261106'
        protocol = base / 'preregistration-cpu.json'
        records, _, _ = results.replay(protocol, results.digest(protocol.read_bytes()), [base / 'execution'])
        self.assertEqual(records[0]["status"], "evidence_complete_unqualified")
        self.assertFalse(records[0]["success"])
        self.assertEqual(records[0]["metrics"]["command_to_correct_business_response_s"], 30 / 1e9)


if __name__ == "__main__":
    unittest.main()
