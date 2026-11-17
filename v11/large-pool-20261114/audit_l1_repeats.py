"""Verify and recompute the DeepSeek-V4-Pro native L1 repeat batch."""

import argparse
import json
from pathlib import Path
from statistics import median

from recompute_sleep import allocator_span, final_release, load_span, read, seconds


def metric(span):
    return seconds(span["begin_ns"], span["end_ns"])


def audit(run, qualification):
    release = final_release(run, qualification)
    if not qualification["passed"]:
        return dict(run=run.name, passed=False, release=release,
                    failures=qualification["failures"])

    config = read(run / "campaign-input.json")
    level, = qualification["results"]
    assert level["level"] == 1 and level["models"] == ["D", "D"]
    assert level["pid_continuity"] and level["golds_passed"]
    assert release["all_gpu_zero"] and release["meets_60s"]
    worker = run / "L1/worker-D"
    cold = read(worker / "sf-admission/summary.json")
    assert cold["cache_condition_passed"] and cold["mode"] == "cold"
    assert len(cold["files"]) == 64 and all(row["resident_pages"] == 0 for row in cold["files"])
    assert read(run / "L1/worker-D-final.json")["State"]["ExitCode"] == 0
    imports = read(worker / "worker-imports.json")
    assert {row["rank"] for row in imports} == set(range(8))
    assert all(not row["imports"] for row in imports)
    lifecycle = [row for path in sorted(worker.glob("ack-*.json"))
                 if (row := read(path))["op"] in {"sleep", "wake"}]
    assert [row["op"] for row in lifecycle] == ["sleep", "wake"]
    assert [row["sleeping"] for row in lifecycle] == [True, False]
    assert all(len(row["receipt"]["ranks"]) == 8 for row in lifecycle)
    identity_keys = ("pid", "process_start_ticks", "boot_id", "time_namespace", "pid_namespace", "clock_id")
    identities = [{rank["rank"]: (rank["device_uuid"], *[rank["identity"][key] for key in identity_keys])
                   for rank in row["receipt"]["ranks"]} for row in lifecycle]
    assert set(identities[0]) == set(range(8)) and identities[0] == identities[1]
    fingerprint = None
    for row in lifecycle:
        current = {rank["rank"]: (rank["allocation_signature"], rank["parameter_signature"])
                   for rank in row["receipt"]["ranks"]}
        assert fingerprint is None or fingerprint == current
        fingerprint = current
        for rank in row["receipt"]["ranks"]:
            weights = rank["tags"]["weights"]
            assert weights["cpu_backup_bytes"] == (weights["bytes"] if row["sleeping"] else 0)
            assert not rank["uncovered_cuda_parameters"]

    steps = []
    old_kv = None
    for index, rec in enumerate(level["records"]):
        stage = rec["stage"]
        ready = stage["ready_ns"]
        assert rec["step"] == index and stage["kind"] == ("wake" if index else "load")
        assert rec["weights_cpu_backup_bytes"] == (0 if index else config["l1_backup_bytes"])
        assert rec["kv_bytes"] > 0
        log = worker / "worker.log"
        backup = unmap = sleep_s = wake_s = None
        if index:
            assert rec["sleep_phases_receipt"] is None
            wake = read(worker / stage["phases_receipt"])
            start, finish = wake["combined_wake_begin_ns"], wake["combined_wake_end_ns"]
            wake_s = seconds(start, finish)
            weight_map = allocator_span(log, "gpu_create_map", "weights", start, finish)
            weight = allocator_span(log, "cpu_restore_copy", "weights", start, finish)
            kv = allocator_span(log, "gpu_create_map", "kv_cache", start, finish)
            weight_begin = weight_map["begin_ns"]
        else:
            phase = read(worker / rec["sleep_phases_receipt"])
            begin, end = phase["sleep_begin_ns"], phase["sleep_end_ns"]
            assert rec["t4_ns"] <= begin < end
            sleep_s = seconds(begin, end)
            backup = allocator_span(log, "cpu_backup_copy", "weights", begin, end)
            unmap = allocator_span(log, "gpu_unmap_release", "kv_cache", begin, end)
            assert sum(row["bytes"] for row in backup["rows"]) == config["l1_backup_bytes"]
            weight = load_span(worker, "full_weight_load", run.name, ready)
            kv = load_span(worker, "kv_cache_allocation", run.name, ready)
            weight_begin = weight["begin_ns"]
        response = read(worker / f"c{index}-business-response.json")
        assert rec["checks"]["business"]["passed"]
        assert ready <= response["started_ns"] <= response["received_ns"] <= rec["t4_ns"]
        steps.append(dict(stage=stage["kind"], worker_pid=rec["worker_pid"],
                          weight_s=seconds(weight_begin, weight["end_ns"]),
                          kv_s=metric(kv), switch_s=seconds(old_kv, kv["end_ns"]),
                          weight_start_to_ready_s=seconds(weight_begin, ready),
                          first_correct_s=seconds(ready, response["received_ns"]),
                          all_golds_s=seconds(ready, rec["t4_ns"]),
                          gpu_kv_bytes=rec["kv_bytes"],
                          cpu_backup_bytes=rec["weights_cpu_backup_bytes"],
                          cpu_backup_copy_s=metric(backup) if backup else None,
                          cpu_restore_copy_s=metric(weight) if index else None,
                          sleep_s=sleep_s, wake_s=wake_s,
                          timing=dict(backup=backup, weight=weight, kv=kv, kv_unmap=unmap)))
        old_kv = unmap["begin_ns"] if unmap else None
    assert len(steps) == 2
    return dict(run=run.name, passed=True, release=release, steps=steps,
                rank_identities={rank: dict(zip(("device_uuid", *identity_keys), values))
                                 for rank, values in identities[0].items()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    args = parser.parse_args()
    rounds = []
    for run in sorted(args.runs.glob("B2-L1-D-*")):
        qualification = run / "qualification.json"
        if qualification.is_file():
            rounds.append(audit(run, read(qualification)))
            continue
        failed_release = run / "qualification-recovery-release.jsonl"
        if failed_release.is_file():
            rows = [json.loads(line) for line in failed_release.read_bytes().splitlines()]
            assert all(row["gpu_used_mib"] == [0] * 8 and row["compute_apps"] == 0
                       and not row["container_running"] for row in rows)
            elapsed = seconds(rows[0]["ns"], rows[-1]["ns"])
            assert elapsed >= 60
            rounds.append(dict(run=run.name, passed=False, failures=read(run / "failure.json"),
                               release=dict(source=failed_release.as_posix(), samples=len(rows),
                                            elapsed_s=elapsed, all_gpu_zero=True, meets_60s=True)))
    summaries = {}
    for index, stage in enumerate(("load", "wake")):
        rows = [row["steps"][index] for row in rounds if row["passed"]]
        summaries[stage] = {}
        for key in ("weight_s", "kv_s", "switch_s", "weight_start_to_ready_s",
                    "first_correct_s", "all_golds_s", "cpu_backup_copy_s",
                    "cpu_restore_copy_s", "sleep_s", "wake_s"):
            values = [row[key] for row in rows if row[key] is not None]
            summaries[stage][key] = dict(n=len(values), median=median(values) if values else None,
                                        range=[min(values), max(values)] if values else None)
    print(json.dumps(dict(rounds=rounds, summaries=summaries), indent=2))


if __name__ == "__main__":
    main()
