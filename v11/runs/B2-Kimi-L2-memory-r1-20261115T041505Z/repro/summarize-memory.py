import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
samples = [json.loads(line) for line in (run / "resource-samples.jsonl").read_text().splitlines()]
groups = {}
for row in samples:
    if "cgroup_memory" in row:
        groups.setdefault(row["phase"], []).append(row)
summary = {}
for phase, rows in groups.items():
    peak = max(rows, key=lambda row: int(row["cgroup_memory"]["current"]))
    mem = peak["cgroup_memory"]
    proc_peaks = {}
    for row in rows:
        for pid, status in row["cgroup_processes"].items():
            if "VmRSS" in status:
                proc_peaks[pid] = max(proc_peaks.get(pid, 0), int(status["VmRSS"].split()[0]) * 1024)
    summary[phase] = dict(
        samples=len(rows), peak_sample_wall_ns=peak["wall_ns"],
        sampled_peak_bytes=int(mem["current"]), memory_max_bytes=int(mem["max"]),
        memory_stat_at_peak=mem["stat"], process_status_at_peak=peak["cgroup_processes"],
        sampled_process_rss_peak_bytes=proc_peaks,
        min_host_available_bytes=min(row["mem_available"] for row in rows),
        last_memory_events=rows[-1]["cgroup_memory"]["events"],
    )
print(json.dumps({"run": run.name, "phase_memory": summary}, indent=2))
