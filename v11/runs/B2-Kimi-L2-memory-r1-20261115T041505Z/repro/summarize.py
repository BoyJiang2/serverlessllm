import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
worker = run / "L2/worker-K"
events = {}
decoder = json.JSONDecoder()
for line in (worker / "worker.log").read_text(errors="replace").splitlines():
    if "KIMI_L2_TRACE " in line:
        row, _ = decoder.raw_decode(line.split("KIMI_L2_TRACE ", 1)[1])
        rank = events.setdefault(row["rank"], {})
        assert row["event"] not in rank, row
        rank[row["event"]] = row

intervals = {
    "reload": ("reload_begin", "reload_end"),
    "initialize": ("initialize_layerwise_reload_begin", "initialize_layerwise_reload_end"),
    "load": ("load_weights_begin", "load_weights_end"),
    "finalize": ("finalize_layerwise_reload_begin", "finalize_layerwise_reload_end"),
}
phases = {}
for name, (start, end) in intervals.items():
    complete = {rank: rows for rank, rows in events.items() if start in rows and end in rows}
    secs = {rank: (rows[end]["ns"] - rows[start]["ns"]) / 1e9
            for rank, rows in sorted(complete.items())}
    assert all(value >= 0 for value in secs.values())
    all_ranks = set(complete) == set(range(8))
    phases[name] = {
        "rank_seconds": secs,
        "max_rank_seconds": max(secs.values()) if all_ranks else None,
        "all_rank_envelope_seconds": (
            (max(row[end]["ns"] for row in complete.values())
             - min(row[start]["ns"] for row in complete.values())) / 1e9
            if all_ranks else None
        ),
    }

print(json.dumps({
    "run": run.name,
    "phases": phases,
    "rank_nested_host_totals": {rank: rows["reload_end"]["totals"]
                                for rank, rows in events.items() if "reload_end" in rows},
    "note": "Nested host-call durations overlap. GPU completion is not measured.",
}, indent=2))
