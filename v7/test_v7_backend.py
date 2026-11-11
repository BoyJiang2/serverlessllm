"""CPU checks for phase/cohort gates and retaining failed raw responses."""
import asyncio
import copy
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock

source = Path(__file__).resolve().parents[3] / "benchmarks/v7_backend.py"
spec = importlib.util.spec_from_file_location("v7_backend", source)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def cohort(sleeping=False):
    return [dict(rank=i, identity={"pid": 100+i}, device_uuid=str(i), parameter_signature=str(i),
                 tags={"weights": dict(bytes=8, cpu_backup_bytes=8 if sleeping else 0),
                       "kv_cache": dict(bytes=16, cpu_backup_bytes=0)})
            for i in range(8)]


class BackendGateTest(unittest.TestCase):
    def test_complete_cohort_sleep_and_wake(self):
        before = cohort()
        runner.validate_sleep_receipts(before, cohort(True), sleeping=True)
        runner.validate_sleep_receipts(before, cohort(), sleeping=False)

    def test_missing_rank_changed_worker_or_parameter_fails(self):
        for field, value in (("rank", 1), ("identity", {"pid": 999}),
                             ("parameter_signature", "other")):
            with self.subTest(field=field):
                changed = cohort(True)
                changed[0][field] = value
                with self.assertRaises(AssertionError):
                    runner.validate_sleep_receipts(cohort(), changed, sleeping=True)

    def test_partial_weight_backup_and_kv_backup_fail(self):
        for tag, count in (("weights", 4), ("kv_cache", 16)):
            changed = cohort(True)
            changed[0]["tags"][tag]["cpu_backup_bytes"] = count
            with self.assertRaises(AssertionError):
                runner.validate_sleep_receipts(cohort(), changed, sleeping=True)

    def test_freeing_inactive_blocks_is_allowed(self):
        before = cohort()
        before[0]["tags"]["weights"]["bytes"] = 16
        runner.validate_sleep_receipts(before, cohort(True), sleeping=True)

    def test_failed_answer_is_saved_before_assertion(self):
        response = dict(choices=[dict(message=dict(content="answer is 42"),
                                     token_ids=[1], finish_reason="stop")],
                        usage=dict(completion_tokens=1))
        backend = types.SimpleNamespace(generate=mock.AsyncMock(return_value=response))
        item = dict(name="added", payload={}, validation="exact_numeric_answer", expected_content="42")
        writes = []
        with mock.patch.object(runner, "write", side_effect=lambda n, v: writes.append((n, copy.deepcopy(v)))):
            with self.assertRaises(AssertionError):
                asyncio.run(runner.run_requests(backend, dict(name="test", requests=[item]), "after"))
        self.assertEqual(writes[0][0], "after-added-response.json")
        self.assertEqual(writes[0][1]["response"], response)
        self.assertFalse(writes[1][1]["passed"])


if __name__ == "__main__":
    unittest.main()
