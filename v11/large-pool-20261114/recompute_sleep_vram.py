"""Recompute the observed Sleep L2 wake memory and the S3 admission failure."""
import argparse
import csv
import json
import math
from pathlib import Path
import re

from recompute_sleep import allocator_span, read

MIB = 2**20
HEADROOM_MIB = 8192  # v11_run_b2.py's L2 VRAM gate
RANKS = range(8)


def source(run, path):
    return path.relative_to(run).as_posix()


def snapshot_path(run, step, model):
    return run / "L2" / f"L2-{step:02d}-{model}-post-sleep-residency.json"


def snapshot(run, path):
    row = read(path)
    owner = {p["pid"]: model for model, processes in row["processes"].items() for p in processes}
    hbm = {model: {} for model in row["processes"]}
    for line in row["compute_apps"]:
        pid, used = map(int, line.split(","))
        if pid in owner:
            hbm[owner[pid]][pid] = used
    return dict(source=source(run, path), ns=row["ns"],
                used_mib=[int(line.rsplit(",", 1)[-1]) for line in row["gpu"]],
                model_pid_hbm_mib=hbm)


def admission(run, samples, step, model, previous_model):
    path = run / "L2" / f"{step:02d}-{model}-vram-gate.json"
    gate = read(path)
    previous = snapshot(run, snapshot_path(run, step - 1, previous_model))
    tail = []
    previous_phase = f"L2-{step-1:02d}-{previous_model}"
    for row in reversed(samples):
        if row["phase"] != previous_phase:
            continue
        if [int(row[f"gpu{i}"]) for i in RANKS] != previous["used_mib"]:
            break
        tail.append(int(row["ns"]))
    assert tail, path
    first = next((row for row in samples if row["phase"] == f"L2-{step:02d}-{model}"), None)
    assert len(gate["free_mib"]) == len(previous["used_mib"]) == len(RANKS)
    free_plus_used = [free + used for free, used in zip(gate["free_mib"], previous["used_mib"])]
    return dict(source=source(run, path), **gate, preceding_sleep=previous,
                free_plus_preceding_used_mib=free_plus_used, stable_tail_samples=len(tail),
                stable_tail_s=(tail[0] - tail[-1]) / 1e9,
                preceding_sleep_sample_to_next_phase_sample_s=(
                    (int(first["ns"]) - previous["ns"]) / 1e9 if first else None
                ), first_phase_sample_used_mib=[int(first[f"gpu{i}"]) for i in RANKS] if first else None)


def ranks(path):
    receipt = read(path)
    rows = receipt.get("receipt", receipt)["ranks"]
    indexed = {row["rank"]: row for row in rows}
    assert sorted(indexed) == list(RANKS) and len(rows) == len(indexed), path
    return indexed


def wake_memory(run, samples, record, previous_model, prior_same_model):
    step, model = record["step"], record["model"]
    stage = record["stage"]
    worker = run / "L2" / f"worker-{model}"
    phases_path = worker / stage["phases_receipt"]
    phases = read(phases_path)
    wake_index = int(phases_path.name.split("-")[1])
    prior_sleep_index = int(prior_same_model["sleep_phases_receipt"].split("-")[1])
    sleep_index = int(record["sleep_phases_receipt"].split("-")[1])
    endpoints = {
        "prior_sleep": worker / f"ack-{prior_sleep_index:04d}.json",
        "weights_mapped": worker / f"wake-{wake_index:04d}-weights-mapped.json",
        "weights_reloaded": worker / f"wake-{wake_index:04d}-weights-reloaded.json",
        "resleep": worker / f"ack-{sleep_index:04d}.json",
    }
    measurements = {phase: ranks(path) for phase, path in endpoints.items()}
    baseline = measurements["prior_sleep"]
    allocations = {}
    for tag, begin, end in (
        ("weights", "weight_map_begin_ns", "weight_map_end_ns"),
        ("kv_cache", "kv_wake_begin_ns", "kv_ready_ns"),
    ):
        mapping = allocator_span(worker / "worker.log", "gpu_create_map", tag, phases[begin], phases[end])
        assert not mapping["missing_rank_pairs"], phases_path
        allocations[tag] = mapping["rows"]
    rank_rows = []
    for rank in RANKS:
        original = baseline[rank]
        for rows in measurements.values():
            assert all(rows[rank][key] == original[key] for key in (
                "identity", "device_ordinal", "device_uuid", "allocation_signature", "parameter_signature", "tags",
            )), phases_path
        for tag, rows in allocations.items():
            ranks_mapped = [row for row in rows if row["rank"] == rank]
            assert len(ranks_mapped) == original["tags"][tag]["allocations"] and (
                sum(row["bytes"] for row in ranks_mapped) == original["tags"][tag]["bytes"]
            ), phases_path
        rank_rows.append(dict(rank=rank, pid=original["identity"]["pid"], tags=original["tags"],
                              torch_deltas_bytes={
                                  phase: {key: rows[rank][key] - original[key] for key in (
                                      "cuda_allocated_bytes", "cuda_reserved_bytes",
                                  )} for phase, rows in measurements.items()
                              }))
    gate = admission(run, samples, step, model, previous_model)
    phase_samples = [row for row in samples if row["phase"] == f"L2-{step:02d}-{model}"]
    through_reload = [
        row for row in phase_samples
        if phases["weight_map_begin_ns"] <= int(row["ns"]) <= phases["checkpoint_reload_end_ns"]
    ]
    peak = [max(int(row[f"gpu{i}"]) for row in phase_samples) for i in RANKS]
    peak_reload = [max(int(row[f"gpu{i}"]) for row in through_reload) for i in RANKS]
    used = gate["preceding_sleep"]["used_mib"]
    return dict(stage=step + 1, model=model, wake_command=wake_index, gate=gate,
                phases_source=source(run, phases_path), allocator_source=source(run, worker / "worker.log"),
                torch_sources={phase: source(run, path) for phase, path in endpoints.items()},
                rank_rows=rank_rows, phase_samples=len(phase_samples), reload_samples=len(through_reload),
                sampled_peak_mib=peak, sampled_peak_increment_mib=[p - x for p, x in zip(peak, used)],
                sampled_reload_peak_increment_mib=[p - x for p, x in zip(peak_reload, used)])


def collect(run):
    qualification = read(run / "qualification.json")
    assert qualification["levels"] == [2]
    with (run / "resource-samples.csv").open(newline="") as stream:
        samples = list(csv.DictReader(stream))
    records = [row for result in qualification["results"] for row in result["records"]]
    prior_records = {}
    wakes = []
    for record in records:
        step, model = record["step"], record["model"]
        if model in ("D", "G") and record["stage"]["kind"] == "wake":
            wakes.append(wake_memory(run, samples, record, records[step - 1]["model"], prior_records[model]))
        prior_records[model] = record
    failed_v4_gate = None
    if len(records) < len(qualification["models"]):
        step = len(records)
        model = qualification["models"][step]
        path = run / "L2" / f"{step:02d}-{model}-vram-gate.json"
        if step and model == "D" and path.is_file() and not read(path)["passed"]:
            gate = admission(run, samples, step, model, records[step - 1]["model"])
            failed_v4_gate = dict(stage=step + 1, **gate)
            if v4_wakes := [wake for wake in wakes if wake["model"] == "D"]:
                prior = v4_wakes[-1]
                assert gate["free_plus_preceding_used_mib"] == prior["gate"]["free_plus_preceding_used_mib"]
                projection = cap_projection(run, gate, prior)
                failed_v4_gate.update(prior_v4_wake_stage=prior["stage"], cap_projection=projection)
    return dict(run_id=run.name, sequence=qualification["sequence"], rep=qualification["rep"],
                passed=qualification["passed"], wakes=wakes, failed_v4_gate=failed_v4_gate)


def cap_projection(run, gate, wake):
    worker = run / "L2" / "worker-D"
    config = read(run / "b2-inputs/v4-input.json")["config"]
    messages = (worker / "worker.log").read_text(errors="replace")
    token_counts = set(re.findall(r"GPU KV cache size: ([\d,]+) tokens", messages))
    (tokens,) = [int(value.replace(",", "")) for value in token_counts]
    cap = config["kv_cache_memory_bytes"]
    assert cap == 16 * 2**30
    kv_mib = [row["tags"]["kv_cache"]["bytes"] / MIB for row in wake["rank_rows"]]
    weight_mib = [row["tags"]["weights"]["bytes"] / MIB for row in wake["rank_rows"]]
    non_kv = [peak - kv for peak, kv in zip(wake["sampled_peak_increment_mib"], kv_mib)]
    mapped_required = [math.ceil(weight + kv) + HEADROOM_MIB for weight, kv in zip(weight_mib, kv_mib)]
    observed_free = gate["free_mib"]
    scenarios = []
    for gib in (12, 10, 8, 6, 4, 2):
        spare = [round(free - peak - gib * 1024, 3) for free, peak in zip(observed_free, non_kv)]
        scenarios.append(dict(configured_v4_kv_gib=gib, projected_min_spare_mib=min(spare),
                              preserves_8192_mib_after_sampled_peak=min(spare) >= HEADROOM_MIB,
                              approximate_logical_tokens=tokens * gib // 16))
    return dict(status="changed_configuration_projection_not_measured", headroom_mib=HEADROOM_MIB,
                model_config_source=source(run, run / "b2-inputs/v4-input.json"),
                source_wake_stage=wake["stage"], source_kv_tokens=tokens,
                max_model_len=config["max_model_len"], max_num_seqs=config["max_num_seqs"],
                original_mapped_required_mib=mapped_required,
                original_min_mapped_margin_mib=min(free - need for free, need in zip(observed_free, mapped_required)),
                original_min_sampled_peak_margin_mib=min(
                    free - peak for free, peak in zip(observed_free, wake["sampled_peak_increment_mib"])
                ), assumed_non_kv_peak_mib=non_kv, scenarios=scenarios)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    args = parser.parse_args()
    print(json.dumps([collect(q.parent) for q in sorted(args.runs.glob("*/qualification.json"))], indent=2))
