"""Compare metadata-only V4 ScaleDiag receipts. JSON goes to stdout."""
from collections import Counter
import json
from pathlib import Path
import re
import sys


IDENTITY = ("boot_id", "time_namespace", "pid_namespace", "pid", "process_start_ticks")
EXPECTED = (
    "initial_ready", "before_sleep", "after_sleep",
    "weights_mapped", "weights_reloaded", "kv_ready", "before_sleep", "after_sleep",
    "weights_mapped", "weights_reloaded", "kv_ready", "before_sleep", "after_sleep",
)


def read(path):
    return json.loads(path.read_bytes())


def identity(row):
    return tuple(row["identity"][k] for k in IDENTITY)


def by_rank(doc):
    rows = {row["rank"]: row for row in doc["ranks"]}
    assert len(doc["ranks"]) == 8 and set(rows) == set(range(8))
    return rows


def state(row):
    alias = Counter()
    keys = {}
    for layer in row["scales_by_layer"]:
        for name, scale in layer["scales"].items():
            for holder, entry in scale["holders"].items():
                keys[layer["name"], name, holder] = entry.get("storage_key") if entry else None
                alias[f"{holder}:{entry['weight_map'] if entry else 'missing'}"] += 1
            registered = keys[layer["name"], name, "registered"]
            for holder in ("method", "kernel"):
                other = keys[layer["name"], name, holder]
                outcome = "missing" if registered is None or other is None else (
                    "alias" if registered == other else "diverged")
                alias[f"{holder}_vs_registered:{outcome}"] += 1
            method = keys[layer["name"], name, "method"]
            kernel = keys[layer["name"], name, "kernel"]
            outcome = "missing" if method is None or kernel is None else (
                "alias" if method == kernel else "diverged")
            alias[f"method_vs_kernel:{outcome}"] += 1
    return alias, keys


def native_path(receipt):
    match = re.fullmatch(r"scales-(sleep|wake)-(\d{4})-(.*)\.json", receipt.name)
    if not match:
        return None
    kind, number, suffix = match.groups()
    if kind == "wake" and suffix in ("weights-mapped", "weights-reloaded"):
        path = receipt.with_name(f"wake-{number}-{suffix}.json")
        return (path, False)
    if (kind, suffix) in (("sleep", "after"), ("wake", "kv-ready")):
        return (receipt.with_name(f"ack-{number}.json"), True)
    return None


def native_receipt(receipt):
    reference = native_path(receipt)
    if reference is None or not reference[0].exists():
        return None, None
    path, ack = reference
    doc = read(path)
    return path.name, by_rank(doc["receipt"] if ack else doc)


def changes(old, new):
    return sum(old.get(key) != new.get(key) for key in old.keys() | new.keys())


def assess_rebind(passed, phases, post_sleep):
    """Evaluate scale storage independently of total NVML residency."""
    required = {"after_sleep", "weights_reloaded", "kv_ready"}
    checks = []
    checked_phases = []
    for phase in phases:
        if phase["phase"] not in required:
            continue
        checked_phases.append(phase["phase"])
        assert {row["rank"] for row in phase["rank_rows"]} == set(range(8))
        for row in phase["rank_rows"]:
            aliases = row["aliases"]
            if (row["layer_count"] != 61 or row["storage"].get("outside_weights") != 0
                    or any(aliases.get(f"{holder}:contained") != 122
                           for holder in ("registered", "method", "kernel"))
                    or any(aliases.get(f"{holder}_vs_registered:alias") != 122
                           for holder in ("method", "kernel"))):
                checks.append(dict(source=phase["source"], rank=row["rank"]))
    first = post_sleep[0]["gpu_used_per_card_mib"] if post_sleep else []
    deltas = [[value - base for base, value in zip(first, row["gpu_used_per_card_mib"], strict=True)]
              for row in post_sleep[1:]]
    if passed:
        assert checked_phases == [phase for phase in EXPECTED if phase in required]
        assert len(deltas) == 2
    return dict(scale_contract_passed=(not checks if passed else None),
                qualification_passed=passed, scale_contract_failures=checks,
                post_sleep_delta_from_initial_mib=deltas)


def summarize(run):
    worker = run / "L2/worker-D"
    receipts = sorted(((read(path), path) for path in worker.glob("scales-*.json")),
                      key=lambda item: item[0]["started_ns"])
    rank_identities, first, previous, previous_storage = {}, {}, {}, {}
    phases, phase_flags = [], []
    for doc, path in receipts:
        phase = doc["phase"]
        assert len(phase_flags) < len(EXPECTED) and phase == EXPECTED[len(phase_flags)], path
        phase_flags.append(phase)
        native_file, native = native_receipt(path)
        rank_rows, totals = [], Counter()
        for rank, row in sorted(by_rank(doc).items()):
            assert row["phase"] == phase and row["layer_count"] == len(row["scales_by_layer"]) > 0
            assert row["identity"]["clock_id"] == "CLOCK_MONOTONIC"
            assert doc["started_ns"] <= row["begin_ns"] <= row["end_ns"] <= doc["finished_ns"]
            assert rank_identities.setdefault(rank, identity(row)) == identity(row), (path, rank)
            aliases, keys = state(row)
            storage = {holder: value["storage_union_bytes"]
                       for holder, value in row["storage"].items()}
            storage["tagged_weights_virtual"] = row["tagged_weight_map"]["virtual_bytes"]
            if native is not None:
                native_row = native[rank]
                assert identity(native_row) == identity(row), (native_file, rank)
                storage["native_tagged_weights_virtual"] = native_row["tags"]["weights"]["bytes"]
                storage["torch_allocated"] = native_row["cuda_allocated_bytes"]
                storage["torch_reserved"] = native_row["cuda_reserved_bytes"]
                storage["tagged_kv_virtual"] = native_row["tags"]["kv_cache"]["bytes"]
            for metric, value in storage.items():
                totals[metric] += value
            last = previous_storage.setdefault(rank, {})
            deltas = {metric: value - last[metric] for metric, value in storage.items() if metric in last}
            last.update(storage)
            rank_rows.append(dict(
                rank=rank, layer_count=row["layer_count"], aliases=dict(aliases), storage=storage,
                storage_delta_from_preceding_observation_bytes=deltas,
                changed_holder_refs_from_previous=changes(previous[rank], keys) if rank in previous else None,
                changed_holder_refs_from_initial=changes(first.setdefault(rank, keys), keys),
            ))
            previous[rank] = keys
        phases.append(dict(phase=phase, source=path.name, native_source=native_file,
                           started_ns=doc["started_ns"], finished_ns=doc["finished_ns"],
                           rank_rows=rank_rows, storage_total_bytes=dict(totals)))
    last_values = {}
    for row in phases:
        totals = row["storage_total_bytes"]
        row["delta_from_preceding_observation_bytes"] = {
            metric: value - last_values[metric] for metric, value in totals.items() if metric in last_values}
        last_values.update(totals)
    post_sleep = []
    for path in sorted((run / "L2").glob("L2-*-D-post-sleep-residency.json")):
        doc = read(path)
        indexed = {int(i): int(used) for i, _, used in (line.split(",") for line in doc["gpu"])}
        assert len(doc["gpu"]) == 8 and set(indexed) == set(range(8))
        use = [indexed[i] for i in range(8)]
        delta = ([a - b for a, b in zip(use, post_sleep[-1]["gpu_used_per_card_mib"])]
                 if post_sleep else None)
        post_sleep.append(dict(source=path.name, ns=doc["ns"], gpu_used_per_card_mib=use,
                               gpu_used_delta_per_card_mib=delta,
                               gpu_used_total_mib=sum(use), gpu_used_max_mib=max(use)))
    passed = read(run / "qualification.json")["passed"] if (run / "qualification.json").is_file() else None
    if passed:
        assert tuple(phase_flags) == EXPECTED and len(post_sleep) == 3
        assert all(row["native_source"] is not None for row in phases
                   if row["phase"] in ("after_sleep", "weights_mapped", "weights_reloaded", "kv_ready"))
    result = dict(run_id=run.name, qualification_passed=passed, phase_count=len(phases),
                  expected_phase_count=len(EXPECTED), phases=phases, post_sleep_nvml=post_sleep)
    if run.name.startswith("B2-V4RebindDiag-"):
        result["rebind"] = assess_rebind(passed, phases, post_sleep)
    return result


if __name__ == "__main__":
    print(json.dumps(summarize(Path(sys.argv[1])), indent=2, sort_keys=True))
