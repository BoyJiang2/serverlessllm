"""Validate and summarize native B1 Store phase receipts for a run or stage."""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import shlex


MARKERS = {
    "STORE_LOOKUP": ("lookup", "path monotonic_ns"),
    "PREAD": ("pread", "path partition file direct_open expected_file_bytes begin_ns end_ns "
               "call_sum_ns calls short_calls error_calls clock_errors requested_bytes transferred_bytes "
               "read_error cancelled"),
    "H2D": ("h2d", "path replica device begin_ns end_ns call_sum_ns calls error_calls clock_errors "
             "requested_bytes transferred_bytes rc"),
    "RECOVERY": ("recovery", "path replica begin_ns end_ns rc"),
    "EVICT": ("eviction", "path begin_ns end_ns freed_chunks freed_bytes"),
}
TEXT_FIELDS = {"path", "file", "replica"}
IO_PHASES = {"pread", "h2d"}
CHUNK_BYTES = 32 * 2**20
LEGACY_LOOKUP = re.compile(r"\] SLLM_CACHE_LOOKUP path=(\S+) resident=([01]) bytes=(\d+)$")
CLIENT_REQUEST = re.compile(
    r"\(Worker_TP(\d+) pid=(\d+)\).*?client\.py:93\] load_into_gpu: (\S+), (\S+)$"
)
STAGE_NAME = re.compile(r"^\d{2}-[KGD]-")


def parse_native(source):
    records, errors = defaultdict(list), defaultdict(list)
    if not source.is_file():
        return records, errors
    for line, raw in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        _, found, body = raw.partition("SLLM_B1_")
        marker, _, payload = body.partition(" ")
        if not found or marker not in MARKERS:
            errors["source"].append(f"line {line}: unsupported timing marker")
            continue
        phase, keys = MARKERS[marker]
        try:
            tokens = [token.split("=", 1) for token in shlex.split(payload)]
            fields = dict(tokens)
            if len(tokens) != len(fields) or set(fields) != set(keys.split()):
                raise ValueError("unexpected, duplicate, or missing fields")
            record = {key: value if key in TEXT_FIELDS else int(value) for key, value in fields.items()}
            if any(value == "" or (isinstance(value, int) and value < 0 and key != "rc")
                   for key, value in record.items()):
                raise ValueError("invalid value")
        except ValueError as exc:
            errors[phase].append(f"line {line}: {exc}")
            continue
        records[phase].append(dict(line=line, **record))
    return records, errors


def span_ok(row):
    return 0 < row["begin_ns"] <= row["end_ns"]


def io_ok(row):
    return (span_ok(row) and row["calls"] > 0
            and row["call_sum_ns"] <= row["calls"] * (row["end_ns"] - row["begin_ns"])
            and not row["clock_errors"] and not row["error_calls"]
            and row["transferred_bytes"] <= row["requested_bytes"])


def expected_sizes(source, cache):
    sizes, hits = {}, set()
    for raw in source.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := LEGACY_LOOKUP.search(raw):
            path, hit, size = match.groups()
            if path in sizes:
                raise ValueError(f"duplicate Store ledger lookup: {path}")
            sizes[path] = int(size)
            if hit == "1":
                hits.add(path)
    if (hits != set(cache["hit_paths"]) or set(sizes) != hits | set(cache["miss_paths"])
            or sum(sizes[path] for path in hits) != cache["hit_bytes"]
            or sum(sizes[path] for path in cache["miss_paths"]) != cache["file_read_bytes"]):
        raise ValueError("Store ledger lookup paths or bytes disagree with store-step.json")
    return sizes


def replica_identity(source, paths):
    by_uuid = {}
    for line, raw in enumerate(source.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if match := CLIENT_REQUEST.search(raw):
            rank, pid, path, replica = match.groups()
            if replica in by_uuid:
                raise ValueError(f"duplicate backend replica request: {replica}")
            if path.rsplit("/", 1)[-1] != f"rank_{rank}":
                raise ValueError(f"backend rank and Store path disagree: {path}")
            by_uuid[replica] = dict(rank=int(rank), pid=int(pid), path=path, backend_line=line)
    if (Counter(row["path"] for row in by_uuid.values()) != Counter(paths)
            or {row["rank"] for row in by_uuid.values()} != set(range(8))):
        raise ValueError("backend requests do not cover the expected Store paths and eight ranks")
    return by_uuid


def check_pread(rows, cache, sizes, problems):
    files = defaultdict(list)
    for row in rows:
        files[row["path"]].append(row)
        if not (io_ok(row) and row["expected_file_bytes"] > 0
                and row["expected_file_bytes"] == row["transferred_bytes"]
                and 0 <= row["short_calls"] <= row["calls"]
                and not row["read_error"] and not row["cancelled"] and row["direct_open"] in (0, 1)):
            problems.append(f"line {row['line']}: incomplete or failed pread partition")
    if set(files) != set(cache["miss_paths"]):
        problems.append("pread records do not cover exactly the missing Store paths")
    for path, partitions in files.items():
        if sorted(row["partition"] for row in partitions) != list(range(len(partitions))):
            problems.append(f"{path}: missing or duplicate partition index")
        if len({row["file"] for row in partitions}) != len(partitions):
            problems.append(f"{path}: duplicate partition file")
        if sizes is not None and sum(row["transferred_bytes"] for row in partitions) != sizes.get(path):
            problems.append(f"{path}: pread bytes disagree with the Store ledger")
    if sum(row["transferred_bytes"] for row in rows) != cache["file_read_bytes"]:
        problems.append("pread byte total disagrees with store-step.json")


def check_gpu(records, identity, problems):
    if identity is None:
        return
    for phase in ("h2d", "recovery"):
        rows = records[phase]
        if Counter(row["replica"] for row in rows) != Counter(identity.keys()):
            problems[phase].append("records do not cover each expected single-device replica exactly once")
        for row in rows:
            expected = identity.get(row["replica"])
            if expected is None or row["path"] != expected["path"]:
                problems[phase].append(f"line {row['line']}: path/replica not matched to the backend")
            else:
                row.update(expected)
            if row["rc"] != 0 or not span_ok(row):
                problems[phase].append(f"line {row['line']}: failed or invalid operation span")
    recoveries = {row["replica"]: row for row in records["recovery"]}
    rank_devices = defaultdict(set)
    for row in records["h2d"]:
        if not io_ok(row) or not row["requested_bytes"] or row["requested_bytes"] != row["transferred_bytes"]:
            problems["h2d"].append(f"line {row['line']}: incomplete or failed H2D bytes")
        recovery = recoveries.get(row["replica"])
        if recovery and not recovery["begin_ns"] <= row["begin_ns"] <= row["end_ns"] <= recovery["end_ns"]:
            problems["h2d"].append(f"line {row['line']}: H2D falls outside its replica recovery")
        if "rank" in row:
            rank_devices[row["rank"]].add(row["device"])
    if (set(rank_devices) != set(range(8)) or any(len(devices) != 1 for devices in rank_devices.values())
            or set().union(*rank_devices.values()) != set(range(8))):
        problems["h2d"].append("inconsistent eight-rank mapping to native Store device ordinals")


def phase_summary(phase, rows, problems, expected):
    status = "incomplete" if problems else "complete" if expected else "not_applicable"
    summary = dict(status=status, expected_paths=len(expected) if isinstance(expected, list) else None,
                   observed_records=len(rows), records=rows, problems=problems,
                   begin_ns=None, end_ns=None, wall_span_ns=None)
    complete = status == "complete"
    if phase in IO_PHASES:
        summary.update(call_sum_ns=sum(row["call_sum_ns"] for row in rows) if complete else None,
                       transferred_bytes=sum(row["transferred_bytes"] for row in rows) if complete else None)
    if phase == "eviction":
        summary["freed_bytes"] = sum(row["freed_bytes"] for row in rows) if not problems else None
    if complete:
        begin = min(row["monotonic_ns" if phase == "lookup" else "begin_ns"] for row in rows)
        end = max(row["monotonic_ns" if phase == "lookup" else "end_ns"] for row in rows)
        summary.update(begin_ns=begin, end_ns=end, wall_span_ns=end - begin)
    return summary


def recompute_stage(stage):
    sources = {key: stage / name for key, name in (
        ("native", "store-timing.log"), ("ledger", "store-step.json"),
        ("legacy", "store-events.log"), ("client", "backend.log"),
    )}
    records, problems = parse_native(sources["native"])
    missing = [path.name for path in sources.values() if not path.is_file()]
    cache = None
    if sources["ledger"].is_file():
        try:
            cache = json.loads(sources["ledger"].read_bytes())["cache"]
        except (ValueError, KeyError) as exc:
            problems["source"].append(f"store-step.json: {exc}")
    phases = {}
    if cache is None:
        for phase, _ in MARKERS.values():
            problems[phase].append("store-step.json inventory unavailable")
            phases[phase] = phase_summary(phase, records[phase], problems[phase], True)
    else:
        paths = cache["hit_paths"] + cache["miss_paths"]
        if not sources["native"].is_file():
            for phase, _ in MARKERS.values():
                problems[phase].append("store-timing.log missing")
        if Counter(row["path"] for row in records["lookup"]) != Counter(paths):
            problems["lookup"].append("lookup markers do not cover exactly the expected Store paths")
        if any(row["monotonic_ns"] <= 0 for row in records["lookup"]):
            problems["lookup"].append("invalid lookup timestamp")
        sizes, identity = None, None
        if paths:
            try:
                identity = replica_identity(sources["client"], paths)
            except (ValueError, FileNotFoundError) as exc:
                for phase in ("h2d", "recovery"):
                    problems[phase].append(f"backend request join unavailable: {exc}")
        if cache["miss_paths"]:
            try:
                sizes = expected_sizes(sources["legacy"], cache)
            except (ValueError, FileNotFoundError) as exc:
                problems["pread"].append(f"legacy ledger bytes unavailable: {exc}")
        check_pread(records["pread"], cache, sizes, problems["pread"])
        check_gpu(records, identity, problems)
        evicted = records["eviction"]
        if (Counter(row["path"] for row in evicted) != Counter(cache["evicted_paths"])
                or sum(row["freed_chunks"] for row in evicted) != cache["freed_blocks"]):
            problems["eviction"].append("freed path or chunk inventory disagrees with store-step.json")
        if any(not span_ok(row) or row["freed_chunks"] <= 0
               or row["freed_bytes"] != row["freed_chunks"] * CHUNK_BYTES for row in evicted):
            problems["eviction"].append("invalid eviction interval or positive free bytes")
        expected = {"lookup": paths, "pread": cache["miss_paths"], "h2d": paths,
                    "recovery": paths, "eviction": cache["evicted_paths"]}
        for phase, applicable in expected.items():
            if not applicable and records[phase] and not problems[phase]:
                problems[phase].append("unexpected operation for this stage")
            phases[phase] = phase_summary(phase, records[phase], problems[phase], applicable)
    incomplete = problems["source"] or any(row["status"] == "incomplete" for row in phases.values())
    return dict(step=stage.name, cache_state=cache["cache_state"] if cache else None,
                status="incomplete" if incomplete else "complete",
                missing_sources=missing, sources={key: path.name for key, path in sources.items()},
                source_problems=problems["source"], phases=phases)


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
    print(json.dumps(dict(run_id=stages[0].parent.name, clock="store_host_CLOCK_MONOTONIC",
                          wall_span="earliest start to latest return; lookup first to last event",
                          call_sum="sum of host API calls; concurrent calls may overlap",
                          stages=[recompute_stage(stage) for stage in stages]), indent=2))


if __name__ == "__main__":
    main()
