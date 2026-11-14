"""Export completed formal rounds for the V11 large-pool report."""
import argparse
import csv
import json
from pathlib import Path

from summarize_pilot import load, summarize


def collect(root):
    campaign = load(root / "campaign.json")
    rounds = []
    for rep, block in enumerate(campaign["blocks"], 1):
        for sequence in block:
            qualified = []
            for run in sorted((root / "runs").glob(f"B1-{sequence}-r{rep}-*")):
                receipt = run / "qualification.json"
                if not receipt.exists():
                    continue
                q = load(receipt)
                if q["passed"] and q["strict_release_passed"]:
                    qualified.append((run, q))
            if not qualified:
                continue
            (run, q), = qualified
            assert q["models"] == campaign["sequences"][sequence]
            assert q["activations"] == 7 and q["final_release_seconds"] >= 60
            data = summarize(run)
            with (run / "switch-events.csv").open(newline="") as source:
                switches = list(csv.DictReader(source))
            assert len(data["steps"]) == len(switches) == 7
            assert [row["to_model"] for row in switches] == q["models"]
            assert {row["store_pid"] for row in switches} == {switches[0]["store_pid"]}
            assert int(switches[0]["store_pid"]) > 0
            cold = load(run / data["steps"][0]["step"] / "sf-admission" / "summary.json")
            assert cold["mode"] == "cold" and cold["cache_condition_passed"]
            assert cold["files"] and all(row["resident_pages"] == 0 for row in cold["files"])
            assert switches[0]["cache_state"] == "safetensors_disk_cold"
            assert int(switches[0]["file_read_bytes"]) == 0
            assert all(row["cache_state"] in ("full_hit", "full_miss", "partial_hit")
                       for row in switches[1:])
            assert len(q["b1_host_kv"]["steps"]) == 7
            for step, row, kv in zip(data["steps"], switches, q["b1_host_kv"]["steps"], strict=True):
                assert step["step"].startswith(f"{int(row['step']):02d}-{row['to_model']}-")
                assert kv["closed"] and kv["zero_hit"] and kv["used_after_clear"] == 0
                assert kv["committed_tokens"] == 512 and kv["peak_bytes"] > 0
                start = int(row["t2_ns"])
                correct = step["first_correct_business_response_ns"]
                assert start <= step["engine_ready_ns"] <= correct <= int(row["t4_ns"])
                step["activation_to_first_correct_business_s"] = (correct - start) / 1e9
                step["switch_to_first_correct_business_s"] = (
                    (correct - int(row["t0_ns"])) / 1e9 if row["t0_ns"] else None)
                step.update(switch=row, host_kv=kv)
            with (run / "final-residency.jsonl").open() as source:
                final = [json.loads(line) for line in source]
            after_clear = data["steps"][-1]["residency"]["after_clear"]
            assert after_clear["ns"] <= final[0]["ns"]
            assert final[-1]["ns"] - after_clear["ns"] >= 60 * 10**9
            service_pids = lambda sample: {
                role: {process["pid"] for process in sample["processes"][role]}
                for role in ("lmcache", "store")
            }
            assert all(service_pids(sample) == service_pids(after_clear) for sample in final)
            with (run / "release-samples.jsonl").open() as source:
                release = [json.loads(line) for line in source]
            assert release[-1]["ns"] - release[0]["ns"] >= 60 * 10**9
            assert all(sample["compute_apps"] == 0 and
                       all(sample[f"gpu{index}"] == 0 for index in range(8))
                       for sample in release)
            rounds.append(dict(sequence=sequence, repeat=rep, run=run.name,
                               release_seconds=q["final_release_seconds"],
                               final_residency=final, **data))
    return dict(expected_rounds=9, completed_rounds=len(rounds), rounds=rounds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    print(json.dumps(collect(args.root), indent=2))
