"""Offline V4 evidence calculations, not a runtime or campaign admission gate.

Reuse GLM C23 timing boundaries. Callers must separately verify source hashes,
clock identity, native reference provenance, cache state and physical cleanup.
"""

PHASES = (
    "device_setup_including_communication", "communication_groups",
    "weights_pipeline_including_allocation", "weight_ingestion",
    "quantization_postprocess", "kv_cache_allocation", "compile_graph_and_warmup",
)


def require(value, message):
    if not value:
        raise ValueError(message)


def timestamp(value):
    require(type(value) is int and value >= 0, "invalid monotonic timestamp")
    return value


def choice(response):
    require(len(response["choices"]) == 1, "expected one choice")
    result = response["choices"][0]
    tokens = result["token_ids"]
    require(isinstance(tokens, list) and tokens
            and all(type(t) is int and t >= 0 for t in tokens), "invalid token IDs")
    require(isinstance(result["message"]["content"], str), "invalid response text")
    return result


def response_checks(response, native_reference, purpose):
    """Both inputs are raw backend API responses; reference identity is external."""
    result, native = choice(response), choice(native_reference)
    checks = {"native_token_parity": result["token_ids"] == native["token_ids"]}
    if purpose == "business":
        checks["semantic_success"] = result["message"]["content"].strip() == "4"
        checks["natural_eos_observed"] = (
            result["finish_reason"] == "stop" and result["token_ids"][-1] == 1
            and (result.get("stop_reason") is None
                 or type(result["stop_reason"]) is int and result["stop_reason"] == 1)
        )
        checks["complete_business_success"] = (
            checks["semantic_success"] and checks["natural_eos_observed"]
        )
    elif purpose == "workload32":
        checks["fixed32_length_success"] = (
            len(result["token_ids"]) == 32 and result["finish_reason"] == "length"
        )
    else:
        raise ValueError("unknown request purpose")
    return checks


def timing_metrics(record, events, complete_business_success):
    """Recompute GLM fields; missing backend first-token evidence stays missing."""
    metrics = {}
    if "preparation_begin_ns" in record or "preparation_complete_ns" in record:
        begin = timestamp(record["preparation_begin_ns"])
        end = timestamp(record["preparation_complete_ns"])
        require(begin <= end, "preparation events out of order")
        metrics["preparation_s"] = (end - begin) / 1e9
    response = record.get("correct_business_response_ns")
    require(type(complete_business_success) is bool, "business success must be boolean")
    require((response is not None) == complete_business_success,
            "correct response event must match complete business success")
    if "startup_command_ns" not in record:
        require(response is None, "response without startup")
        return metrics
    command = timestamp(record["startup_command_ns"])
    if "business_trigger_ns" not in record:
        require(response is None and "first_token_observed_ns" not in record,
                "business evidence without trigger")
        return metrics
    trigger = timestamp(record["business_trigger_ns"])
    require(command <= trigger, "trigger predates startup")
    first = [e for e in events if e["name"] == "backend_first_token_observed"
             and e.get("request_id") == record["business_request_id"]]
    require(len(first) <= 1, "duplicate backend first-token events")
    if first:
        token = timestamp(first[0]["monotonic_ns"])
        require(trigger <= token, "first token predates business trigger")
        require(record.get("first_token_observed_ns", token) == token,
                "first-token field differs from backend evidence")
        metrics["command_to_first_token_s"] = (token - command) / 1e9
        metrics["trigger_to_first_token_s"] = (token - trigger) / 1e9
    else:
        require("first_token_observed_ns" not in record,
                "first-token field without backend evidence")
    if response is not None:
        response = timestamp(response)
        require(trigger <= response, "response predates trigger")
        require(not first or token <= response, "first token after response")
        metrics["command_to_correct_business_response_s"] = (response - command) / 1e9
        metrics["trigger_to_correct_business_response_s"] = (response - trigger) / 1e9
    return metrics


def phase_metrics(events, startup_command_ns):
    """GLM all-rank host envelope; nested phases must not be summed."""
    require(not any(e["name"] in {p + "_failed" for p in PHASES} for e in events),
            "failed startup phase cannot produce accepted metrics")
    ranks = {}
    for event in events:
        if event.get("rank") is not None:
            rank = event["rank"]
            require(type(rank) is int and 0 <= rank < 8, "invalid TP8 rank")
            key = event["container_hostname"], event["pid"]
            require(key not in ranks or ranks[key] == rank, "process rank changed")
            ranks[key] = rank
    metrics = {}
    for phase in PHASES:
        spans = []
        for rank in range(8):
            rows = [e for e in events if e["name"] in (phase + "_begin", phase + "_end")
                    and ranks.get((e["container_hostname"], e["pid"])) == rank]
            rows.sort(key=lambda e: timestamp(e["monotonic_ns"]))
            require(len(rows) == 2 and [e["name"] for e in rows]
                    == [phase + "_begin", phase + "_end"], "missing/duplicate rank phase")
            spans.append((rows[0]["monotonic_ns"], rows[1]["monotonic_ns"]))
        metrics["phase_" + phase + "_span_s"] = (
            max(end for _, end in spans) - min(begin for begin, _ in spans)
        ) / 1e9
    controls = {}
    for name in ("backend_engine_create_begin", "backend_engine_ready", "router_instance_published"):
        rows = [e for e in events if e["name"] == name]
        require(len(rows) == 1, "missing/duplicate control event")
        controls[name] = timestamp(rows[0]["monotonic_ns"])
    begin = controls["backend_engine_create_begin"]
    end = controls["backend_engine_ready"]
    require(begin <= end, "backend construction out of order")
    published = controls["router_instance_published"]
    command = timestamp(startup_command_ns)
    require(command <= published, "instance publication predates startup")
    metrics["backend_construct_s"] = (end - begin) / 1e9
    metrics["command_to_route_publication_s"] = (published - command) / 1e9
    metrics["weight_ingestion_span_s"] = metrics["phase_weight_ingestion_span_s"]
    metrics["weights_pipeline_span_s"] = metrics["phase_weights_pipeline_including_allocation_span_s"]
    return metrics


def release_coverage(rows, delete_ack_ns):
    """Validate temporal coverage only; does not approve physical release."""
    timestamp(delete_ack_ns)
    require(len(rows) == 61 and [r["second"] for r in rows] == list(range(61)),
            "release interval incomplete")
    times = [timestamp(r["monotonic_ns"]) for r in rows]
    require(times[0] >= delete_ack_ns and times[-1] - times[0] >= 60_000_000_000,
            "release shorter than 60s or predates delete")
    require(all(0 < b - a <= 5_000_000_000 for a, b in zip(times, times[1:])),
            "release monitor gap")
    for row, now in zip(rows, times):
        snapshot = row["host_gpu_snapshot"]
        begin, end = timestamp(snapshot["sample_begin_ns"]), timestamp(snapshot["sample_end_ns"])
        require(timestamp(row["delete_ack_ns"]) == delete_ack_ns, "delete acknowledgement changed")
        require(delete_ack_ns <= begin <= end <= now and now - begin <= 5_000_000_000,
                "stale or invalid post-delete GPU sample")
    return {"sample_span_seconds": (times[-1] - times[0]) / 1e9,
            "scope": "temporal coverage only; GPU/process/IPC cleanup not validated"}
