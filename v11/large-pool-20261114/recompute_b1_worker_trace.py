"""Recompute the pinned B1 worker trace from one stage or one run directory."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import re


PHASES = {
    "b1_weight_ready_storage": ("weight_ready", "ready_ns", (
        "parameter_storage_bytes", "buffer_storage_bytes", "parameter_and_buffer_storage_bytes",
    )),
    "b1_kv_budget": ("kv_budget", "monotonic_ns", ("requested_bytes", "available_bytes", "layout_bytes")),
    "b1_kv_tensor_init": ("kv_tensor_init", None, ()),
    "b1_kv_initialized_storage": ("kv_initialized_storage", "initialized_ns", ("runner_kv_storage_bytes",)),
    "b1_lmcache_register_transfer": ("initial_registration", None, ()),
    "b1_kv_worker_ready": ("kv_worker_ready", "ready_ns", ()),
    "b1_kv_reference_cleanup": ("kv_reference_cleanup", None, ("runner_kv_storage_bytes_before",)),
}
STAGE_NAME = re.compile(r"^\d{2}-[KGD]-")
RANKS = set(range(8))
CLOCK_FIELDS = ("container_hostname", "boot_id", "time_namespace", "clock_id")
PROCESS_FIELDS = ("pid", "process_start_ticks", "pid_namespace")
PHYSICAL_FIELDS = ("physical_release_begin_ns", "physical_release_end_ns", "physical_release_bytes")


def positive(value):
    return type(value) is int and value > 0


def event_bounds(row, point):
    if point is None:
        return row.get("begin_ns"), row.get("end_ns")
    return row.get(point), row.get(point)


def stage_markers(stage, problems):
    markers = {}
    for key, filename in (
        ("constructed", "constructed.json"), ("backend_ready", "ready.json"),
        ("shutdown_begin", "backend-shutdown-begin.json"), ("shutdown_end", "normal-backend-shutdown.json"),
    ):
        path = stage / filename
        ns = None
        if path.is_file():
            try:
                ns = json.loads(path.read_bytes())["ns"]
                if not positive(ns):
                    raise ValueError("invalid monotonic timestamp")
            except (ValueError, KeyError) as error:
                problems.append(f"{filename}: {error}")
                ns = None
        markers[key] = dict(source=filename, ns=ns)
    return markers


def read_worker_events(stage, problems):
    run_id = f"{stage.parent.name}-{stage.name}"
    selected, other_attempts = [], []
    files = sorted((stage / "events").glob("*.jsonl"))
    if not files:
        problems.append("events/*.jsonl unavailable")
    for path in files:
        for line, raw in enumerate(path.read_bytes().splitlines(), 1):
            source = f"events/{path.name}:{line}"
            try:
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError("event is not an object")
            except ValueError as error:
                problems.append(f"{source}: {error}")
                continue
            if row.get("name") not in PHASES:
                continue
            row.update(source=source)
            if (row["name"] == "b1_lmcache_register_transfer"
                    and row.get("initial_registration") is False and positive(row.get("attempt"))
                    and row["attempt"] > 1):
                other_attempts.append(row)
            else:
                selected.append(row)
    return run_id, selected, other_attempts


def has_identity(row, run_id):
    return (row.get("run_id") == run_id and row.get("trace_schema") == 1
            and row.get("clock_id") == "CLOCK_MONOTONIC"
            and all(isinstance(row.get(key), str) and row[key]
                    for key in (*CLOCK_FIELDS, "pid_namespace"))
            and all(positive(row.get(key)) for key in ("pid", "process_start_ticks", "monotonic_ns")))


def validate_event(row, phases, bounds, problems):
    name = row["name"]
    phase, point, bytes_fields = PHASES[name]
    start, end = event_bounds(row, point)
    rank = row["rank"]
    error = lambda message: problems[phase].append(f"rank {rank} ({row['source']}): {message}")
    if not (positive(start) and positive(end) and start <= end <= row["monotonic_ns"]):
        error("invalid host phase timestamps")
        return
    shutdown = phase == "kv_reference_cleanup"
    lower, upper = ((bounds["shutdown_begin"], bounds["shutdown_end"]) if shutdown else
                    (bounds["constructed"], bounds["backend_ready"]))
    if (lower is not None and start < lower) or (upper is not None and end > upper):
        error("outside its activation or shutdown bounds")
    if point is None and row.get("status") != "ok":
        error(f"worker operation {row.get('status')!r}")
    for key in bytes_fields:
        value = row.get(key)
        if key in ("requested_bytes", "available_bytes") and key in row and value is None:
            continue
        if (type(value) is not int or value < 0
                or (key not in ("buffer_storage_bytes", "runner_kv_storage_bytes_before") and value == 0)):
            error(f"invalid {key}")
    if phase == "weight_ready":
        param = row.get("parameter_storage_bytes")
        buffer = row.get("buffer_storage_bytes")
        combined = row.get("parameter_and_buffer_storage_bytes")
        sampled = row.get("storage_sample_end_ns")
        if not (all(type(value) is int for value in (param, buffer, combined))
                and max(param, buffer) <= combined <= param + buffer):
            error("weight storage union does not reconcile")
        if not positive(sampled) or not start <= sampled <= row["monotonic_ns"]:
            error("invalid weight storage sampling bounds")
    elif phase == "kv_budget":
        util = row.get("gpu_memory_utilization")
        if not positive(row.get("num_blocks")) or type(util) not in (int, float) or not 0 < util <= 1:
            error("invalid block count or GPU memory utilization")
    elif phase == "initial_registration":
        instance = row.get("instance_id")
        if (type(row.get("attempt")) is not int or row["attempt"] != 1
                or row.get("initial_registration") is not True
                or type(instance) not in (int, str) or instance in ("", None)):
            error("initial registration identity missing")
    elif shutdown and any(key not in row or row[key] is not None for key in PHYSICAL_FIELDS):
        error("pinned cleanup trace cannot independently attribute physical KV release")

    def single(edge):
        rows = phases[edge].get(rank, [])
        return rows[0] if len(rows) == 1 else {}

    tensor_init = single("kv_tensor_init")
    registration = single("initial_registration")
    if phase == "kv_initialized_storage" and positive(tensor_init.get("end_ns")) and start != tensor_init["end_ns"]:
        error("storage timestamp differs from the same rank's tensor initialization return")
    if phase == "initial_registration" and positive(tensor_init.get("end_ns")) and start < tensor_init["end_ns"]:
        error("registration started before the same rank's tensor initialization returned")
    if phase == "kv_worker_ready" and any(positive(edge) and start < edge for edge in (
        tensor_init.get("end_ns"), registration.get("end_ns"),
    )):
        error("worker-ready preceded a required same-rank operation")


def recompute_stage(stage):
    problems = []
    markers = stage_markers(stage, problems)
    bounds = {key: value["ns"] for key, value in markers.items()}
    run_id, rows, other_attempts = read_worker_events(stage, problems)
    phases = {phase: defaultdict(list) for phase, _, _ in PHASES.values()}
    issues = defaultdict(list)
    clocks, process_by_rank = set(), defaultdict(set)
    for row in rows:
        phase = PHASES[row["name"]][0]
        rank = row.get("rank")
        if type(rank) is not int or rank not in RANKS:
            issues[phase].append(f"{row['source']}: invalid rank")
            continue
        phases[phase][rank].append(row)
        if not has_identity(row, run_id):
            issues[phase].append(f"{row['source']}: invalid or mismatched run, clock or process identity")
            continue
        clocks.add(tuple(row[key] for key in CLOCK_FIELDS))
        process_by_rank[rank].add(tuple(row[key] for key in PROCESS_FIELDS))
    shared_processes = {next(iter(identities)) for identities in process_by_rank.values() if len(identities) == 1}
    identity_ok = (len(clocks) == 1 and all(len(identities) == 1 for identities in process_by_rank.values())
                   and len(shared_processes) == len(process_by_rank))
    if rows and not identity_ok:
        problems.append("events do not share one host clock and stable, distinct processes per rank")
    # Check order only after events from all files have been indexed.
    for phase, rank_rows in phases.items():
        for rank, rank_events in rank_rows.items():
            if len(rank_events) > 1:
                issues[phase].append(f"rank {rank}: duplicate event")
            for row in rank_events:
                if has_identity(row, run_id):
                    validate_event(row, phases, bounds, issues)
    instances = [row["instance_id"] for rank_rows in phases["initial_registration"].values()
                 for row in rank_rows if type(row.get("instance_id")) in (str, int)]
    if len(instances) != len(set(instances)):
        issues["initial_registration"].append("instance_id repeated across initial registrations")

    summaries = {}
    for phase, point, fields in PHASES.values():
        selected = phases[phase]
        missing = sorted(RANKS - set(selected))
        phase_issues = issues[phase]
        events = [row for rank in sorted(selected) for row in selected[rank]]
        valid = not (missing or phase_issues or problems or not identity_ok)
        edges = [event_bounds(row, point) for row in events] if valid else []
        begin = min((edge[0] for edge in edges), default=None)
        end = max((edge[1] for edge in edges), default=None)
        totals = {key: (sum(row[key] for row in events)
                        if valid and all(row[key] is not None for row in events) else None) for key in fields}
        summaries[phase] = dict(status="complete" if valid else "incomplete", missing_ranks=missing,
                                observed_ranks=sorted(selected), issues=phase_issues, records=events,
                                kind="interval" if point is None else "point_window",
                                begin_ns=begin, end_ns=end,
                                wall_span_ns=end - begin if valid else None, bytes_total=totals)
    ready_phases = ("kv_tensor_init", "kv_initialized_storage", "initial_registration", "kv_worker_ready")
    ready = max(summaries[name]["end_ns"] for name in ready_phases) if all(
        summaries[name]["status"] == "complete" for name in ready_phases
    ) else None
    return dict(step=stage.name, run_id=run_id, markers=markers, trace_problems=problems,
                clock_identity=dict(zip(CLOCK_FIELDS, next(iter(clocks)))) if identity_ok else None,
                phases=summaries, noninitial_registration_attempts=other_attempts,
                new_kv_ready_ns=ready, physical_kv_release={key: None for key in PHYSICAL_FIELDS},
                kv_storage_bytes_by_group=None,
                kv_storage_bytes_by_group_reason="pinned events contain rank storage totals only, without group or shared-storage attribution")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Directory of one stage or the complete run")
    args = parser.parse_args()
    if not args.path.is_dir():
        parser.error("input must be a stage or run directory")
    stages = ([args.path] if STAGE_NAME.match(args.path.name) else
              sorted(path for path in args.path.iterdir() if path.is_dir() and STAGE_NAME.match(path.name)))
    if not stages:
        parser.error("no stage directories found")
    print(json.dumps(dict(run_id=stages[0].parent.name, clock="worker_host_CLOCK_MONOTONIC",
                          stage_bounds="Backend construction/ready, and shutdown call begin/return when recorded",
                          stages=[recompute_stage(stage) for stage in stages]), indent=2))


if __name__ == "__main__":
    main()
