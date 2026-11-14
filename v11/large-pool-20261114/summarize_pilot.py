"""Summarize the measured V11 pilot boundaries from per-run receipts."""
import argparse
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


def summarize(run):
    pools = load(run / "pool-init-timing.json")
    kv_init = seconds(pools["kv_begin_ns"], pools["kv_ready_ns"])
    store_init = seconds(pools["store_begin_ns"], pools["store_ready_ns"])
    summary = dict(host_kv_init_s=kv_init, weight_store_init_s=store_init,
                   init_sum_s=kv_init + store_init,
                   init_wall_s=seconds(pools["kv_begin_ns"], pools["store_ready_ns"]),
                   steps=[])
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
        summary["steps"].append(dict(
            step=step.name,
            engine_ready_ns=load(step / "ready.json")["ns"],
            first_correct_business_response_ns=min(business),
            full_eight_rank_weight_load_s=full_load,
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
        ))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), indent=2))
