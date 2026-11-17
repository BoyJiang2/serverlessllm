import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def audit_layers(rows, arm):
    by_rank = defaultdict(list)
    for rec in rows:
        if rec.get("event") != "wna16_layer":
            continue
        assert rec["buffered"] > 0 and rec["start_ns"] <= rec["ns"]
        if arm == "candidate":
            assert not rec["during_finalize"], rec
            assert rec["loaded"] == rec["expected"], rec
        by_rank[rec["rank"]].append(rec)
    assert set(by_rank) == set(range(8))
    layers = set(item["layer"] for item in by_rank[0])
    assert len(layers) == 60
    summary = {}
    for rank, records in by_rank.items():
        assert len(records) == 60 and {item["layer"] for item in records} == layers
        summary[rank] = dict(Counter("finalize" if item["during_finalize"] else "load"
                                     for item in records))
    return summary


def memory(summary_path, timing):
    rows = [
        row for line in summary_path.read_text().splitlines()
        if (row := json.loads(line)).get("cgroup_memory", {}).get("stat")
    ]
    assert rows
    start, end = timing["reload_begin_ns"], timing["reload_end_ns"]
    windows = [row for row in rows if start <= row["ns"] <= end]
    assert windows
    first = min(rows, key=lambda row: abs(row["ns"]-start))
    last = min(rows, key=lambda row: abs(row["ns"]-end))
    def stat(row, key):
        return row["cgroup_memory"]["stat"][key]
    def peak(rows):
        value = max(rows, key=lambda row: stat(row, "anon"))
        return dict(anon_bytes=stat(value, "anon"),
                    memory_current_bytes=int(value["cgroup_memory"]["current"]),
                    ns=value["ns"])
    keys = first["cgroup_memory"]["stat"].keys() & last["cgroup_memory"]["stat"].keys()
    delta = (stat(last, "workingset_refault_file") - stat(first, "workingset_refault_file")
             if "workingset_refault_file" in keys else None)
    assert delta is None or delta >= 0
    return dict(sampled_anon_peak=peak(rows), reload_sampled_anon_peak=peak(windows),
                reload_samples=len(windows), pages=delta,
                refault_bytes=None if delta is None else delta * os.sysconf("SC_PAGE_SIZE"),
                endpoint_sample_offsets_ns=[first["ns"]-start, last["ns"]-end])


def main():
    run = Path(sys.argv[1])
    worker = run / "L2/worker-K"
    arm = read(run / "manifest.json")["arm"]
    assert arm in ("baseline", "candidate")
    lifecycle = json.loads(subprocess.check_output(
        [sys.executable, str(Path(__file__).with_name("audit_lifecycle.py")), str(run)]))
    for path in ("L2/worker-K", "L2/wake-admission"):
        assert read(run/path/"sf-admission/summary.json")["cache_condition_passed"]
    timing = read(worker/"wake-0002-timing.json")
    start, end, kv_begin, kv_end = (timing[k] for k in (
        "reload_begin_ns", "reload_end_ns", "kv_begin_ns", "kv_end_ns"))
    assert start < end <= kv_begin < kv_end
    events = []
    for line in (worker / "worker.log").read_text(errors="replace").splitlines():
        match = line.partition("KIMI_L2_TRACE ")[2]
        if match.startswith("{"):
            events.append(json.JSONDecoder().raw_decode(match)[0])
    print(json.dumps(dict(arm=arm, lifecycle=lifecycle, reload_seconds=(end-start)/1e9,
                          kv_wake_seconds=(kv_end-kv_begin)/1e9,
                          layer_completions=audit_layers(events,arm),
                          memory=memory(run/"resource-samples.jsonl",timing)), indent=2))


if __name__ == "__main__":
    main()
