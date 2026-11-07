"""Consume frozen Kimi evidence; no model, cache, or runtime operations."""
import argparse
import hashlib
import itertools
import json
import math
import pathlib
import random
import re
import statistics
import sys

CONDITIONS = ("safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit")
COMPARISONS = ((CONDITIONS[2], CONDITIONS[0]), (CONDITIONS[3], CONDITIONS[0]),
               (CONDITIONS[3], CONDITIONS[2]), (CONDITIONS[2], CONDITIONS[1]))
CLOCK_KEYS = ("boot_id", "time_namespace", "monotonic_clock_implementation")
PASS = "FOUR_CONDITION_BLOCK_RELEASE60_PASS"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_ref(ref):
    require(isinstance(ref, dict) and re.fullmatch(r"[0-9a-f]{64}", ref.get("sha256", "")), "invalid evidence reference")
    path = pathlib.Path(ref["path"])
    require(path.is_absolute() and not path.is_symlink() and path.is_file(), "evidence must be an absolute regular file")
    require(path.stat().st_size <= 16 << 20, "small evidence size limit")
    with path.open("rb") as stream:
        raw = stream.read((16 << 20) + 1)
    require(len(raw) <= 16 << 20, "small evidence size limit")
    require(hashlib.sha256(raw).hexdigest() == ref["sha256"], "evidence SHA mismatch: " + str(path))
    return json.loads(raw)


def clock(identity):
    require(isinstance(identity, dict) and not identity.get("errors"), "invalid clock identity")
    require(all(isinstance(identity.get(k), str) and identity[k] for k in CLOCK_KEYS), "missing clock domain")
    require(isinstance(identity.get("container_hostname"), str) and identity["container_hostname"], "missing producer hostname")
    require(type(identity.get("process_startticks")) is int and identity["process_startticks"] > 0, "missing producer startticks")
    return tuple(identity[k] for k in CLOCK_KEYS)


def ns(value):
    require(type(value) is int and value > 0, "invalid monotonic timestamp")
    return value


def elapsed(end, start):
    value = ns(end) - ns(start)
    require(value >= 0, "reversed timing endpoints")
    return value / 1e9


def request_metrics(request, case, run_id):
    require(request["case"] == case, "request case mismatch")
    if request.get("raw_interval_status") != "NUMERIC":
        return None
    require(request.get("request_id") == run_id + "-" + str(("arithmetic", "translation", "fixed32").index(case)), "request identity mismatch")
    clock(request["clock_identity"])
    times = request["producer_times"]
    ordered = [ns(times[k]) for k in ("business_trigger_ns", "request_begin_ns", "response_body_complete_ns", "json_parse_complete_ns", "received_ns", "child_gold_validation_begin_ns", "child_gold_validation_complete_ns")]
    require(ordered == sorted(ordered), "HTTP producer endpoint order mismatch")
    business = ns(times["received_ns"]) - ns(times["business_trigger_ns"])
    require(type(request.get("business_B_ns")) is int and request["business_B_ns"] == business, "business interval mismatch")
    primary = None
    if case == "arithmetic":
        primary = ns(times["received_ns"]) - ns(times["registration_command_ns"])
        require(ns(times["registration_command_ns"]) <= ns(times["register_complete_ns"]) <= ns(times["business_trigger_ns"]), "registration endpoint order mismatch")
        require(type(request.get("primary_A_ns")) is int and request["primary_A_ns"] == primary, "primary A mismatch")
    return {"primary_A_s": None if primary is None else primary / 1e9, "business_s": business / 1e9,
            "producer_times": times, "clock_identity": request["clock_identity"]}


def load_run(entry):
    refs = entry["refs"]
    data = {key: read_ref(ref) if ref is not None else None for key, ref in refs.items()}
    run = entry["run_id"]
    cohort = entry["cohort"]
    require(cohort in ("original", "supplement", "historical"), "unknown cohort")
    require(entry["condition"] in CONDITIONS, "unknown condition")
    if cohort == "historical":
        validity, controller = data.get("validity") or {}, data.get("controller") or {}
        qualified = validity.get("primary_sample_qualified", False)
        require(type(qualified) is bool, "historical qualification must be boolean")
        require(not qualified or data.get("controller") is not None, "historical qualified count lacks launch evidence")
        return {"run_id": run, "cohort": cohort, "block": entry.get("block"), "condition": entry["condition"],
                "launched": data.get("controller") is not None, "qualified": qualified,
                "observation_status": "HISTORICAL_PRESERVED_NOT_REVALIDATED", "version": None,
                "controller_status": controller.get("status", "NOT_OBSERVED"),
                "timing": dict.fromkeys(("A_s", "Prep_s", "Gap_s", "Prep_plus_A_s", "contiguous_s", "arithmetic_B_s", "translation_B_s", "D_fixed32_s")),
                "historical_raw_metrics": data.get("metrics"), "backend_first_nonempty": {},
                "client_TTFT": "UNMEASURED", "pure_decode": "UNMEASURED", "refs": refs}
    for key in ("metrics", "validity", "terminal", "cleanup"):
        row = data.get(key)
        if row is not None:
            require(row.get("run_id") == run and row.get("condition", entry["condition"]) == entry["condition"], "run/condition mismatch: " + key)
    versions = {(row["source_commit"], row["protocol_sha256"]) for row in data.values()
                if isinstance(row, dict) and row.get("source_commit") and row.get("protocol_sha256")}
    require(len(versions) <= 1, "within-run source/protocol disagreement")
    validity = data.get("validity") or {}
    qualified = validity.get("primary_sample_qualified", False)
    require(type(qualified) is bool, "qualification must be boolean")
    controller = data.get("controller")
    launched = controller is not None
    metrics = data.get("metrics") or {}
    requests = metrics.get("requests", [])
    require(len({q["case"] for q in requests}) == len(requests), "duplicate request case")
    by_case = {q["case"]: q for q in requests}
    values = {case: request_metrics(by_case[case], case, run) if case in by_case else None
              for case in ("arithmetic", "translation", "fixed32")}
    domains = {clock(v["clock_identity"]) for v in values.values() if v}
    require(len(domains) <= 1, "incompatible HTTP producer clocks")
    if controller is not None:
        require(controller.get("condition", entry["condition"]) == entry["condition"], "controller condition mismatch")
        if controller.get("attempt", {}).get("block") is not None:
            require(controller["attempt"]["block"] == entry.get("block"), "controller block mismatch")
    if qualified:
        evidence = {pathlib.Path(q["path"]).name: q["sha256"] for q in validity.get("evidence", [])}
        require(len(evidence) == len(validity.get("evidence", [])), "duplicate validity evidence")
        for key, filename in (("metrics", "sample-metrics.json"), ("terminal", "condition-terminal.json"), ("cleanup", "cleanup-result.json")):
            require(refs.get(key) is not None and evidence.get(filename) == refs[key]["sha256"], "validity evidence SHA link mismatch: " + key)
        require(launched and isinstance(controller.get("status"), str) and controller["status"].endswith("_PASS"), "qualified run lacks normal controller")
        require(all(validity.get("gates", {}).get(k, {}).get("status") == "PASS" for k in ("cache", "gold", "clock", "ownership")), "qualified gate contradiction")
        terminal, cleanup = data.get("terminal"), data.get("cleanup")
        require(terminal is not None and terminal.get("normal") is True and cleanup is not None and cleanup.get("normal") is True, "qualified terminal/cleanup contradiction")
        quiet = cleanup.get("quiet_seconds")
        require(type(quiet) in (int, float) and math.isfinite(quiet) and quiet >= 60, "qualified release quiet missing")
        require(all(values.values()) and all(by_case[k].get("child_gold_validation_pass") is True for k in values), "qualified HTTP evidence missing")
        require(len(versions) == 1, "qualified version identity missing")
    if qualified:
        registration, completion = data.get("registration_command"), data.get("arithmetic_complete")
        require(registration is not None and completion is not None, "qualified primary endpoint events missing")
        arithmetic = by_case["arithmetic"]
        times = arithmetic["producer_times"]
        for event, name in ((registration, "command_register_begin"), (completion, "arithmetic_http_complete_correct")):
            require(event.get("run_id") == run and event.get("condition") == entry["condition"] and event.get("event") == name, "primary endpoint event identity mismatch")
            require(event.get("request_id") == arithmetic["request_id"], "primary endpoint request mismatch")
            require(event.get("clock_identity") == arithmetic["clock_identity"], "primary endpoint producer clock mismatch")
            require(type(event.get("client_pid")) is int and event["client_pid"] > 0 and isinstance(event.get("client_cid"), str) and event["client_cid"], "primary endpoint client identity missing")
            require(all(k in times and value == times[k] for k, value in event.get("http_producer_times", {}).items()), "primary endpoint producer times mismatch")
            require(all(k in event.get("http_producer_times", {}) for k in ("business_trigger_ns", "request_begin_ns", "response_body_complete_ns", "json_parse_complete_ns", "received_ns", "child_gold_validation_begin_ns", "child_gold_validation_complete_ns")), "primary endpoint producer times missing")
        require(registration["client_pid"] == completion["client_pid"] and registration["client_cid"] == completion["client_cid"], "primary endpoint client identity mismatch")
        require(registration.get("producer") == "same_http_client_as_arithmetic" and completion.get("endpoint") == "received_ns", "primary endpoint semantics mismatch")
        require(ns(registration["monotonic_ns"]) == times["registration_command_ns"] and ns(completion["monotonic_ns"]) == times["received_ns"], "primary endpoint timestamp mismatch")
        for key in ("primary_A_ns", "business_B_ns"):
            require(type(completion.get(key)) is int and completion[key] == arithmetic[key], "primary endpoint interval mismatch: " + key)
        require(completion["primary_A_ns"] == completion["monotonic_ns"] - registration["monotonic_ns"], "primary endpoint A decomposition mismatch")
        for key in ("registration_command_ns", "register_complete_ns", "response_body_complete_ns"):
            require(completion.get(key) == times[key], "primary endpoint field mismatch: " + key)
    timing = {"A_s": values["arithmetic"]["primary_A_s"] if values["arithmetic"] else None,
              "arithmetic_B_s": values["arithmetic"]["business_s"] if values["arithmetic"] else None,
              "translation_B_s": values["translation"]["business_s"] if values["translation"] else None,
              "D_fixed32_s": values["fixed32"]["business_s"] if values["fixed32"] else None,
              "Prep_s": None, "Gap_s": None, "Prep_plus_A_s": None, "contiguous_s": None}
    begin, admission = data.get("construction_begin"), data.get("admission_complete")
    if begin is not None and admission is not None and values["arithmetic"]:
        require(begin.get("run_id") == admission.get("run_id") == run, "preparation event run mismatch")
        require(begin.get("event") == "target_condition_construction_begin" and admission.get("event") == "cache_pool_admission_complete", "preparation event type mismatch")
        domain = clock(values["arithmetic"]["clock_identity"])
        require(clock(begin["clock_identity"]) == clock(admission["clock_identity"]) == domain, "incompatible preparation/HTTP clocks")
        times = values["arithmetic"]["producer_times"]
        timing["Prep_s"] = elapsed(admission["monotonic_ns"], begin["monotonic_ns"])
        timing["Gap_s"] = elapsed(times["registration_command_ns"], admission["monotonic_ns"])
        timing["Prep_plus_A_s"] = timing["Prep_s"] + timing["A_s"]
        timing["contiguous_s"] = elapsed(times["received_ns"], begin["monotonic_ns"])
        require(math.isclose(timing["contiguous_s"], timing["Prep_plus_A_s"] + timing["Gap_s"], rel_tol=1e-12, abs_tol=1e-9), "cost decomposition mismatch")
    if qualified:
        require(timing["Prep_s"] is not None, "qualified preparation evidence missing")
    first = {}
    lifecycle = data.get("lifecycle")
    if lifecycle is not None:
        require(lifecycle.get("run_id") == run, "lifecycle run mismatch")
        events = lifecycle["events"].get("backend_first_token_observed", [])
        require(len({q["request_id"] for q in events}) == len(events), "duplicate first-output event")
        for event in events:
            matches = [k for k in values if by_case.get(k, {}).get("request_id") == event["request_id"]]
            require(len(matches) == 1 and values[matches[0]] is not None, "unjoined first-output event")
            case = matches[0]
            request = values[case]
            require(clock(event["clock_identity"]) == clock(request["clock_identity"]), "incompatible first-output clocks")
            owner = lifecycle["backend_owner"]
            require(event["clock_identity"]["process_startticks"] == owner["startticks"], "first-output process identity mismatch")
            require(ns(request["producer_times"]["request_begin_ns"]) <= ns(event["monotonic_ns"]) <= ns(request["producer_times"]["response_body_complete_ns"]), "first-output outside request")
            first[case] = {"status": "BACKEND_FIRST_NONEMPTY_OUTPUT_OBSERVED", "latency_from_business_trigger_s": elapsed(event["monotonic_ns"], request["producer_times"]["business_trigger_ns"]),
                           "pid": event["pid"], "clock_identity": event["clock_identity"]}
    return {"run_id": run, "cohort": cohort, "block": entry.get("block"), "condition": entry["condition"],
            "launched": launched, "qualified": qualified, "observation_status": "OBSERVED" if any(values.values()) else "NOT_OBSERVED",
            "version": list(next(iter(versions))) if versions else None, "controller_status": controller.get("status") if controller else "NOT_OBSERVED",
            "timing": timing, "backend_first_nonempty": first, "client_TTFT": "UNMEASURED", "pure_decode": "UNMEASURED",
            "observation_context": entry.get("observation_context"), "refs": refs}


def load_inputs(manifest):
    require(manifest.get("schema") == "kimi-four-analysis-input-v1", "unknown manifest schema")
    for ref in manifest.get("provenance", []):
        read_ref(ref)
    entries = manifest["runs"]
    require(len({q["run_id"] for q in entries}) == len(entries), "duplicate run_id; logical IDs must not overwrite failures")
    rows = [load_run(q) for q in entries]
    original = [q for q in rows if q["cohort"] == "original"]
    require(len(original) == 40 and {(q["block"], q["condition"]) for q in original} == set(itertools.product(range(1, 11), CONDITIONS)), "original planned40 geometry changed")
    blocks = manifest["blocks"]
    require(len(blocks) == 10 and {q["block"] for q in blocks} == set(range(1, 11)), "block final inventory changed")
    complete, excluded = [], []
    versions = []
    for item in sorted(blocks, key=lambda q: q["block"]):
        final = read_ref(item["final"])
        group = [q for q in original if q["block"] == item["block"]]
        version_set = {tuple(q["version"]) for q in group if q["version"]}
        versions.append({"block": item["block"], "versions": sorted(version_set), "block_status": final["status"]})
        if final["status"] != PASS:
            excluded.append({"block": item["block"], "reason": "original block terminal is not PASS", "status": final["status"]})
            continue
        require(all(q["qualified"] for q in group) and len(version_set) == 1, "PASS block qualification/version contradiction")
        require(set(q["run_id"] for q in group) == set(q["run_id"] for q in final["attempts"]), "block final run set mismatch")
        require(all(q["status"].endswith("_PASS") and q.get("returncode") == 0 for q in final["attempts"]), "PASS block child terminal contradiction")
        if final.get("source_commit"):
            require(final["source_commit"] == next(iter(version_set))[0] and final["protocol_sha256"] == next(iter(version_set))[1], "block final version mismatch")
        complete.append({"block": item["block"], "values": {q["condition"]: q["timing"] for q in group}})
    return rows, complete, excluded, versions


def exact_sign_flip(differences):
    n = len(differences)
    if not n:
        return None
    observed = abs(statistics.mean(differences))
    count = sum(abs(sum(s * d for s, d in zip(signs, differences)) / n) >= observed - 1e-12
                for signs in itertools.product((-1, 1), repeat=n))
    return count / (2 ** n)


def holm(pvalues):
    order = sorted(range(len(pvalues)), key=lambda i: (pvalues[i], i))
    adjusted, largest = [None] * len(pvalues), 0.0
    for rank, i in enumerate(order):
        largest = max(largest, (len(order) - rank) * pvalues[i])
        adjusted[i] = min(1.0, largest)
    return adjusted


def ratio(left, right):
    denominator = statistics.mean(right)
    return statistics.mean(left) / denominator if denominator else None


def bootstrap(left, right, statistic):
    if not left:
        return None
    rng = random.Random(20261024)
    values = []
    for _ in range(10000):
        indices = [rng.randrange(len(left)) for _ in left]
        a, b = [left[i] for i in indices], [right[i] for i in indices]
        value = statistics.mean(x - y for x, y in zip(a, b)) if statistic == "difference" else ratio(a, b)
        if value is None or not math.isfinite(value):
            return None
        values.append(value)
    values.sort()
    return [values[249], values[9749]]


def paired_family(complete, metric):
    result = []
    for left, right in COMPARISONS:
        a = [q["values"][left][metric] for q in complete]
        b = [q["values"][right][metric] for q in complete]
        require(all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in a + b), "invalid paired duration")
        delta = [x - y for x, y in zip(a, b)]
        sd = statistics.stdev(delta) if len(delta) >= 2 else 0
        result.append({"left": left, "right": right, "n": len(delta), "blocks": [q["block"] for q in complete],
                       "left_values_s": a, "right_values_s": b, "differences_s": delta,
                       "mean_difference_s": statistics.mean(delta) if delta else None,
                       "ratio_of_means": ratio(a, b) if delta else None,
                       "dz": statistics.mean(delta) / sd if sd else None,
                       "dz_status": "ESTIMATED" if sd else "UNDEFINED_SAMPLE_SD_ZERO_OR_N_LT_2",
                       "exact_two_sided_p": exact_sign_flip(delta),
                       "difference_percentile95": bootstrap(a, b, "difference"),
                       "ratio_percentile95": bootstrap(a, b, "ratio")})
    adjusted = holm([q["exact_two_sided_p"] for q in result]) if complete else [None] * 4
    for row, value in zip(result, adjusted):
        row.update(holm4_adjusted_p=value, holm4_reject_0_05=value is not None and value <= .05)
    return result


def wilson(successes, n):
    if not n:
        return None
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0, center - half), min(1, center + half)]


def describe(values):
    if not values:
        return {"n": 0, "mean": None, "median": None, "nearest_rank_p90": None, "raw": []}
    ordered = sorted(values)
    return {"n": len(values), "mean": statistics.mean(values), "median": statistics.median(values),
            "nearest_rank_p90": ordered[math.ceil(.9 * len(values)) - 1], "raw": values}


def analyze(rows, complete, excluded, versions):
    cohorts = {}
    for name in ("original", "supplement", "historical", "all"):
        selected = rows if name == "all" else [q for q in rows if q["cohort"] == name]
        launched = sum(q["launched"] for q in selected)
        qualified = sum(q["qualified"] for q in selected)
        cohorts[name] = {"planned_or_listed": len(selected), "launch_requested": launched, "qualified": qualified,
                         "failed_launched": launched - qualified, "not_started": len(selected) - launched,
                         "wilson95": wilson(qualified, launched)}
    descriptive = []
    for cohort in ("original", "supplement", "historical"):
        for condition in CONDITIONS:
            selected = [q for q in rows if q["cohort"] == cohort and q["condition"] == condition and q["qualified"]]
            descriptive.append({"cohort": cohort, "condition": condition,
                                "metrics": {metric: describe([q["timing"][metric] for q in selected if q["timing"][metric] is not None])
                                            for metric in ("A_s", "Prep_s", "Gap_s", "Prep_plus_A_s", "contiguous_s", "arithmetic_B_s", "translation_B_s", "D_fixed32_s")}})
    return {"schema": "kimi-four-analysis-report-v1", "python_version": sys.version, "cohorts": cohorts, "complete_block_ids": [q["block"] for q in complete],
            "paired_sample_count": 4 * len(complete), "excluded_blocks": excluded, "block_versions": versions,
            "primary_A_Holm4": paired_family(complete, "A_s"), "secondary_Prep_plus_A_Holm4": paired_family(complete, "Prep_plus_A_s"),
            "qualified_descriptives_including_labeled_unpaired": descriptive, "runs": rows,
            "limits": ["Two separate Holm4 families, no joint8-test FWER or simultaneous CI claim.",
                       "Bootstrap MT19937 Random(20261024), B10000 reset per comparison/statistic, paired block resampling, sorted indices249/9749.",
                       "D is ready fixed32 whole HTTP duration, not pure decode. First-nonempty events are backend observations, not client TTFT/GPU sampling.",
                       "Original incomplete blocks never paired with supplemental retries; original FAIL and all version strata retained.",
                       "Historical qualification labels are preserved, not revalidated under the new criterion; aggregate counts mix explicitly separated criterion cohorts."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    parser.add_argument("--manifest-sha", required=True)
    args = parser.parse_args()
    manifest = read_ref({"path": str(args.manifest), "sha256": args.manifest_sha})
    rows, complete, excluded, versions = load_inputs(manifest)
    report = analyze(rows, complete, excluded, versions)
    report["input_manifest"] = {"path": str(args.manifest), "sha256": args.manifest_sha}
    report["implementation_sha256"] = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": "ANALYSIS_WRITTEN", "output": str(args.output)}))


if __name__ == "__main__":
    main()
