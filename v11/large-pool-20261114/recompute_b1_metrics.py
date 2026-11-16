"""Read-only per-step B1 supplemental recompute from frozen evidence."""
import argparse
import json
from pathlib import Path
import re
from collections import defaultdict

import recompute_b1_store_trace as store
import recompute_b1_worker_trace as worker


NS = 10**9
CLOCK = ("boot_id", "time_namespace", "clock_id")
RANKS = set(range(8))


def read(path):
    return json.loads(path.read_bytes()) if path.is_file() else {}


def rows(path):
    if path.is_file():
        for number, line in enumerate(path.read_bytes().splitlines(), 1):
            yield number, json.loads(line)


def interval(begin, end):
    if type(begin) is int and type(end) is int and 0 < begin <= end:
        return begin, end


def metric(bounds=None, reason=None, source=None):
    return dict(begin_ns=bounds[0] if bounds else None, end_ns=bounds[1] if bounds else None,
                seconds=(bounds[1] - bounds[0]) / NS if bounds else None,
                status="observed" if bounds else "missing", source=source, reason=reason)


def unavailable(reason, *, zero=False):
    return dict(begin_ns=None, end_ns=None, seconds=0.0 if zero else None,
                status="did_not_occur" if zero else "not_applicable", reason=reason)


def union(bounds):
    result = []
    for begin, end in sorted(bounds):
        if result and begin <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((begin, end))
    return result


def intersect(left, right):
    return union([(max(a, c), min(b, d)) for a, b in left for c, d in right
                  if max(a, c) < min(b, d)])


def duration(bounds):
    return sum(end - begin for begin, end in bounds) / NS


def event_data(stage):
    return [(f"events/{path.name}:{number}", event)
            for path in sorted((stage / "events").glob("*.jsonl"))
            for number, event in rows(path)]


def ranked(stage, data, name, clock, lower=None, upper=None, pair=True, registered=None):
    selected = defaultdict(list)
    run_id = f"{stage.parent.name}-{stage.name}"
    identities = {}
    for source, event in data:
        if event.get("name") not in ((name + "_begin", name + "_end") if pair else (name,)):
            continue
        rank = event.get("rank")
        if type(rank) is not int or rank not in RANKS:
            return None, [], f"{source}: invalid rank"
        ident = tuple(event.get(key) for key in worker.PROCESS_FIELDS)
        if not (worker.has_identity(event, run_id) and tuple(event[k] for k in CLOCK) == clock
                and identities.setdefault(rank, ident) == ident):
            return None, [], f"{source}: clock/worker identity mismatch"
        selected[rank].append((source, event))
    if set(selected) != RANKS or len(set(identities.values())) != 8:
        return None, [], f"{name}: missing rank coverage or worker collision"
    observations = []
    for rank, events in sorted(selected.items()):
        if pair:
            starts = [(src, row) for src, row in events if row["name"].endswith("_begin")]
            ends = [(src, row) for src, row in events if row["name"].endswith("_end")]
            if len(starts) != 1 or len(ends) != 1:
                return None, [], f"{name}: missing or duplicate rank {rank} pair"
            (begin_source, begin), (end_source, end) = starts[0], ends[0]
            if begin["sequence"] >= end["sequence"]:
                return None, [], f"{name}: invalid rank {rank} sequence"
            bounds = interval(begin["monotonic_ns"], end["monotonic_ns"])
            sources = [begin_source, end_source]
        else:
            if len(events) != 1:
                return None, [], f"{name}: missing or duplicate rank {rank} attempt"
            source, row = events[0]
            if not (row.get("status") == "ok" and row.get("ack_observed") is True
                    and row.get("future_present") is True and registered and (row["instance_id"], identities[rank]) == registered.get(rank)):
                return None, [], f"{name}: missing ACK or mismatched registration for rank {rank}"
            bounds = interval(row.get("begin_ns"), row.get("end_ns"))
            if bounds and bounds[1] > row["monotonic_ns"]:
                return None, [], f"{name}: interval after its event"
            sources = [source]
        if not bounds or (lower and bounds[0] < lower) or (upper and bounds[1] > upper):
            return None, [], f"{name}: rank {rank} interval outside stage bounds"
        observations.append(dict(rank=rank, bounds=bounds, sources=sources))
    return (min(x["bounds"][0] for x in observations),
            max(x["bounds"][1] for x in observations)), observations, None


def covered(summary, phase):
    item = summary["phases"][phase]
    return interval(item["begin_ns"], item["end_ns"]) if item["status"] == "complete" else None


def worker_intervals(summary, phase):
    item = summary["phases"][phase]
    if item["status"] != "complete":
        return []
    return [interval(row["begin_ns"], row["end_ns"]) for row in item["records"]]


def fill_metrics(stage, events, service_clock):
    start = read(stage / "before-kv_fill-request-start.json")
    response = read(stage / "before-kv_fill-response.json")
    check = read(stage / "before-kv_fill-checks.json")
    request_id = f"{stage.name}-before-kv_fill"
    verified = check.get("passed") is True and start.get("request_id") == request_id
    arrived = response.get("received_ns") if response.get("response", {}).get("id") == request_id else None
    response_bounds = interval(start.get("ns"), arrived) if verified else None
    reply = metric(response_bounds, None if response_bounds else "missing exact request/result identity or gold", "kv_fill request, response and checks")
    if not verified or not service_clock or type(start.get("wall_ns")) is not int or type(start.get("ns")) is not int:
        return reply, metric(reason="missing kv_fill request clock/identity")
    bridge = start["wall_ns"] - start["ns"]
    request_pattern = re.compile(re.escape(request_id) + r"(?:-[0-9a-f]{8})?$")
    selected = defaultdict(list)
    for line, event in events:
        if request_pattern.fullmatch(event.get("request_id") or ""):
            selected[event.get("transfer_key")].append((line, event))
    commits, offsets = [], [bridge]
    for key, items in selected.items():
        began = [(number, row) for number, row in items if row["event"] == "mp.store.start"]
        ended = [(number, row) for number, row in items if row["event"] == "mp.store.end"]
        if not key or len(began) != 1 or len(ended) != 1:
            return reply, metric(reason="ambiguous LMCache transfer start/end")
        (start_line, x), (end_line, y) = began[0], ended[0]
        if not (start_line < end_line and x["event_wall_ns"] <= y["event_wall_ns"]
                and x["engine_id"] == y["engine_id"] and y.get("total_bytes", 0) > 0
                and y.get("num_tokens", 0) > 0 and y.get("stored_count", 0) > 0):
            return reply, metric(reason="invalid LMCache transfer evidence")
        offsets.extend(row["drain_wall_ns"] - row["drain_monotonic_ns"] for row in (x, y))
        commits.append((end_line, y))
    if not commits or max(offsets) - min(offsets) > 5_000_000:
        return reply, metric(reason="no successful matching commit or clock bridge spread exceeded 5 ms")
    last_commit = max(row["event_wall_ns"] for _, row in commits) - bridge
    bounds = interval(start["ns"], last_commit)
    clear_start = read(stage / "kv-clear.json").get("http_begin_ns")
    if bounds is None or (clear_start is not None and last_commit > clear_start):
        return reply, metric(reason="wall-aligned LMCache commit falls outside the request/clear window")
    result = metric(bounds, source="lmcache/store-events.jsonl:mp.store.end, scoped by request and transfer")
    result.update(bridge_spread_ns=max(offsets) - min(offsets), event_lines=[line for line, _ in commits],
                  stored_bytes=sum(row["total_bytes"] for _, row in commits),
                  clock="LMCache REALTIME mapped to host MONOTONIC; observed bracket spread")
    return reply, result


def rank_physical(stage, data):
    # Pinned worker currently records only reference cleanup. No supported physical event can be invented.
    rows = [row for _, row in data if row.get("name") == "b1_kv_reference_cleanup"]
    return metric(reason="no independent physical KV release witness", source="b1_kv_reference_cleanup; CUDA context/exit separate"), sorted({r["rank"] for r in rows if r.get("rank") in RANKS})


def stage_summary(stage, previous, commit_events, service_clock):
    wrk = worker.recompute_stage(stage)
    strc = store.recompute_stage(stage)
    dat = event_data(stage)
    arm = read(stage / "activation-readiness.json")
    constructed = read(stage / "constructed.json").get("clock_identity", {})
    clock = tuple(constructed.get(k) for k in CLOCK)
    have_clock = (None not in clock and clock[2] == "CLOCK_MONOTONIC")
    ready = arm.get("backend_ready_ns") or wrk["markers"]["backend_ready"]["ns"]
    armed = arm.get("start_model_signal_ns") or wrk["markers"]["constructed"]["ns"]
    full, load_ranks, load_gap = ranked(stage, dat, "full_weight_load", clock, armed, ready)
    post, post_ranks, post_gap = ranked(stage, dat, "quantization_postprocess", clock, armed, ready)
    dispatch = arm.get("activation_dispatch_ns")
    clear = read(stage / "kv-clear.json")
    drain = read(stage / "gpu-drain.json")
    c = clear.get("clear", {})
    clear_bounds = interval(clear.get("http_begin_ns"), clear.get("http_end_ns")) if (
        c.get("status") == 200 and clear.get("clear_ok") is True) else None
    shutdown = interval(drain.get("backend_shutdown_begin_ns"), drain.get("shutdown_and_drain_ready_ns"))
    init, reg = covered(wrk, "kv_tensor_init"), covered(wrk, "initial_registration")
    worker_clock = wrk["clock_identity"] or {}
    if not have_clock or tuple(worker_clock.get(key) for key in CLOCK) != clock:
        init = reg = None
    worker_ready = wrk["new_kv_ready_ns"] if init and reg else None
    init_ready = interval(init[0] if init else None, worker_ready)
    registered = {row["rank"]: (row["instance_id"], tuple(row[k] for k in worker.PROCESS_FIELDS))
                  for row in wrk["phases"]["initial_registration"]["records"] if
                  all(k in row for k in ("rank", "instance_id", *worker.PROCESS_FIELDS))}
    um, umr, um_err = ranked(
        stage, dat, "b1_lmcache_unregister_transfer", clock,
        wrk["markers"]["shutdown_begin"]["ns"], wrk["markers"]["shutdown_end"]["ns"],
        pair=False, registered=registered if reg else None)
    physical, ref_rows = rank_physical(stage, dat)
    external = metric(interval(dispatch, arm.get("route_ready_ns")) if have_clock and
                      arm.get("route_kind") == "direct_engine_after_init_backend" else None,
                      source="activation-readiness.json",
                      reason="direct engine admission; external router/health unavailable")
    correct = []
    for path in stage.glob("before-*-checks.json"):
        name = path.name.removeprefix("before-").removesuffix("-checks.json")
        check = read(path)
        resp = read(stage / f"before-{name}-response.json")
        if name != "kv_fill" and check.get("passed") is True and not check.get("length_only", False) and (
            resp.get("response", {}).get("id") == f"{stage.name}-before-{name}") and (
            resp.get("response", {}).get("model") == "shared-" + stage.name.split("-", 2)[-1]) and interval(ready, resp.get("received_ns")):
            correct.append((resp["received_ns"], name))
    first = min(correct, default=(None, None))
    same_service_clock = have_clock and tuple(service_clock.get(key) for key in CLOCK[:2]) == clock[:2]
    reply, commit = fill_metrics(stage, commit_events, same_service_clock)
    metrics = {"9": unavailable("frozen B1 enforce_eager=true; validated below"),
               "10": external, "11_response": reply, "11_write_confirmed": commit,
               "12": metric(interval(dispatch, first[0]) if have_clock else None, source=first[1])}
    input_name = stage.name.split("-", 2)[-1]
    if previous is None:
        input_name += "-sf"
    frozen = read(stage.parent / "b1-inputs" / (input_name + (".json" if previous is None else "-input.json")))
    metrics["9"] = unavailable("frozen input enforce_eager=true") if frozen.get("config", {}).get("enforce_eager") is True else metric(reason="no CUDA graph capture-specific bounds or frozen eager config")
    if previous is None:
        cold = read(stage / "sf-admission/summary.json").get("cache_condition_passed") is True
        metrics.update({"3": metric(full if cold else None, load_gap if cold else "SF cold admission unavailable", "events + sf-admission/summary.json"),
                        "4": metric(init_ready), "4.1": metric(init), "4.2": metric(reg)})
    else:
        lookup, rec = covered(strc, "lookup"), covered(strc, "recovery")
        store_complete = interval(lookup[0] if lookup else None, full[1] if full else None)
        recovery = interval(rec[0] if rec else None, full[1] if full else None)
        metrics.update({
            "5": metric(store_complete, source="Store lookup to eight-rank full_weight_load"),
            "5.1": metric(covered(strc, "eviction"), source="successful observed Store evictions"),
            "5.2": metric(reason="exact Host pool publication absent; report pread envelope separately",
                          source="native Store partitions"),
            "5.2_pread_envelope": metric(covered(strc, "pread"), source="per-file earliest pread to last pread"),
            "5.3": metric(recovery, source="Store recovery request to eight-rank full_weight_load"),
            "6": metric(reason="old independently observable physical KV release start is absent"),
            "7.1": previous["own_shutdown"]["unregister"],
            "7.2": previous["own_shutdown"]["physical_release"],
            "7.3": previous["own_shutdown"]["host_clear"],
            "7.4": metric(init, source="b1_kv_tensor_init"),
            "7.5": metric(reg, source="b1_lmcache_register_transfer initial ACK"),
            "8": previous["own_shutdown"]["exit_and_drain"],
        })
        for phase, number in (("eviction", "5.1"), ("pread", "5.2")):
            if strc["phases"][phase]["status"] == "not_applicable":
                metrics[number] = unavailable("Store ledger confirms operation did not occur", zero=True)
        available = {"7.1": [x["bounds"] for x in previous["own_shutdown"]["unregister_ranks"]],
                     "7.2": [],
                     "7.3": [previous["own_shutdown"]["clear_bounds"]] if previous["own_shutdown"]["clear_bounds"] else [],
                     "7.4": worker_intervals(wrk, "kv_tensor_init") if init else [],
                     "7.5": worker_intervals(wrk, "initial_registration") if reg else []}
        seen = union([bounds for group in available.values() for bounds in group])
        weight = union([span["bounds"] for span in post_ranks] + [
            (row["begin_ns"], row["end_ns"])
            for phase in ("eviction", "pread", "h2d")
            if strc["phases"][phase]["status"] == "complete"
            for row in strc["phases"][phase]["records"]
        ])
        same_clock = have_clock and tuple(previous["clock"]) == clock
        missing = [name for name, spans in available.items() if not spans]
        metrics["7"] = dict(
            status="partial" if missing else "complete", missing=missing,
            observed_union_s=duration(seen) if seen and same_clock else None,
            observed_union_intervals=seen if same_clock else [],
            weight_overlap_observed_s=duration(intersect(seen, weight)) if seen and weight and same_clock else None,
            weight_overlap_basis="per-file PREAD and per-device H2D envelopes, eviction and rank postprocess spans; sub-envelope gaps unobserved",
            full_exact_union_s=None if missing or not same_clock else duration(seen))
    return dict(step=stage.name, clock=clock, metrics=metrics,
                phase_coverage={"full_weight": load_ranks, "full_weight_gap": load_gap,
                                "postprocess": post_ranks, "postprocess_gap": post_gap,
                                "worker": {n: dict(status=p["status"], ranks=p["observed_ranks"], issues=p["issues"])
                                           for n, p in wrk["phases"].items()},
                                "store": {n: dict(status=p["status"], observed_records=p["observed_records"], problems=p["problems"])
                                          for n, p in strc["phases"].items()}},
                own_shutdown=dict(unregister=metric(um, um_err, "worker unregister success ACK"),
                                  unregister_ranks=umr, reference_cleanup_observed_ranks=ref_rows,
                                  physical_release=physical, host_clear=metric(clear_bounds, source="kv-clear.json:HTTP+zero"),
                                  clear_bounds=clear_bounds,
                                  exit_and_drain=metric(shutdown, source="gpu-drain.json:later of exit and passed NVML sample")))


def summarize(run):
    init = read(run / "pool-init-timing.json")
    server_start = read(run / "lmcache/host-kv-server-start.json")
    clock = read(run / "lmcache/store-trace-clock.json")
    service_clock = (clock.get("clock") == "CLOCK_REALTIME" and bool(clock.get("boot_id"))
                     and (not server_start or tuple(clock.get(k) for k in CLOCK[:2]) ==
                          tuple(server_start.get(k) for k in CLOCK[:2])))
    pool_store = interval(init.get("store_begin_ns"), init.get("store_ready_ns"))
    pool_lmc = interval(init.get("kv_begin_ns"), init.get("kv_ready_ns"))
    pool_interval = interval(init.get("kv_begin_ns"), init.get("store_ready_ns"))
    pool_serial = pool_store is not None and pool_lmc is not None and pool_lmc[1] <= pool_store[0]
    summaries = []
    initial = read(run / "lmcache-initial-status.json").get("body", {}).get("storage_manager", {})
    receipt = initial.get("l1_manager", {}).get("v11_pool") or {}
    pool_native = interval(server_start.get("monotonic_ns"), receipt.get("end_ns"))
    pool_internal = interval(receipt.get("begin_ns"), receipt.get("end_ns"))
    primary_lmc = pool_native or pool_lmc
    native_serial = bool(primary_lmc and pool_store and primary_lmc[1] <= pool_store[0])
    commits = list(rows(run / "lmcache/store-events.jsonl"))
    for stage in sorted(p for p in run.iterdir() if p.is_dir() and worker.STAGE_NAME.match(p.name)
                        and not p.name.endswith("-guard")):
        summaries.append(stage_summary(stage, summaries[-1] if summaries else None, commits, clock if service_clock else {}))
    qualification = read(run / "qualification.json")
    complete = qualification.get("passed")
    if complete and len(summaries) != 7:
        raise ValueError("passed run lacks seven stage directories")
    return dict(run_id=run.name, round_status=complete,
                round_metrics={"1": metric(pool_store, source="pool-init-timing.json:Store Docker start to port ready"),
                               "2": metric(primary_lmc, source=("LMCache entrypoint start ? pool initialization complete"
                                                                   if pool_native else "Docker start ? healthy /status")),
                               "lmcache_pool_constructor": metric(pool_internal, source="pool init span in LMCache status"),
                               "lmcache_controller_ready": metric(pool_lmc, source="pool-init-timing.json"),
                               "init_serial_sum_s": duration([primary_lmc, pool_store]) if native_serial else None,
                               "init_wall": metric(interval(primary_lmc[0], pool_store[1]) if native_serial else None),
                               "controller_init_wall": metric(pool_interval if pool_serial else None),
                               "lmcache_server_process_start_ns": server_start.get("monotonic_ns")},
                stages=summaries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), indent=2))
