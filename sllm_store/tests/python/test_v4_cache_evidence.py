"""Synthetic offline cache receipts only; never warms or evicts model files."""
import copy
import hashlib
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("v4_cache_evidence", ROOT / "benchmarks/v4_evidence.py")
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


def scan(kind="store_inputs", mode="cold", begin=40):
    flags = b"\x00\x00" if mode == "cold" else b"\x01\x03"
    row = dict(source_path="/frozen/input", file="input", bytes=4097, page_size=4096,
               pages=2, resident_pages=sum(x & 1 for x in flags), vector="input.mincore.gz",
               vector_sha256=hashlib.sha256(flags).hexdigest())
    return (dict(kind=kind, mode=mode, files=[row],
                 cache_condition_passed=None if mode == "check" else True,
                 preparation_begin_ns=begin, preparation_complete_ns=begin + 1,
                 verification_begin_ns=begin + 2, verification_complete_ns=begin + 3,
                 total_cache_admission_end_ns=begin + 4),
            {"/frozen/input": 4097}, {row["vector"]: flags})


class CacheScanContracts(unittest.TestCase):
    def test_cold_hot_and_counterpart_full_vectors(self):
        for mode in ("cold", "hot", "check"):
            with self.subTest(mode=mode):
                result = evidence.cache_scan_checks(*scan(mode=mode))
                self.assertEqual((result["files"], result["pages"]), (1, 2))

    def test_thresholds_are_exact_not_aggregate_percentages(self):
        for mode, flags in (("cold", b"\x00\x01"), ("hot", b"\x01\x00")):
            summary, inventory, vectors = scan(mode=mode)
            row = summary["files"][0]
            vectors[row["vector"]] = flags
            row["resident_pages"] = 1
            row["vector_sha256"] = hashlib.sha256(flags).hexdigest()
            with self.assertRaisesRegex(ValueError, "condition failed"):
                evidence.cache_scan_checks(summary, inventory, vectors)

    def test_missing_duplicate_extra_files_or_vectors_rejected(self):
        for change in (lambda s, i, v: s["files"].clear(),
                       lambda s, i, v: s["files"].append(copy.deepcopy(s["files"][0])),
                       lambda s, i, v: i.update({"/unscanned": 1}),
                       lambda s, i, v: v.update(extra=b"")):
            args = scan()
            change(*args)
            with self.assertRaises(ValueError):
                evidence.cache_scan_checks(*args)

    def test_all_mincore_flag_values_use_only_the_residency_bit(self):
        summary,_,_=scan(mode="check")
        flags=bytes(range(256));row=summary["files"][0]
        row.update(bytes=256*4096,pages=256,resident_pages=128,
                   vector_sha256=hashlib.sha256(flags).hexdigest())
        checked=evidence.cache_scan_checks(summary,{row["source_path"]:row["bytes"]},{row["vector"]:flags})
        self.assertEqual(checked["pages"],256)

    def test_wrong_size_page_counters_checksum_and_short_vectors(self):
        for field, value in (("bytes", True), ("bytes", 4096), ("page_size", 0),
                             ("page_size", 4095), ("pages", True), ("resident_pages", 2),
                             ("vector_sha256", "0" * 64)):
            args = scan()
            args[0]["files"][0][field] = value
            with self.assertRaises(ValueError):
                evidence.cache_scan_checks(*args)
        args = scan()
        args[2]["input.mincore.gz"] = b"\x00"
        with self.assertRaisesRegex(ValueError, "incomplete"):
            evidence.cache_scan_checks(*args)

    def test_failed_summary_and_interleaved_verification_rejected(self):
        for change in (lambda s: s.update(cache_condition_passed=False),
                       lambda s: s.update(verification_begin_ns=0)):
            args = scan()
            change(args[0])
            with self.assertRaises(ValueError):
                evidence.cache_scan_checks(*args)


class CacheTimingContracts(unittest.TestCase):
    def setUp(self):
        self.target = scan()[0]
        self.other = scan("safetensors_inputs", "check", 20)[0]
        self.record = dict(preparation_begin_ns=0, preparation_complete_ns=60,
                           startup_command_ns=70, attempt_deadline_ns=1200_000_000_000,
                           store_validation_complete_ns=10, store_registration_reused=True,
                           pool_confirmation_begin_ns=45, pool_confirmation_complete_ns=55,
                           pool_confirmed=True)

    def test_four_conditions_preserve_preparation_and_command(self):
        for condition, kind, mode in (
            ("safetensors_disk_cold", "safetensors_inputs", "cold"),
            ("safetensors_disk_hot", "safetensors_inputs", "hot"),
            ("store_disk_cold", "store_inputs", "cold"),
            ("store_pool_hit", "store_inputs", "check")):
            target = scan(kind, mode)[0]
            other = scan("store_inputs" if kind == "safetensors_inputs" else "safetensors_inputs", "check", 20)[0]
            result = evidence.cache_timing_checks(self.record, target, other, condition)
            self.assertEqual(result["preparation_s"], 60 / 1e9)
            self.assertEqual(self.record["startup_command_ns"], 70)

    def test_bootstrap_validation_can_precede_attempt_without_moving_command(self):
        record = dict(self.record, preparation_begin_ns=5, store_validation_complete_ns=0)
        result = evidence.cache_timing_checks(record, self.target, self.other, "store_disk_cold")
        self.assertEqual(result["preparation_s"], 55 / 1e9)

    def test_unfrozen_condition_aliases_are_rejected(self):
        for alias in ("safetensors_cold", "safetensors_hot"):
            with self.assertRaisesRegex(ValueError, "unknown"):
                evidence.cache_timing_checks(self.record, self.target, self.other, alias)

    def test_hashing_after_cache_gate_or_new_timed_registration_rejected(self):
        for change in (dict(store_validation_complete_ns=41), dict(store_registration_reused=False)):
            record = dict(self.record, **change)
            with self.assertRaises(ValueError):
                evidence.cache_timing_checks(record, self.target, self.other, "store_disk_cold")

    def test_wrong_counterpart_mode_or_target_mode_rejected(self):
        for target, other in ((scan(mode="hot")[0], self.other),
                              (self.target, scan("safetensors_inputs", "cold", 20)[0]),
                              (self.target, scan("store_inputs", "check", 20)[0])):
            with self.assertRaises(ValueError):
                evidence.cache_timing_checks(self.record, target, other, "store_disk_cold")

    def test_shared_metadata_scan_must_precede_target_preparation(self):
        self.other["total_cache_admission_end_ns"] = 41
        with self.assertRaisesRegex(ValueError, "boundary"):
            evidence.cache_timing_checks(self.record, self.target, self.other, "store_disk_cold")

    def test_preparation_cannot_escape_timeout_or_command_boundary(self):
        for change in (dict(attempt_deadline_ns=1200_000_000_001),
                       dict(attempt_deadline_ns=70), dict(preparation_complete_ns=71),
                       dict(preparation_begin_ns=True)):
            with self.assertRaises(ValueError):
                evidence.cache_timing_checks(dict(self.record, **change), self.target, self.other, "store_disk_cold")

    def test_pool_hit_requires_reconfirmation_after_final_cache_scan(self):
        target = scan(mode="check")[0]
        for change in (dict(pool_confirmed=False), dict(pool_confirmation_begin_ns=43),
                       dict(pool_confirmation_complete_ns=61)):
            with self.assertRaisesRegex(ValueError, "cohort"):
                evidence.cache_timing_checks(dict(self.record, **change), target, self.other, "store_pool_hit")


if __name__ == "__main__":
    unittest.main()
