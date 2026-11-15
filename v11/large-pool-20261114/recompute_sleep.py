"""Read native Sleep phase receipts and summarize qualified repetitions."""
import argparse
import csv
import json
from pathlib import Path
import re

from recompute_readiness import statistics


ALLOCATOR = re.compile(
    r"sleep_allocator phase=(\w+) tag=(\w+) rank=(\d+) bytes=(\d+) start_ns=(\d+) end_ns=(\d+)"
)


def read(path):
    return json.loads(path.read_bytes())


def allocator_span(path, operation, tag, lower, upper):
    rows = []
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        match = ALLOCATOR.search(line)
        if not match or match.groups()[:2] != (operation, tag):
            continue
        rank, size, begin, end = map(int, match.groups()[2:])
        if lower <= begin < end <= upper:
            rows.append(dict(rank=rank, bytes=size, begin_ns=begin, end_ns=end, line=number))
    return span(rows)


def span(rows):
    missing = sorted(set(range(8)) - {row["rank"] for row in rows})
    complete = not missing
    rank_seconds = {}
    for rank in sorted({row["rank"] for row in rows}):
        receipts = [row for row in rows if row["rank"] == rank]
        rank_seconds[rank] = seconds(min(row["begin_ns"] for row in receipts),
                                     max(row["end_ns"] for row in receipts))
    return dict(rows=rows, begin_ns=min(row["begin_ns"] for row in rows) if complete else None,
                end_ns=max(row["end_ns"] for row in rows) if complete else None,
                rank_envelope_s=rank_seconds,
                max_rank_envelope_s=max(rank_seconds.values()) if complete else None,
                missing_rank_pairs=missing)


def load_span(worker, phase, run_id, ready):
    waiting = read(worker / "waiting.json")["ns"]
    rows = []
    coverage = {"begin": set(), "end": set()}
    for path in sorted((worker / "events").glob("*.jsonl")):
        events = [json.loads(line) for line in path.read_bytes().splitlines()]
        for edge, ranks in coverage.items():
            ranks.update(event["rank"] for event in events if event["name"] == phase + "_" + edge)
        for x in events:
            if x["name"] != phase + "_begin":
                continue
            ends = [y for y in events if y["name"] == phase + "_end" and y["rank"] == x["rank"]]
            if not ends:
                continue
            y, = ends
            assert x["run_id"] == y["run_id"] == run_id
            assert all(x[k] == y[k] for k in ("pid", "process_start_ticks", "boot_id",
                                              "time_namespace", "clock_id"))
            assert waiting <= x["monotonic_ns"] < y["monotonic_ns"] <= ready
            rows.append(dict(rank=x["rank"], begin_ns=x["monotonic_ns"], end_ns=y["monotonic_ns"],
                             source=str(path)))
    assert len(rows) == len({row["rank"] for row in rows})
    return dict(span(rows), missing_begin_ranks=sorted(set(range(8)) - coverage["begin"]),
                missing_end_ranks=sorted(set(range(8)) - coverage["end"]))


def seconds(begin, end):
    return (end - begin) / 1e9 if begin is not None and end is not None else None


def post_sleep_residency(path):
    if not path.is_file():
        return None
    sample = read(path)
    gpu_mib = [int(row.split(",")[2]) for row in sample["gpu"]]
    assert len(gpu_mib) == 8
    memory_kib = {}
    for line in sample["host_memory"].splitlines():
        field, _, value = line.partition(":")
        if field in ("MemAvailable", "AnonPages", "Cached"):
            amount, unit = value.split()
            assert unit == "kB"
            memory_kib[field] = int(amount)
    return dict(source=str(path), ns=sample["ns"], gpu_used_mib=gpu_mib,
                compute_app_count=len(sample["compute_apps"]), host_memory_kib=memory_kib,
                processes=sample["processes"])


def final_release(run, qualification):
    path = run / "resource-samples.csv"
    if not path.is_file():
        return None
    with path.open(newline="") as stream:
        samples = [row for row in csv.DictReader(stream) if row["phase"] == "final-release"]
    if not samples:
        return None
    timestamps = [int(row["ns"]) for row in samples]
    assert all(a < b for a, b in zip(timestamps, timestamps[1:]))
    elapsed = seconds(timestamps[0], timestamps[-1])
    all_zero = all(int(row["compute_apps"]) == 0 and
                   all(int(row[f"gpu{i}"]) == 0 for i in range(8)) for row in samples)
    assert round(elapsed, 3) == qualification["final_release_seconds"]
    assert all_zero == qualification["final_all_gpu_zero"]
    return dict(source=str(path), samples=len(samples), elapsed_s=elapsed, all_gpu_zero=all_zero,
                meets_60s=elapsed >= 60, last_host_available_bytes=int(samples[-1]["mem_available"]))


def collect(run, result):
    level = run / f"L{result['level']}"
    old = None
    steps = []
    for record in result["records"]:
        worker = level / f"worker-{record['model']}"
        stage = record["stage"]
        ready = stage["ready_ns"]
        wake = read(worker / stage["phases_receipt"]) if stage["kind"] == "wake" else None
        if wake:
            weight_begin = wake["weight_map_begin_ns"]
            weight_ready = wake["checkpoint_reload_end_ns"]
            kv = allocator_span(worker / "worker.log", "gpu_create_map", "kv_cache",
                                wake["kv_wake_begin_ns"], wake["kv_ready_ns"])
            kv_ready = wake["kv_ready_ns"] if kv["end_ns"] is not None else None
            weight = None
        else:
            weight = load_span(worker, "full_weight_load", run.name, ready)
            weight_begin, weight_ready = weight["begin_ns"], weight["end_ns"]
            kv = load_span(worker, "kv_cache_allocation", run.name, ready)
            kv_ready = kv["end_ns"]
        sleep = read(worker / record["sleep_phases_receipt"])
        release = {tag: allocator_span(worker / "worker.log", "gpu_unmap_release", tag,
                                       sleep["sleep_begin_ns"], sleep["sleep_end_ns"])
                   for tag in ("weights", "kv_cache")}
        phase = f"{level.name}-{record['step']:02d}-{record['model']}"
        steps.append(dict(step=record["step"], model=record["model"], stage=stage["kind"],
                          service_ready_ns=ready, weight_begin_ns=weight_begin,
                          weight_ready_ns=weight_ready, kv_ready_ns=kv_ready,
                          load_or_wake_weight_s=seconds(weight_begin, weight_ready),
                          kv_allocation_s=seconds(kv["begin_ns"], kv["end_ns"]),
                          gpu_kv=kv, full_weight_load=weight, sleep_release=release, wake_phases=wake,
                          kv_switch_s=seconds(old["kv_cache"]["begin_ns"], kv_ready) if old else None,
                          kv_release_to_service_ready_s=seconds(old["kv_cache"]["begin_ns"], ready)
                          if old else None,
                          weight_switch_s=seconds(old["weights"]["begin_ns"], weight_ready) if old else None,
                          post_sleep_residency=post_sleep_residency(level / f"{phase}-post-sleep-residency.json"),
                          sleep_cpu_weight_backup_bytes=record.get("weights_cpu_backup_bytes"),
                          host_kv="not_applicable"))
        old = release
    return steps


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path, help="instrumented Sleep campaign runs directory")
    args = parser.parse_args()
    rounds, groups = [], {}
    for receipt in sorted(args.runs.glob("*/qualification.json")):
        q = read(receipt)
        run = receipt.parent
        release = final_release(run, q)
        if not q["results"]:
            rounds.append(dict(run=run.name, level=q["levels"][0], sequence=q["sequence"],
                               rep=q["rep"], passed=False, failures=q["failures"], steps=[],
                               final_release=release))
        for result in q["results"]:
            row = dict(run=run.name, level=result["level"], sequence=q["sequence"], rep=q["rep"],
                       passed=q["passed"], failures=q["failures"], steps=collect(run, result),
                       final_release=release)
            rounds.append(row)
            if row["passed"]:
                key = f"L{row['level']}-{row['sequence']}"
                for step in row["steps"]:
                    groups.setdefault(f"{key}-{step['step']:02d}", []).append(step)
    fields = ("load_or_wake_weight_s", "kv_allocation_s", "kv_switch_s", "weight_switch_s",
              "kv_release_to_service_ready_s")
    summary = {key: {field: statistics([row[field] for row in rows]) for field in fields}
               for key, rows in groups.items()}
    print(json.dumps(dict(rounds=rounds, summary=summary), indent=2))
