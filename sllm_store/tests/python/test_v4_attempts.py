"""CPU journal state/exception tests. Inputs and worker events are synthetic."""
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from sllm_store.event_trace import process_identity
import test_v4_cache_evidence as cache
from test_v4_cache_evidence import evidence

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "benchmarks"))
import v4_attempts as attempts

PROTOCOL = Path(__file__).resolve().parents[1] / "fixtures/v4-attempts/preregistration-cpu.json"
SHA = hashlib.sha256(PROTOCOL.read_bytes()).hexdigest()
ID = json.loads(PROTOCOL.read_bytes())["attempts"][0]["attempt_id"]
SOURCE = dict(code_commit="a" * 40, source_manifest_sha256="b" * 64)


def claim(root, barrier, queue):
    barrier.wait()
    try:
        attempts.AttemptJournal(root, ID, PROTOCOL, SHA, "cpu-run", 10**30, SOURCE, [root])
        queue.put("claimed")
    except (OSError, ValueError, KeyError):
        queue.put("refused")


class AttemptJournalContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = 100
        patch = mock.patch.object(attempts.time, "monotonic_ns", side_effect=lambda: self.now)
        patch.start()
        self.addCleanup(patch.stop)

    def start(self):
        return attempts.AttemptJournal(self.root, ID, PROTOCOL, SHA, "cpu-run", 10**15,
                                       SOURCE, [self.root])

    def collect(self):
        return attempts.collect_attempts(PROTOCOL, SHA, [self.root])

    def startup(self, journal):
        target, files, vectors = cache.scan(begin=140)
        other, other_files, other_vectors = cache.scan("safetensors_inputs", "check", 120)
        self.now = 170
        journal.startup(dict(store_validation_complete_ns=110,
                             store_registration_reused=True), target, files, vectors,
                        other, other_files, other_vectors,
                        {k: journal.identity[k] for k in ("run_id", "boot_id", "time_namespace", "clock_id")})

    def business_events(self, journal):
        directory = self.root.parent / (self.root.name + "-events")
        directory.mkdir()
        self.addCleanup(lambda: __import__('shutil').rmtree(directory))
        identity = dict(process_identity(), trace_schema=1, run_id="cpu-run", sequence=0)
        rows = []
        for rank in range(8):
            for phase in evidence.PHASES:
                for suffix, when in (("_begin", 181), ("_end", 190)):
                    rows.append(dict(identity, pid=10000 + rank, rank=rank, name=phase + suffix,
                                     monotonic_ns=when))
        for name, when in (("backend_engine_create_begin", 180), ("backend_engine_ready", 192),
                           ("router_instance_published", 194), ("backend_first_token_observed", 196),
                           ("backend_generation_complete", 198)):
            rows.append(dict(identity, name=name, monotonic_ns=when, request_id="cpu-business"))
        (directory / "process-cpu-events.jsonl").write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return directory

    def through_business(self, journal):
        self.startup(journal)
        self.now = 180
        journal.trigger(175, "cpu-business")
        self.now = 200
        response = {"choices": [{"token_ids": [1], "message": {"content": "4"},
                                  "finish_reason": "stop", "stop_reason": None}]}
        journal.business(response, response, self.business_events(journal))

    def test_malformed_business_is_durable_before_validation(self):
        bad = {"error": "invalid output", "choices": []}
        with self.assertRaises(ValueError):
            with self.start() as journal:
                self.startup(journal)
                self.now = 180
                journal.trigger(175, "cpu-business")
                self.now = 200
                journal.business(bad, bad, self.root / "unused-events")
        raw = json.loads((journal.path / "business.response").read_bytes())
        self.assertEqual(raw["observation"]["response"], bad)
        self.assertEqual(raw["observation"]["received_ns"], 200)
        self.assertEqual(self.collect()[0]["status"], "failed")

    def test_malformed_fixed32_is_durable_and_retained_on_failure(self):
        bad = {"error": "invalid fixed output", "choices": []}
        with self.assertRaises(ValueError):
            with self.start() as journal:
                self.through_business(journal)
                self.now = 210
                journal.workload32(bad, bad)
        raw = json.loads((journal.path / "workload32.response").read_bytes())
        self.assertEqual(raw["observation"]["response"], bad)
        self.assertEqual(raw["observation"]["received_ns"], 210)
        final = self.collect()[0]["journal"][-1]["data"]
        self.assertEqual(final["workload32_observation"]["response"], bad)

    def test_response_sidecar_corruption_blocks_collection(self):
        with self.assertRaises(ValueError):
            with self.start() as journal:
                self.startup(journal)
                self.now = 180
                journal.trigger(175, "cpu-business")
                self.now = 200
                journal.business({"choices": []}, {"choices": []}, self.root / "unused")
        (journal.path / "business.response").write_text("{}")
        with self.assertRaisesRegex(ValueError, "response"):
            self.collect()

    def test_origin_is_durable_before_preparation_and_deadline_includes_it(self):
        journal = self.start()
        row = json.loads((journal.path / "00-origin.json").read_bytes())
        self.assertEqual(row["data"]["preparation_begin_ns"], 100)
        self.assertEqual(row["data"]["attempt_deadline_ns"], 100 + 1200_000_000_000)
        self.assertEqual(row["identity"]["code_commit"], SOURCE["code_commit"])
        self.assertEqual(self.collect()[0]["status"], "incomplete")
        with self.assertRaisesRegex(ValueError, "blocks"):
            self.start()

    def test_origin_publication_failure_leaves_consumed_directory(self):
        with mock.patch.object(attempts, "write_once", side_effect=OSError("publication failed")):
            with self.assertRaisesRegex(OSError, "publication failed"):
                self.start()
        row = self.collect()[0]
        self.assertTrue(row["consumed"])
        self.assertEqual(row["status"], "incomplete")

    def test_forked_writer_identity_cannot_append(self):
        journal = self.start()
        changed = dict(journal.writer_identity, pid=journal.writer_identity["pid"] + 1)
        with mock.patch.object(attempts, "process_identity", return_value=changed):
            with self.assertRaisesRegex(ValueError, "writer process"):
                journal.fail(RuntimeError("child"))
        self.assertEqual(self.collect()[0]["status"], "incomplete")

    def test_prepare_failure_retains_id_and_has_no_completion_or_fake_latency(self):
        with self.assertRaisesRegex(ValueError, "cold failed"):
            with self.start():
                self.now = 120
                raise ValueError("cold failed")
        result = self.collect()[0]["journal"][-1]["data"]
        self.assertEqual(result["failed_stage"], "origin")
        self.assertIsNone(result["startup_censor_elapsed_seconds"])
        self.assertNotIn("correct_business_response_ns", result)
        self.assertTrue(result["cleanup_pending"])
        self.assertEqual(sum(r["consumed"] for r in self.collect()), 1)

    def test_scope_without_terminal_evidence_is_failed(self):
        with self.assertRaisesRegex(RuntimeError, "complete evidence"):
            with self.start():
                pass
        self.assertEqual(self.collect()[0]["status"], "failed")

    def test_bad_cache_is_failed_before_startup_publication(self):
        with self.assertRaisesRegex(ValueError, "identity"):
            with self.start() as journal:
                self.now = 170
                target, files, vectors = cache.scan()
                journal.startup({}, target, files, vectors, target, files, vectors, {})
        names = [p.name for p in journal.path.iterdir()]
        self.assertEqual(sorted(names), ["00-origin.json", "01-result.json"])

    def test_startup_cannot_replace_deadline_or_original_id(self):
        with self.assertRaisesRegex(ValueError, "frozen"):
            with self.start() as journal:
                args = cache.scan(begin=140)
                self.now = 170
                journal.startup(dict(attempt_deadline_ns=10**30), *args, *cache.scan("safetensors_inputs", "check", 120),
                                {k: journal.identity[k] for k in ("run_id", "boot_id", "time_namespace", "clock_id")})

    def test_trigger_must_follow_registration_and_state_order(self):
        with self.assertRaises(KeyError):
            with self.start() as journal:
                journal.trigger(1, "x")
        self.assertEqual(self.collect()[0]["status"], "failed")

    def test_timeout_keeps_censor_separate_from_observed_completion(self):
        with self.assertRaisesRegex(TimeoutError, "deadline"):
            with self.start() as journal:
                self.startup(journal)
                self.now = journal.record["attempt_deadline_ns"]
                journal.trigger(175, "cpu-business")
        result = self.collect()[0]["journal"][-1]["data"]
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["right_censored"])
        self.assertFalse(result["correct_business_response_observed"])
        self.assertNotIn("completed_ns", result)

    def test_business_metrics_use_validated_trace_and_fixed32_cannot_replace_them(self):
        with self.assertRaisesRegex(RuntimeError, "fixture stop"):
            with self.start() as journal:
                self.through_business(journal)
                before = dict(journal.record)
                self.now = 210
                response = {"choices": [{"token_ids": list(range(32)), "message": {"content": "x"},
                                          "finish_reason": "length"}]}
                journal.workload32(response, response)
                self.assertEqual(journal.record, before)
                raise RuntimeError("fixture stop")
        rows = self.collect()[0]["journal"]
        metrics = rows[3]["data"]["metrics"]
        self.assertEqual(metrics["command_to_correct_business_response_s"], 30 / 1e9)
        self.assertEqual(metrics["trigger_to_first_token_s"], 16 / 1e9)
        self.assertFalse(rows[-1]["data"]["right_censored"])

    def test_complete_synthetic_evidence_still_cannot_award_runtime_pass(self):
        with self.start() as journal:
            self.through_business(journal)
            self.now = 210
            response = {"choices": [{"token_ids": list(range(32)), "message": {"content": "x"},
                                      "finish_reason": "length"}]}
            journal.workload32(response, response)
            self.now = 220
            journal.delete(215)
            rows = []
            for second in range(61):
                now = 230 + second * 1_000_000_000
                rows.append(dict(second=second, monotonic_ns=now, delete_ack_ns=220,
                                 host_gpu_snapshot=dict(sample_begin_ns=now, sample_end_ns=now)))
            self.now = rows[-1]["monotonic_ns"] + 1
            journal.release(rows)
        result = self.collect()[0]["journal"][-1]["data"]
        self.assertEqual(result["status"], "evidence_complete_unqualified")
        self.assertFalse(result["success"])
        self.assertFalse(result["physical_cleanup_verified"])
        self.assertNotIn("right_censored", result)

    def test_clock_change_cannot_complete(self):
        with self.assertRaisesRegex(ValueError, "clock"):
            with self.start() as journal:
                self.startup(journal)
                self.now = 150
                journal.fail(ValueError("test"))
        # Context preserves the original recording exception; the ID stays unclosed.
        self.assertEqual(self.collect()[0]["status"], "incomplete")

    def test_corrupt_chain_and_symlink_inputs_block_collection(self):
        journal = self.start()
        self.now = 120
        journal.fail(ValueError("fixture"))
        origin = journal.path / "00-origin.json"
        original = origin.read_bytes()
        origin.write_bytes(original + b" ")
        with self.assertRaisesRegex(ValueError, "chain"):
            self.collect()
        origin.write_bytes(original)
        linked = journal.path / "02-result.json"
        linked.symlink_to(origin)
        with self.assertRaisesRegex(ValueError, "linked"):
            self.collect()

    def test_empty_claimed_directory_consumes_id_and_blocks_next_start(self):
        (self.root / ID).mkdir()
        self.assertTrue(self.collect()[0]["consumed"])
        self.assertFalse(self.collect()[0]["terminal"])
        with self.assertRaisesRegex(ValueError, "blocks"):
            self.start()

    def test_duplicate_execution_roots_or_id_cannot_disappear_from_denominator(self):
        self.start()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            attempts.collect_attempts(PROTOCOL, SHA, [self.root, self.root / "."])
        with tempfile.TemporaryDirectory() as second:
            (Path(second) / ID).mkdir()
            with self.assertRaisesRegex(ValueError, "multiple"):
                attempts.collect_attempts(PROTOCOL, SHA, [self.root, second])

    def test_terminal_cannot_be_overwritten_and_unknown_ids_are_rejected(self):
        journal = self.start()
        journal.fail(ValueError("first"))
        before = (journal.path / "01-result.json").read_bytes()
        with self.assertRaisesRegex(ValueError, "terminal"):
            journal.fail(ValueError("second"))
        self.assertEqual((journal.path / "01-result.json").read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "unknown"):
            attempts.AttemptJournal(self.root, "not_frozen", PROTOCOL, SHA, "cpu-run", 10**15,
                                    SOURCE, [self.root])

    def test_source_protocol_and_fixed_order_must_match_before_claim(self):
        with self.assertRaisesRegex(ValueError, "SHA"):
            attempts.AttemptJournal(self.root, ID, PROTOCOL, "0" * 64, "cpu-run", 10**15,
                                    SOURCE, [self.root])
        with self.assertRaisesRegex(ValueError, "source"):
            attempts.AttemptJournal(self.root, ID, PROTOCOL, SHA, "cpu-run", 10**15, {}, [self.root])
        second = json.loads(PROTOCOL.read_bytes())["attempts"][1]["attempt_id"]
        with self.assertRaisesRegex(ValueError, "order"):
            attempts.AttemptJournal(self.root, second, PROTOCOL, SHA, "cpu-run", 10**15,
                                    SOURCE, [self.root])
        self.assertFalse(list(self.root.iterdir()))

    def test_recording_error_does_not_mask_original_external_failure(self):
        error = RuntimeError("external operation")
        journal = self.start()
        with self.assertRaisesRegex(RuntimeError, "external operation"):
            with mock.patch.object(journal, "fail", side_effect=OSError("disk")):
                with journal:
                    raise error
        self.assertEqual(error.sllm_record_error, "OSError")
        self.assertEqual(self.collect()[0]["status"], "incomplete")

    def test_truncated_release_is_failed_after_business_without_censoring(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            with self.start() as journal:
                self.through_business(journal)
                self.now = 210
                response = {"choices": [{"token_ids": list(range(32)), "message": {"content": "x"},
                                          "finish_reason": "length"}]}
                journal.workload32(response, response)
                self.now = 220
                journal.delete(215)
                journal.release([])
        result = self.collect()[0]["journal"][-1]["data"]
        self.assertEqual(result["failed_stage"], "delete")
        self.assertTrue(result["correct_business_response_observed"])
        self.assertFalse(result["right_censored"])

    def test_missing_trace_does_not_erase_observed_correct_business_response(self):
        with self.assertRaisesRegex(ValueError, "No events"):
            with self.start() as journal:
                self.startup(journal)
                self.now = 180
                journal.trigger(175, "cpu-business")
                self.now = 200
                response = {"choices": [{"token_ids": [1], "message": {"content": "4"},
                                          "finish_reason": "stop"}]}
                journal.business(response, response, self.root / "no-events")
        self.assertTrue(self.collect()[0]["journal"][-1]["data"]["correct_business_response_observed"])

    def test_four_process_claims_produce_exactly_one_origin(self):
        context = multiprocessing.get_context("fork")
        barrier, queue = context.Barrier(4), context.Queue()
        workers = [context.Process(target=claim, args=(str(self.root), barrier, queue)) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(10)
            self.assertEqual(worker.exitcode, 0)
        results = [queue.get(timeout=2) for _ in workers]
        self.assertEqual(results.count("claimed"), 1)
        self.assertEqual(len(list((self.root / ID).glob("*-origin.json"))), 1)


if __name__ == "__main__":
    unittest.main()
