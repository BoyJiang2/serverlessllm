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


def validate_business_event_order(events, request_id, startup_ns, trigger_ns, received_ns):
    controls = {}
    for name in ("backend_engine_create_begin", "backend_engine_ready", "router_instance_published",
                 "backend_first_token_observed", "backend_generation_complete"):
        rows = [e for e in events if e["name"] == name
                and (name not in ("backend_first_token_observed", "backend_generation_complete")
                     or e.get("request_id") == request_id)]
        require(len(rows) == 1, "missing/duplicate business control event: " + name)
        controls[name] = timestamp(rows[0]["monotonic_ns"])
    # Instance publication follows engine readiness; cold controller registration is separate.
    ordered = [timestamp(startup_ns)] + list(controls.values()) + [timestamp(received_ns)]
    require(ordered == sorted(ordered), "business engine/publication/token/completion order differs")
    require(timestamp(trigger_ns) <= controls["backend_first_token_observed"],
            "first token predates business trigger")


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
    require(timestamp(startup_command_ns) <= begin <= end, "backend construction out of order")
    published = controls["router_instance_published"]
    command = timestamp(startup_command_ns)
    require(command <= end <= published, "instance publication precedes engine readiness")
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


def cache_scan_checks(scan, expected_files, vectors):
    """Validate GLM full mincore evidence, not its runtime provenance.

    expected_files is an independently frozen source_path -> byte count mapping.
    vectors contains decompressed bytes; no file or payload is read here.
    """
    import hashlib

    require(isinstance(expected_files, dict) and expected_files, "empty cache inventory")
    require(scan["mode"] in ("cold", "hot", "check"), "invalid cache mode")
    require(scan["kind"] in ("store_inputs", "safetensors_inputs"), "invalid cache kind")
    rows = scan["files"]
    require(isinstance(rows, list) and len(rows) == len(expected_files), "cache inventory coverage")
    paths, names = set(), set()
    for row in rows:
        path, name = row["source_path"], row["vector"]
        require(path in expected_files and path not in paths and name not in names,
                "cache inventory duplicate or unexpected file")
        paths.add(path)
        names.add(name)
        size, page = row["bytes"], row["page_size"]
        require(type(size) is int and size > 0 and size == expected_files[path]
                and type(expected_files[path]) is int, "cache file size mismatch")
        require(type(page) is int and page > 0 and page & (page - 1) == 0,
                "invalid cache page size")
        pages = (size + page - 1) // page
        flags = vectors[name]
        require(type(flags) is bytes and len(flags) == pages, "incomplete mincore vector")
        require(hashlib.sha256(flags).hexdigest() == row["vector_sha256"], "mincore checksum")
        resident = sum(flag & 1 for flag in flags)
        require(type(row["pages"]) is int and row["pages"] == pages
                and type(row["resident_pages"]) is int and row["resident_pages"] == resident,
                "mincore counters differ from vector")
        if scan["mode"] != "check":
            require(resident == (0 if scan["mode"] == "cold" else pages),
                    "declared cache condition failed")
    require(set(vectors) == names, "unexpected cache vectors")
    require(scan["cache_condition_passed"] is (None if scan["mode"] == "check" else True),
            "cache summary status mismatch")
    order = [timestamp(scan[key]) for key in (
        "preparation_begin_ns", "preparation_complete_ns", "verification_begin_ns",
        "verification_complete_ns", "total_cache_admission_end_ns")]
    require(order == sorted(order), "cache preparation/scan out of order")
    return {"files": len(rows), "pages": sum(row["pages"] for row in rows),
            "scope": "Linux client page-cache evidence only; provenance and storage-server cache unverified"}


def cache_timing_checks(record, target, other, condition):
    """Check timing after BOTH scans have passed cache_scan_checks.

    Clock/run identity, frozen inventories, source sealing, native pool confirmation,
    resource admission and failure-denominator accounting remain caller obligations.
    """
    conditions = {
        "safetensors_disk_cold": ("safetensors_inputs", "cold"),
        "safetensors_disk_hot": ("safetensors_inputs", "hot"),
        "store_disk_cold": ("store_inputs", "cold"),
        "store_pool_hit": ("store_inputs", "check"),
    }
    require(condition in conditions, "unknown cache condition")
    require((target["kind"], target["mode"]) == conditions[condition], "target cache mode mismatch")
    require(other["kind"] in ("store_inputs", "safetensors_inputs")
            and other["kind"] != target["kind"] and other["mode"] == "check",
            "counterpart cache scan missing")
    begin = timestamp(record["preparation_begin_ns"])
    complete = timestamp(record["preparation_complete_ns"])
    command = timestamp(record["startup_command_ns"])
    deadline = timestamp(record["attempt_deadline_ns"])
    require(begin < deadline <= begin + 1200_000_000_000, "attempt excludes preparation or exceeds 1200s")
    sequence = [begin, timestamp(other["preparation_begin_ns"]),
                timestamp(other["total_cache_admission_end_ns"]),
                timestamp(target["preparation_begin_ns"]),
                timestamp(target["total_cache_admission_end_ns"]), complete, command]
    require(sequence == sorted(sequence) and command < deadline, "cache/startup boundary out of order")
    if condition.startswith("store_"):
        validated = timestamp(record["store_validation_complete_ns"])
        require(validated <= sequence[1], "Store payload hashing must precede cache preparation")
        require(record["store_registration_reused"] is True, "timed Store registration may rehash payload")
    if condition == "store_pool_hit":
        confirm_begin = timestamp(record["pool_confirmation_begin_ns"])
        confirm_end = timestamp(record["pool_confirmation_complete_ns"])
        require(record["pool_confirmed"] is True
                and sequence[4] <= confirm_begin <= confirm_end <= complete,
                "pool requires a fresh complete-cohort confirmation")
    return {"preparation_s": (complete - begin) / 1e9,
            "scope": "offline cache/timing consistency only; no runtime admission or residency lease"}
