"""Summarize the measured V11 pilot boundaries from per-run receipts."""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def seconds(begin, end):
    if end < begin:
        raise ValueError("event order reversed")
    return (end - begin) / 1e9


def docker_ns(line):
    stamp = line.split()[0]
    match = re.fullmatch(r"(.+)\.(\d{1,9})Z", stamp)
    if not match:
        raise ValueError(f"unexpected Docker timestamp: {stamp}")
    whole, fraction = match.groups()
    epoch = datetime.fromisoformat(whole).replace(tzinfo=timezone.utc)
    return int(epoch.timestamp()) * 10**9 + int(fraction.ljust(9, "0"))


def residency_metrics(receipt):
    phases = {}
    for phase in ("before_backend_exit", "immediately_after_backend_exit",
                  "after_exit_before_clear", "after_clear"):
        sample = receipt.get(phase)
        if sample is None:
            phases[phase] = None
            continue
        by_role = {}
        for role, processes in sample["processes"].items():
            pids = {row["pid"] for row in processes}
            apps = [entry.split(",") for entry in sample["compute_apps"]]
            by_role[role] = dict(
                process_hbm_mib=sum(int(mib) for pid, mib in apps if int(pid) in pids),
                pids=sorted(pids),
                rss_kib=sum(int(row["VmRSS"].split()[0]) for row in processes)
                if all("VmRSS" in row for row in processes) else None,
                pss_kib=sum(int(row["Pss"].split()[0]) for row in processes)
                if all("Pss" in row for row in processes) else None,
            )
        phases[phase] = dict(
            device_hbm_mib=sum(int(row.rsplit(",", 1)[1]) for row in sample["gpu"]),
            weight_hbm_mib=None, kv_hbm_mib=None, processes=by_role,
        )
    return phases


def store_callback_overlap(events, request, response):
    """Return a union of successful, request-bound transfer intervals."""
    if "received_wall_ns" not in response:
        return None
    assert response["response"]["id"] == request["request_id"]
    start, end = request["wall_ns"], response["received_wall_ns"]
    elapsed = response["received_ns"] - request["ns"]
    if not (0 <= elapsed and abs(end - start - elapsed) <= 1_000_000):
        return None
    pairs = {}
    for event in events:
        if event["request_id"] == request["request_id"]:
            key = (event["engine_id"], event["transfer_key"])
            if None in key or event["event"] in pairs.setdefault(key, {}):
                return None
            pairs[key][event["event"]] = event
    intervals = []
    for pair in pairs.values():
        begin, finish = (pair.get(kind) for kind in ("mp.store.start", "mp.store.end"))
        if begin is None or finish is None or not finish["stored_count"]:
            return None
        left, right = begin["event_wall_ns"], finish["event_wall_ns"]
        if not start <= left <= right <= finish["drain_wall_ns"]:
            return None
        if any(abs((event["drain_wall_ns"] - event["drain_monotonic_ns"]) -
                   (start - request["ns"])) > 1_000_000 for event in (begin, finish)):
            return None
        intervals.append((left, right))
    if not intervals:
        return None
    overlap_ns = 0
    cursor = start
    for left, right in sorted(intervals):
        clipped = min(end, right)
        overlap_ns += max(0, clipped - max(left, cursor))
        cursor = max(cursor, clipped)
    return dict(transfer_count=len(intervals), request_wall_begin_ns=start,
                request_wall_end_ns=end, transfer_intervals_wall_ns=sorted(intervals),
                overlap_s=overlap_ns / 1e9)


def summarize(run):
    pools = load(run / "pool-init-timing.json")
    assert pools["kv_ready_ns"] <= pools["store_begin_ns"]
    kv_init = seconds(pools["kv_begin_ns"], pools["kv_ready_ns"])
    store_init = seconds(pools["store_begin_ns"], pools["store_ready_ns"])
    admission = load(run / "admission.json")["checked_ns"]
    with (run / "switch-events.csv").open(newline="") as source:
        first = next(csv.DictReader(source), None)
    prep = None
    if first and int(first["step"]) == 0:
        first_activation = int(first["t2_ns"])
        assert admission <= pools["kv_begin_ns"] <= pools["store_ready_ns"] <= first_activation
        prep = dict(begin_ns=admission, end_ns=first_activation,
                    wall_s=seconds(admission, first_activation))
    summary = dict(host_kv_init_s=kv_init, weight_store_init_s=store_init,
                   init_sum_s=kv_init + store_init,
                   init_wall_s=seconds(pools["kv_begin_ns"], pools["store_ready_ns"]),
                   preparation_to_first_activation=prep,
                   steps=[])
    callback_path = run / "lmcache/store-events.jsonl"
    callbacks = ([json.loads(line) for line in callback_path.read_text().splitlines()]
                 if callback_path.is_file() else [])
    if callbacks:
        clock = load(run / "lmcache/store-trace-clock.json")
        assert clock["clock"] == "CLOCK_REALTIME"
    for commit_path in sorted(run.glob("*/kv-commit-timing.json")):
        step = commit_path.parent
        commit = load(commit_path)
        lines = commit["server_commit_log"]
        store_lines = (step / "store-events.log").read_text(encoding="utf-8").splitlines()
        lookups = [docker_ns(line) for line in store_lines if "SLLM_CACHE_LOOKUP" in line]
        completed = [docker_ns(line) for line in store_lines if "SLLM_CACHE_COMPLETE" in line]
        shortage = [docker_ns(line) for line in store_lines if "PinnedMemoryPool out of memory" in line]
        evicted = [docker_ns(line) for line in store_lines if " is freed from memory" in line]
        trace = [json.loads(line) for path in (step / "events").glob("*.jsonl")
                 for line in path.read_text(encoding="utf-8").splitlines()]
        begin = {row["rank"]: row["monotonic_ns"] for row in trace
                 if row["name"] == "full_weight_load_begin"}
        end = {row["rank"]: row["monotonic_ns"] for row in trace
               if row["name"] == "full_weight_load_end"}
        full_load = seconds(min(begin.values()), max(end.values())) if (
            set(begin) == set(end) == set(range(8))) else None
        business = []
        for path in step.glob("before-*-response.json"):
            if path.name == "before-kv_fill-response.json":
                continue
            check = path.with_name(path.name.replace("-response.json", "-checks.json"))
            if load(check)["passed"]:
                business.append(load(path)["received_ns"])
        residency = load(step / "residency.json")
        hbm_path = step / "backend-hbm-storage.json"
        storage = load(hbm_path) if hbm_path.is_file() else None
        if storage:
            ranks = storage["ranks"]
            before = residency["before_backend_exit"]
            assert len(ranks) == 8 and {row["rank"] for row in ranks} == set(range(8))
            assert {row["identity"]["pid"] for row in ranks} <= {
                row["pid"] for row in before["processes"][step.name]
            }
            assert storage["end_ns"] <= before["ns"]
            storage["capture_s"] = seconds(storage["begin_ns"], storage["end_ns"])
        callback_overlap = None
        if callbacks:
            worker_clock = load(step / "constructed.json")["clock_identity"]
            assert all(clock[k] == worker_clock[k] for k in ("boot_id", "time_namespace"))
            callback_overlap = store_callback_overlap(
                callbacks, commit["request"], load(step / "before-kv_fill-response.json"))
        kv_begin = [r for r in trace if r["name"] == "kv_cache_allocation_begin"]
        kv_end = [r for r in trace if r["name"] == "kv_cache_allocation_end"]
        kv_s = None
        if len(kv_begin) == len(kv_end) == 8:
            identity = lambda r: (r["rank"], r["pid"], r["process_start_ticks"], r["boot_id"],
                                  r["time_namespace"], r["clock_id"])
            starts = {identity(r): r["monotonic_ns"] for r in kv_begin}
            ends = {identity(r): r["monotonic_ns"] for r in kv_end}
            assert set(starts) == set(ends) and {k[0] for k in starts} == set(range(8))
            assert all(starts[key] <= ends[key] for key in starts)
            kv_s = seconds(min(starts.values()), max(ends.values()))
        summary["steps"].append(dict(
            step=step.name,
            engine_ready_ns=load(step / "ready.json")["ns"],
            first_correct_business_response_ns=min(business),
            full_eight_rank_weight_load_s=full_load,
            gpu_kv_allocation_eight_rank_s=kv_s,
            request_host_store_overlap=callback_overlap,
            # This envelope includes work between rank/path events. It is
            # measured on the Store host and excludes subsequent GPU work.
            store_lookup_to_complete_s=seconds(min(lookups), max(completed)) if lookups else None,
            eviction_event_envelope_s=seconds(min(shortage), max(evicted)) if shortage and evicted else None,
            eviction_and_host_restore_envelope_s=seconds(min(lookups + shortage), max(completed))
            if lookups and completed else None,
            # Docker log timestamps are wall-clock receipt observations.
            request_to_last_store_log_s=seconds(commit["request"]["wall_ns"],
                                                max(map(docker_ns, lines))) if lines else None,
            commit_log_count=len(lines),
            residency=residency,
            residency_metrics=residency_metrics(residency),
            backend_hbm_storage=storage,
        ))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), indent=2))
