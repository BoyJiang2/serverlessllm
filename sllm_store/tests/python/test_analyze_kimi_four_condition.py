"""Synthetic evidence only; never opens a formal run or model."""
import copy
import hashlib
import importlib.util
import json
import pathlib
import random
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[3] / "benchmarks/analyze_kimi_four_condition.py"
spec = importlib.util.spec_from_file_location("analysis", SOURCE)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


class AnalysisConsumerTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="kimi-analysis-unit-")
        self.addCleanup(self.folder.cleanup)
        self.root = pathlib.Path(self.folder.name)
        self.serial = 0

    def ref(self, data):
        self.serial += 1
        p = self.root / (str(self.serial) + ".json")
        p.write_text(json.dumps(data))
        return {"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}

    def edit(self, row, key, change):
        data = json.loads(pathlib.Path(row["refs"][key]["path"]).read_text())
        change(data)
        row["refs"][key] = self.ref(data)
        if key in ("metrics", "terminal", "cleanup"):
            filename = {"metrics": "sample-metrics.json", "terminal": "condition-terminal.json", "cleanup": "cleanup-result.json"}[key]
            validity = json.loads(pathlib.Path(row["refs"]["validity"]["path"]).read_text())
            for item in validity.get("evidence", []):
                if pathlib.Path(item["path"]).name == filename: item["sha256"] = row["refs"][key]["sha256"]
            row["refs"]["validity"] = self.ref(validity)

    def identity(self, start=100, host="client"):
        return dict(boot_id="boot", time_namespace="time:[1]", monotonic_clock_implementation="CLOCK_MONOTONIC",
                    process_startticks=start, container_hostname=host)

    def run_entry(self, block, condition, cohort="original", qualified=True, suffix=""):
        run = f"b{block}-{condition}{suffix}"
        version = {"source_commit": "a" * 40, "protocol_sha256": "b" * 64}
        common = dict(run_id=run, condition=condition, attempt={"block": block}, **version)
        refs = dict.fromkeys(("metrics", "validity", "terminal", "cleanup", "controller", "construction_begin", "admission_complete", "lifecycle"))
        refs["controller"] = self.ref(dict(status="CONDITION_PASS" if qualified else "FAILED_RETAINED", **common))
        refs["validity"] = self.ref(dict(primary_sample_qualified=qualified, gates={k: {"status": "PASS"} for k in ("cache", "gold", "clock", "ownership")}, **common))
        refs["terminal"] = self.ref(dict(normal=qualified, **common))
        refs["cleanup"] = self.ref(dict(normal=qualified, quiet_seconds=60 if qualified else None, **common))
        if qualified:
            requests = []
            first = []
            for i, case in enumerate(("arithmetic", "translation", "fixed32")):
                trigger = (117 + 100 * i) * 10**9
                times = dict(registration_command_ns=115 * 10**9 if i == 0 else None,
                             register_complete_ns=116 * 10**9 if i == 0 else None,
                             business_trigger_ns=trigger, request_begin_ns=trigger + 10**9,
                             response_body_complete_ns=trigger + 3 * 10**9,
                             json_parse_complete_ns=trigger + 4 * 10**9, received_ns=trigger + 5 * 10**9,
                             child_gold_validation_begin_ns=trigger + 6 * 10**9,
                             child_gold_validation_complete_ns=trigger + 7 * 10**9)
                requests.append(dict(case=case, request_id=run + "-" + str(i), raw_interval_status="NUMERIC",
                                     child_gold_validation_pass=True, producer_times=times,
                                     business_B_ns=5 * 10**9, primary_A_ns=7 * 10**9 if i == 0 else None,
                                     stdout_reference={"path": "/not-opened-by-unit", "sha256": "c" * 64},
                                     clock_identity=self.identity(100 + i)))
                first.append(dict(request_id=run + "-" + str(i), pid=99, monotonic_ns=trigger + 2 * 10**9,
                                  clock_identity=self.identity(800, "backend")))
            arithmetic = requests[0]
            event_common = dict(request_id=arithmetic["request_id"], client_pid=123, client_cid="cid", clock_identity=arithmetic["clock_identity"], http_producer_times=arithmetic["producer_times"], **common)
            refs["registration_command"] = self.ref(dict(event="command_register_begin", producer="same_http_client_as_arithmetic", monotonic_ns=arithmetic["producer_times"]["registration_command_ns"], **event_common))
            refs["arithmetic_complete"] = self.ref(dict(event="arithmetic_http_complete_correct", endpoint="received_ns", monotonic_ns=arithmetic["producer_times"]["received_ns"], primary_A_ns=arithmetic["primary_A_ns"], business_B_ns=arithmetic["business_B_ns"], registration_command_ns=arithmetic["producer_times"]["registration_command_ns"], register_complete_ns=arithmetic["producer_times"]["register_complete_ns"], response_body_complete_ns=arithmetic["producer_times"]["response_body_complete_ns"], **event_common))
            refs["metrics"] = self.ref(dict(requests=requests, **common))
            refs["construction_begin"] = self.ref(dict(event="target_condition_construction_begin", monotonic_ns=100 * 10**9, clock_identity=self.identity(10, "controller"), **common))
            refs["admission_complete"] = self.ref(dict(event="cache_pool_admission_complete", monotonic_ns=110 * 10**9, clock_identity=self.identity(10, "controller"), **common))
            refs["lifecycle"] = self.ref(dict(events={"backend_first_token_observed": first}, backend_owner={"pid": 444, "startticks": 800}, **common))
        validity = json.loads(pathlib.Path(refs["validity"]["path"]).read_text())
        validity["evidence"] = [{"path": "/original/" + filename, "sha256": refs[key]["sha256"]} for key, filename in (("metrics", "sample-metrics.json"), ("terminal", "condition-terminal.json"), ("cleanup", "cleanup-result.json")) if refs[key]]
        refs["validity"] = self.ref(validity)
        return dict(run_id=run, cohort=cohort, block=block, condition=condition, refs=refs)

    def manifest(self):
        rows, blocks = [], []
        for block in range(1, 11):
            group = []
            for j, condition in enumerate(a.CONDITIONS):
                qualified = not ((block == 4 and j == 0) or (block == 9 and j == 1))
                row = self.run_entry(block, condition, qualified=qualified)
                rows.append(row)
                group.append(dict(run_id=row["run_id"], status="CONDITION_PASS" if qualified else "FAILED_RETAINED", returncode=0 if qualified else 1))
            blocks.append(dict(block=block, final=self.ref(dict(status=a.PASS if block not in (4, 9) else "FAILED_RETAINED", attempts=group, source_commit="a" * 40, protocol_sha256="b" * 64))))
        rows.append(self.run_entry(4, a.CONDITIONS[0], "supplement", suffix="-retry01"))
        for i in range(5):
            rows.append(self.run_entry(1, a.CONDITIONS[1], "historical", qualified=i == 0, suffix="-history" + str(i)))
        return dict(schema="kimi-four-analysis-input-v1", runs=rows, blocks=blocks, provenance=[])

    def test_actual_schema_costs_and_first_output_domains(self):
        row = a.load_run(self.run_entry(1, a.CONDITIONS[0]))
        self.assertEqual(row["timing"], dict(A_s=7., arithmetic_B_s=5., translation_B_s=5., D_fixed32_s=5., Prep_s=10., Gap_s=5., Prep_plus_A_s=17., contiguous_s=22.))
        self.assertEqual(row["backend_first_nonempty"]["fixed32"]["latency_from_business_trigger_s"], 2.)
        self.assertEqual(row["client_TTFT"], "UNMEASURED")
        self.assertEqual(row["pure_decode"], "UNMEASURED")

    def test_sha_ns_clock_cleanup_and_identity_contradictions_reject(self):
        for bad in ("sha", "nanoseconds", "A", "clock", "quiet", "terminal", "request", "first_owner"):
            row = self.run_entry(1, a.CONDITIONS[0])
            if bad == "sha": row["refs"]["metrics"]["sha256"] = "0" * 64
            if bad == "nanoseconds": self.edit(row, "metrics", lambda d: d["requests"][0]["producer_times"].update(received_ns=122.0))
            if bad == "A": self.edit(row, "metrics", lambda d: d["requests"][0].update(primary_A_ns=1))
            if bad == "clock": self.edit(row, "admission_complete", lambda d: d["clock_identity"].update(time_namespace="foreign"))
            if bad == "quiet": self.edit(row, "cleanup", lambda d: d.update(quiet_seconds=59.9))
            if bad == "terminal": self.edit(row, "terminal", lambda d: d.update(normal=False))
            if bad == "request": self.edit(row, "metrics", lambda d: d["requests"][0].update(request_id="foreign"))
            if bad == "first_owner": self.edit(row, "lifecycle", lambda d: d["backend_owner"].update(startticks=999))
            with self.subTest(bad=bad), self.assertRaises(ValueError): a.load_run(row)

    def test_primary_endpoint_events_required_and_sha_checked(self):
        for key in ("registration_command", "arithmetic_complete"):
            for bad in ("missing", "sha"):
                row = self.run_entry(1, a.CONDITIONS[0])
                if bad == "missing": row["refs"][key] = None
                else: row["refs"][key]["sha256"] = "0" * 64
                with self.subTest(key=key, bad=bad), self.assertRaises(ValueError): a.load_run(row)

    def test_actual_consumer_rejects_primary_event_endpoint_and_identity_drift(self):
        for key, field, value in (("registration_command", "run_id", "foreign"), ("arithmetic_complete", "condition", "store_pool_hit"),
                                  ("registration_command", "request_id", "foreign"), ("arithmetic_complete", "client_pid", 999),
                                  ("arithmetic_complete", "client_cid", "foreign"), ("registration_command", "monotonic_ns", 1),
                                  ("arithmetic_complete", "monotonic_ns", 1), ("arithmetic_complete", "primary_A_ns", 1),
                                  ("arithmetic_complete", "business_B_ns", 1), ("arithmetic_complete", "registration_command_ns", 1),
                                  ("registration_command", "producer", "parent"), ("arithmetic_complete", "endpoint", "validation_end")):
            row = self.run_entry(1, a.CONDITIONS[0]); self.edit(row, key, lambda d: d.update({field: value}))
            with self.subTest(key=key, field=field), self.assertRaises(ValueError): a.load_run(row)
        for field in ("clock_identity", "http_producer_times"):
            row = self.run_entry(1, a.CONDITIONS[0])
            self.edit(row, "arithmetic_complete", lambda d: d[field].update({"process_startticks": 999} if field == "clock_identity" else {"received_ns": 1}))
            with self.subTest(field=field), self.assertRaises(ValueError): a.load_run(row)

    def test_qualified_validity_must_bind_the_consumed_metrics_sha(self):
        row = self.run_entry(1, a.CONDITIONS[0])
        data = json.loads(pathlib.Path(row["refs"]["metrics"]["path"]).read_text())
        data["unrelated_metadata"] = "different immutable artifact"
        row["refs"]["metrics"] = self.ref(data)
        with self.assertRaisesRegex(ValueError, "validity evidence SHA link"):
            a.load_run(row)

    def test_failed_raw_not_observed_rows_need_no_fabricated_request_id_or_time(self):
        row = self.run_entry(4, a.CONDITIONS[0], qualified=False)
        row["refs"]["metrics"] = self.ref(dict(run_id=row["run_id"], condition=row["condition"], source_commit="a" * 40, protocol_sha256="b" * 64,
            requests=[{"case": case, "observation_status": "NOT_OBSERVED", "primary_A_ns": None, "business_B_ns": None} for case in ("arithmetic", "translation", "fixed32")]))
        result = a.load_run(row)
        self.assertEqual(result["observation_status"], "NOT_OBSERVED")
        self.assertIsNone(result["timing"]["A_s"])
        self.assertIsNone(result["timing"]["D_fixed32_s"])

    def test_original_failed_rows_not_overwritten_or_paired_by_extra(self):
        manifest = self.manifest()
        rows, complete, excluded, versions = a.load_inputs(manifest)
        self.assertEqual(len(rows), 46)
        self.assertEqual([b["block"] for b in complete], [1, 2, 3, 5, 6, 7, 8, 10])
        self.assertEqual([b["block"] for b in excluded], [4, 9])
        original = next(r for r in rows if r["cohort"] == "original" and r["block"] == 4 and r["condition"] == a.CONDITIONS[0])
        self.assertFalse(original["qualified"])
        self.assertEqual(original["observation_status"], "NOT_OBSERVED")
        self.assertIsNone(original["timing"]["A_s"])
        with mock.patch.object(a, "bootstrap", return_value=[0., 1.]):
            report = a.analyze(rows, complete, excluded, versions)
        self.assertEqual(report["paired_sample_count"], 32)
        self.assertEqual(report["cohorts"]["original"]["qualified"], 38)
        self.assertEqual(report["cohorts"]["all"]["launch_requested"], 46)
        self.assertEqual(report["cohorts"]["all"]["qualified"], 40)
        self.assertEqual(report["cohorts"]["all"]["failed_launched"], 6)
        manifest["runs"].append(copy.deepcopy(manifest["runs"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate run_id"): a.load_inputs(manifest)

    def test_incomplete_block_not_promoted_and_pass_version_mismatch_rejects(self):
        manifest = self.manifest()
        manifest["blocks"][3]["final"] = self.ref(dict(status="FAILED_RETAINED", attempts=[]))
        self.assertNotIn(4, [b["block"] for b in a.load_inputs(manifest)[1]])
        row = manifest["runs"][0]
        for key in ("metrics", "validity", "terminal", "cleanup", "controller", "construction_begin", "admission_complete", "lifecycle", "registration_command", "arithmetic_complete"):
            self.edit(row, key, lambda d: d.update(source_commit="c" * 40))
        with self.assertRaisesRegex(ValueError, "version contradiction"): a.load_inputs(manifest)

    def test_historical_old_schema_kept_without_new_criterion_validation(self):
        row = self.run_entry(1, a.CONDITIONS[0], "historical")
        row["refs"]["metrics"] = self.ref({"old_loading_time": 12, "not_a_current_schema": True})
        result = a.load_run(row)
        self.assertTrue(result["qualified"])
        self.assertEqual(result["historical_raw_metrics"]["old_loading_time"], 12)
        self.assertIsNone(result["timing"]["A_s"])
        self.assertEqual(result["observation_status"], "HISTORICAL_PRESERVED_NOT_REVALIDATED")

    def test_actual_cli_synthetic_input_and_exclusive_output(self):
        manifest = self.manifest()
        # No inference is estimable in this synthetic terminal-failure fixture.
        for block in manifest["blocks"]:
            block["final"] = self.ref({"status": "FAILED_RETAINED", "attempts": []})
        ref = self.ref(manifest)
        output = self.root / "report.json"
        argv = [sys.executable, "-B", str(SOURCE), ref["path"], str(output), "--manifest-sha", ref["sha256"]]
        first = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(output.read_text())
        self.assertEqual(report["complete_block_ids"], [])
        self.assertIsNone(report["primary_A_Holm4"][0]["exact_two_sided_p"])
        before = output.read_bytes()
        self.assertNotEqual(subprocess.run(argv, capture_output=True, timeout=30).returncode, 0)
        self.assertEqual(output.read_bytes(), before)


class StatisticalContractTest(unittest.TestCase):
    def test_exact_enumeration_tolerance_and_no_plus_one(self):
        self.assertEqual(a.exact_sign_flip([1., 2., 4.]), .25)
        self.assertEqual(a.exact_sign_flip([1., 2.]), .5)
        self.assertEqual(a.exact_sign_flip([0., 0.]), 1.)
        self.assertEqual(a.exact_sign_flip([1., 1e-13]), 1.)
        self.assertIsNone(a.exact_sign_flip([]))

    def test_holm_mean_ratio_and_undefined_dz(self):
        for x, y in zip(a.holm([.01, .04, .03, .2]), [.04, .09, .09, .2]): self.assertAlmostEqual(x, y)
        self.assertAlmostEqual(a.ratio([2., 8.], [1., 2.]), 10. / 3.)
        self.assertNotEqual(a.ratio([2., 8.], [1., 2.]), 3.)
        complete = [dict(block=i, values={c: {"A_s": float(j + 1)} for j, c in enumerate(a.CONDITIONS)}) for i in (1, 2)]
        with mock.patch.object(a, "bootstrap", return_value=[0., 1.]): result = a.paired_family(complete, "A_s")
        self.assertTrue(all(r["dz"] is None for r in result))
        self.assertTrue(all(r["dz_status"].startswith("UNDEFINED") for r in result))

    def test_bootstrap_mt19937_reset_paired_order_and_percentile_indices(self):
        left, right = [2., 9., 5.], [1., 4., 3.]
        rng = random.Random(20261024)
        values = []
        for _ in range(10000):
            ix = [rng.randrange(3) for _ in range(3)]
            values.append(sum(left[i] - right[i] for i in ix) / 3)
        values.sort()
        expected = [values[249], values[9749]]
        self.assertEqual(a.bootstrap(left, right, "difference"), expected)
        a.bootstrap(left, right, "ratio")
        self.assertEqual(a.bootstrap(left, right, "difference"), expected)
        self.assertIsNone(a.bootstrap([], [], "difference"))
        self.assertIsNone(a.bootstrap([1.], [0.], "ratio"))
