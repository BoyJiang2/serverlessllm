"""Recompute Kimi → V4 → Kimi native L1 admission from historical receipts."""

import argparse
import hashlib
import json
from pathlib import Path


GIB = 2**30
MIB = 2**20
RESERVE = 200 * GIB
RANKS = range(8)


def read(path):
    return json.loads(path.read_bytes())


def source(path):
    return dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def range_of(values):
    return [min(values), max(values)]


def summarize(rows):
    assert rows
    return dict(sample_count=len(rows), ns=[rows[0]["ns"], rows[-1]["ns"]],
                host_available_bytes=range_of([row["mem_available"] for row in rows]),
                last_host_available_bytes=rows[-1]["mem_available"],
                gpu_used_mib_by_rank=[range_of([row[f"gpu{i}"] for row in rows]) for i in RANKS])


def load_run(run, model):
    phase = f"L1-00-{model}"
    samples_path = run / "resource-samples.jsonl"
    rows = [json.loads(line) for line in samples_path.read_bytes().splitlines()]
    assert all(left["ns"] < right["ns"] for left, right in zip(rows, rows[1:]))
    receipt_path = run / f"L1/worker-{model}/ack-0001.json"
    receipt = read(receipt_path)
    assert receipt["op"] == "sleep" and receipt["sleeping"] is True
    ranks = {row["rank"]: row for row in receipt["receipt"]["ranks"]}
    assert len(receipt["receipt"]["ranks"]) == len(ranks) == 8 and sorted(ranks) == list(RANKS)
    begin, end = receipt["started_ns"], receipt["finished_ns"]
    before = [row for row in rows if row["phase"] == phase and row["ns"] < begin]
    during = [row for row in rows if row["phase"] == phase and begin <= row["ns"] <= end]
    after = [row for row in rows if row["phase"] == phase + ":observation" and end <= row["ns"]]
    startup, = [row for row in rows if row["phase"] == "startup"]
    min_backup_available = min(row["mem_available"] for row in during + after)
    cgroups = [(row["ns"], row["cgroup_memory"]) for row in after if row.get("cgroup_memory", {}).get("stat")]
    cgroup_ranges = None
    if cgroups:
        stats = [entry["stat"] for _, entry in cgroups]
        cgroup_ranges = dict(sample_count=len(cgroups), current_bytes=range_of([int(entry["current"]) for _, entry in cgroups]),
                             **{key + "_bytes": range_of([row[key] for row in stats]) for key in ("anon", "file", "shmem")})
    weights = [ranks[i]["tags"]["weights"] for i in RANKS]
    kv = [ranks[i]["tags"]["kv_cache"] for i in RANKS]
    assert all(item["cpu_backup_bytes"] == item["bytes"] > 0 for item in weights)
    assert all(item["cpu_backup_bytes"] == 0 and item["bytes"] > 0 for item in kv)
    return dict(run=run.name, dataset=[source(samples_path), source(receipt_path)],
                startup_host_available_bytes=startup["mem_available"],
                before_sleep=summarize(before), sleep_command=summarize(during), post_sleep=summarize(after),
                sleep_command_ns=[begin, end], cold_to_pre_sleep_peak_bytes=startup["mem_available"] - min(row["mem_available"] for row in before),
                cold_to_sleep_peak_bytes=startup["mem_available"] - min_backup_available,
                sleep_increment_peak_bytes=before[-1]["mem_available"] - min_backup_available,
                cpu_weight_backup_bytes=sum(item["cpu_backup_bytes"] for item in weights),
                rank_mapped_weights_bytes=[item["bytes"] for item in weights],
                rank_mapped_kv_bytes=[item["bytes"] for item in kv],
                post_sleep_cgroup_ranges=cgroup_ranges)


def derive(kimi_path, v4_paths):
    kimi = load_run(kimi_path, "K")
    v4 = [load_run(path, "D") for path in v4_paths]
    assert len({run["run"] for run in v4}) == 3
    for run, path in zip(v4, v4_paths):
        receipt_path = path / "qualification.json"
        qualification = read(receipt_path)
        assert qualification["passed"] and qualification["final_all_gpu_zero"]
        assert qualification["final_release_seconds"] >= 60
        run["dataset"].append(source(receipt_path))
    gate_paths = [path / "L1/00-D-vram-gate.json" for path in v4_paths]
    gates = [read(path) for path in gate_paths]
    cold_v4_required, = {gate["required_mib"] for gate in gates}
    assert all(gate["passed"] and len(gate["free_mib"]) == 8 for gate in gates)
    idle_gpu = [min(gate["free_mib"][i] for gate in gates) for i in RANKS]
    kimi_residual = [lo_hi[1] for lo_hi in kimi["post_sleep"]["gpu_used_mib_by_rank"]]
    free_after_kimi = [total - used for total, used in zip(idle_gpu, kimi_residual)]
    v4_peak = [max(run["before_sleep"]["gpu_used_mib_by_rank"][i][1] for run in v4) for i in RANKS]
    kimi_wake = [(w + k + MIB - 1) // MIB for w, k in zip(kimi["rank_mapped_weights_bytes"], kimi["rank_mapped_kv_bytes"])]
    after_kimi = kimi["post_sleep"]["host_available_bytes"][0]
    v4_load_peak = max(run["cold_to_pre_sleep_peak_bytes"] for run in v4)
    v4_sleep_peak = max(run["sleep_increment_peak_bytes"] for run in v4)
    v4_cold_sleep_peak = max(run["cold_to_sleep_peak_bytes"] for run in v4)
    v4_backup, = {run["cpu_weight_backup_bytes"] for run in v4}
    projected_v4_awake = after_kimi - v4_load_peak
    projected_v4_sleeping = after_kimi - v4_cold_sleep_peak
    v4_hbm_gate_margin = [free - cold_v4_required for free in free_after_kimi]
    v4_hbm_peak_margin = [free - peak for free, peak in zip(free_after_kimi, v4_peak)]
    return dict(kimi=kimi, v4=v4, reserve_bytes=RESERVE,
                requirements=dict(kimi_cold_to_sleep_host_bytes=kimi["cold_to_sleep_peak_bytes"] + RESERVE,
                                  kimi_sleep_from_active_host_bytes=kimi["sleep_increment_peak_bytes"] + RESERVE,
                                  v4_load_from_kimi_sleep_host_bytes=v4_load_peak + RESERVE,
                                  v4_sleep_from_active_host_bytes=v4_sleep_peak + RESERVE,
                                  v4_backup_only_from_active_host_bytes=v4_backup + RESERVE,
                                  kimi_wake_existing_backup_host_floor_bytes=RESERVE),
                projections=dict(v4_awake_host_available_bytes=projected_v4_awake,
                                 v4_sleep_phase_admission_headroom_bytes=projected_v4_awake - (v4_sleep_peak + RESERVE),
                                 v4_backup_only_admission_headroom_bytes=projected_v4_awake - (v4_backup + RESERVE),
                                 host_available_both_sleeping_bytes=projected_v4_sleeping,
                                 host_reserve_headroom_both_sleeping_bytes=projected_v4_sleeping - RESERVE),
                hbm=dict(source=[source(path) for path in gate_paths], idle_free_mib_by_rank=idle_gpu,
                         kimi_post_sleep_used_mib_by_rank=kimi_residual, free_after_kimi_sleep_mib_by_rank=free_after_kimi,
                         v4_cold_required_mib_per_rank=cold_v4_required,
                         v4_cold_gate_headroom_mib_by_rank=v4_hbm_gate_margin,
                         minimum_v4_cold_gate_headroom_mib=min(v4_hbm_gate_margin),
                         v4_measured_cold_peak_mib_by_rank=v4_peak,
                         projected_v4_peak_headroom_mib_by_rank=v4_hbm_peak_margin,
                         minimum_projected_v4_peak_headroom_mib=min(v4_hbm_peak_margin),
                         kimi_wake_tagged_remap_required_mib_by_rank=kimi_wake),
                caveats=["Historical snapshot bounds are planning inputs. Recheck live host and per-rank HBM before each allocation.",
                         "Sleep interval uses command acknowledgement bounds. Incremental Sleep pressure starts at the last active sample.",
                         "A projected phase starts from the prior phase's observed or estimated available RAM. CPU backup payload is already included in measured Sleep pressure.",
                         "Cgroup shmem is a subset of file. Kimi historical samples lack a cgroup breakdown.",
                         "Kimi wake reuses its existing backup. The host floor does not estimate additional Kimi wake overhead.",
                         "HBM projections retain the historical V4 cold gate. The observed V4 peak is shown for comparison."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kimi_run", type=Path)
    parser.add_argument("v4_run", type=Path, nargs=3)
    args = parser.parse_args()
    print(json.dumps(derive(args.kimi_run, args.v4_run), indent=2))


if __name__ == "__main__":
    main()
