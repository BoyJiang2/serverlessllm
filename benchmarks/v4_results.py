"""Read-only V4 journal replay and GLM-field export; no runtime qualification."""
import argparse
import json
from pathlib import Path

from sllm_store.event_trace import validate_event_window
from sllm_store.v4_state import write_once
from v4_attempts import collect_attempts, digest, frozen_protocol
from v4_evidence import (phase_metrics, release_coverage, require, response_checks,
                         timestamp, timing_metrics, validate_business_event_order)


def business_evidence(record, observation, identity, missing, allow_partial=False):
    """Separate business correctness from native parity and trace completeness."""
    try:
        checks = response_checks(observation["response"], observation["native_reference"], "business")
    except (KeyError, TypeError, ValueError) as error:
        if not allow_partial:
            raise
        checks = dict(response_valid=False, native_token_parity=False, complete_business_success=False)
        missing.append("invalid_business_response: " + type(error).__name__)
    received = timestamp(observation["received_ns"])
    require(record["business_trigger_ns"] <= received, "response predates trigger")
    complete = checks["complete_business_success"]
    if complete:
        record["correct_business_response_ns"] = received
    events = observation.get("events", [])
    if events:
        checked = validate_event_window(events, identity["run_id"], record["startup_command_ns"],
            received, boot_id=identity["boot_id"], time_namespace=identity["time_namespace"])
        require(len(checked) == len(events), "journal contains out-of-window events")
        first = [e for e in checked if e["name"] == "backend_first_token_observed"
                 and e.get("request_id") == record["business_request_id"]]
        require(len(first) <= 1, "duplicate business first-token event")
        if first:
            record["first_token_observed_ns"] = first[0]["monotonic_ns"]
        else:
            require(allow_partial, "missing business first-token event")
            missing.append("business_first_token")
        try:
            validate_business_event_order(checked, record["business_request_id"],
                record["startup_command_ns"], record["business_trigger_ns"], received)
        except ValueError as error:
            if not allow_partial:
                raise
            missing.append("business_event_order: " + str(error))
        events = checked
    else:
        missing.append("business_events")
    metrics = timing_metrics(record, events, complete)
    if events:
        try:
            metrics.update(phase_metrics(events, record["startup_command_ns"]))
        except ValueError as error:
            if not allow_partial:
                raise
            missing.append("startup_phases: " + str(error))
    return checks, metrics


def reconstruct(entry, frozen):
    """Flatten checked journal entries; missing old failure proof remains unknown."""
    result = dict(attempt_id=frozen["attempt_id"], block=frozen["block"], condition=frozen["condition"],
        status=entry["status"], success=False, consumed=entry["consumed"], terminal=entry["terminal"],
        runtime_qualified=False, physical_cleanup_verified=False, metrics={}, component_checks={},
        missing_evidence=[], correct_business_response_observed=False)
    rows = entry.get("journal", [])
    if not rows:
        if entry["consumed"]:
            result["missing_evidence"].append("origin")
        return result
    origin, identity = rows[0]["data"], rows[0]["identity"]
    require(timestamp(origin["preparation_begin_ns"]) == rows[0]["monotonic_ns"]
            and origin["preparation_begin_ns"] < timestamp(origin["attempt_deadline_ns"])
            <= origin["preparation_begin_ns"] + frozen.get("attempt_timeout_seconds", 1200) * 1_000_000_000,
            "origin deadline/clock differs")
    require(origin.get("attempt_timeout_seconds", 1200) == frozen.get("attempt_timeout_seconds", 1200),
            "origin changed frozen attempt timeout")
    record = dict(origin)
    result.update(execution_run_id=identity["run_id"], code_commit=identity["code_commit"],
        source_manifest_sha256=identity["source_manifest_sha256"],
        preregistration_sha256=identity["preregistration_sha256"], boot_id=identity["boot_id"],
        time_namespace=identity["time_namespace"])
    metrics, components, missing = result["metrics"], result["component_checks"], result["missing_evidence"]
    for index, row in enumerate(rows[1:], 1):
        stage, data, now = row["stage"], row["data"], timestamp(row["monotonic_ns"])
        if stage != "result":
            require(now < timestamp(origin["attempt_deadline_ns"]), "stage exceeded original attempt deadline")
        if stage == "startup":
            saved = data["record"]
            require(all(saved[k] == origin[k] for k in origin), "startup changed frozen origin")
            require(saved["startup_command_ns"] == now, "startup timestamp differs")
            record = dict(saved)
            metrics.update(timing_metrics(record, [], False))
        elif stage == "trigger":
            require(record["startup_command_ns"] <= timestamp(data["register_complete_ns"]) <= now,
                    "registration/trigger out of order")
            record.update(register_complete_ns=data["register_complete_ns"], business_trigger_ns=now,
                          business_request_id=data["business_request_id"])
        elif stage == "business":
            observation = dict(response=data["response"], native_reference=data["native_reference"],
                               received_ns=now, events=data["events"])
            # Earlier journals predate raw-response binding and the completion-event gate.
            checks, calculated = business_evidence(record, observation, identity, missing,
                                                   allow_partial="observation_sha256" not in data)
            if "observation_sha256" not in data:
                missing[:] = ["legacy_" + item if item.startswith("business_event_order: ") else item
                              for item in missing]
            require(all(checks.values()), "invalid accepted business stage")
            require(data["checks"] == checks and data["metrics"] == calculated,
                    "stored business checks/metrics differ from replay")
            metrics.update(calculated)
            components["business"] = checks
        elif stage == "workload32":
            checks = response_checks(data["response"], data["native_reference"], "workload32")
            require(all(checks.values()) and checks == data["checks"], "invalid accepted fixed32 stage")
            components["workload32"] = checks
        elif stage == "delete":
            require(rows[index - 1]["monotonic_ns"] <= timestamp(data["delete_command_ns"]) <= now,
                    "delete acknowledgement out of order")
            record.update(delete_command_ns=data["delete_command_ns"], delete_complete_ns=now)
        elif stage == "release":
            coverage = release_coverage(data["rows"], record["delete_complete_ns"])
            require(data["rows"][-1]["monotonic_ns"] <= now and coverage == data["coverage"],
                    "release coverage differs from replay")
            result["release_coverage"] = coverage
        elif stage == "result":
            if data["status"] == "evidence_complete_unqualified":
                require(rows[index - 1]["stage"] == "release"
                        and all(item.startswith("legacy_business_event_order: ") for item in missing),
                        "unqualified completion requires all evidence stages")
                require(timestamp(data["completed_ns"]) == now and now < origin["attempt_deadline_ns"],
                        "completion boundary differs")
                result["evidence_complete_ns"] = now
            else:
                require(timestamp(data["failure_ns"]) == now
                        and data["failed_stage"] == rows[index - 1]["stage"], "failure boundary differs")
                saved = data.get("record")
                if saved is not None:
                    require(all(saved.get(k) == value for k, value in record.items()),
                            "failure snapshot changed earlier boundary")
                observation = data.get("business_observation")
                if observation is not None and "business" not in components:
                    checks, calculated = business_evidence(record, observation, identity, missing, allow_partial=True)
                    require(observation["received_ns"] <= now, "response follows failure")
                    components["business"] = checks
                    metrics.update(calculated)
                fixed = data.get("workload32_observation")
                if fixed is not None and "workload32" not in components:
                    require(timestamp(fixed["received_ns"]) <= now, "fixed response follows failure")
                    try:
                        components["workload32"] = response_checks(
                            fixed["response"], fixed["native_reference"], "workload32")
                    except (KeyError, TypeError, ValueError) as error:
                        components["workload32"] = dict(response_valid=False)
                        missing.append("invalid_workload32_response: " + type(error).__name__)
                observed = "correct_business_response_ns" in record
                reported = data["correct_business_response_observed"]
                require(type(reported) is bool, "invalid response observation flag")
                if reported and not observed:
                    result["correct_business_response_observed"] = None
                    missing.append("legacy_business_observation")
                else:
                    require(reported == observed, "response observation contradicts journal")
                timed_out = data["status"] == "timeout"
                if not timed_out:
                    require(now < origin["attempt_deadline_ns"], "late failure must be timeout")
                censor = (now - record["startup_command_ns"]) / 1e9 if "startup_command_ns" in record else None
                require(data["startup_censor_elapsed_seconds"] == censor, "failure censor duration differs")
                right_censored = None if result["correct_business_response_observed"] is None else timed_out and not observed
                if right_censored is not None:
                    require(data["right_censored"] is right_censored, "right censor flag contradicts observations")
                result.update(failure_ns=now, failed_stage=data["failed_stage"], error_type=data["error_type"],
                              error=data["error"], right_censored=right_censored,
                              startup_censor_elapsed_seconds=censor, cleanup_pending=data["cleanup_pending"])
    # Fields are copied explicitly so source records cannot replace qualification.
    for key in ("preparation_begin_ns", "attempt_deadline_ns", "preparation_complete_ns", "startup_command_ns",
                "register_complete_ns", "business_trigger_ns", "business_request_id", "correct_business_response_ns",
                "first_token_observed_ns", "delete_command_ns", "delete_complete_ns"):
        if key in record:
            result[key] = record[key]
    if result["correct_business_response_observed"] is not None:
        result["correct_business_response_observed"] = "correct_business_response_ns" in record
    return result


def replay(preregistration, prereg_sha, execution_roots):
    frozen = frozen_protocol(preregistration, prereg_sha)
    entries = collect_attempts(preregistration, prereg_sha, execution_roots)
    results = [reconstruct(entry, row) for entry, row in zip(entries, frozen)]
    counts = {}
    for record in results:
        group = counts.setdefault(record["condition"], dict(preregistered=0, started=0, unstarted=0, incomplete=0,
            failed=0, timeout=0, evidence_complete_unqualified=0, verified_business_responses=0,
            unknown_business_observations=0, right_censored=0, failed_after_stage={},
            formal_successes=0, formal_success_rate=None))
        group["preregistered"] += 1
        group["started"] += int(record["consumed"])
        group[record["status"]] += 1
        group["verified_business_responses"] += int(record["correct_business_response_observed"] is True)
        group["unknown_business_observations"] += int(record["correct_business_response_observed"] is None)
        group["right_censored"] += int(record.get("right_censored") is True)
        if "failed_stage" in record:
            stage = record["failed_stage"]
            group["failed_after_stage"][stage] = group["failed_after_stage"].get(stage, 0) + 1
    return results, dict(schema=1, scope="Unqualified evidence accounting only; no performance or formal success inference",
                         runtime_qualified=False, conditions=counts), entries


def export_results(preregistration, prereg_sha, execution_roots, output):
    results, summary, entries = replay(preregistration, prereg_sha, execution_roots)
    output = Path(output)
    require(output.is_absolute(), "export output must be absolute")
    for root in execution_roots:
        require(not output.resolve().is_relative_to(Path(root).resolve()), "export must be outside execution roots")
    # Bind output to the exact bytes collected, and reject a change during replay.
    for entry in entries:
        for receipt in entry.get("journal_files", []) + entry.get("response_files", []):
            path = Path(entry["directory"]) / receipt["path"]
            require(not path.is_symlink() and digest(path.read_bytes()) == receipt["sha256"],
                    "journal changed during replay")
    protocol_bytes = Path(preregistration).read_bytes()
    require(digest(protocol_bytes) == prereg_sha, "protocol changed during replay")
    output.mkdir(exist_ok=False)
    for record in results:
        directory = output / record["attempt_id"]
        directory.mkdir()
        write_once(directory / "result.json", record)
    write_once(output / "summary.json", summary)
    write_once(output / "manifest.json", dict(schema=1, artifact_kind="derived_not_execution",
        exporter_source_sha256=digest(Path(__file__).read_bytes()),
        protocol_document=json.loads(protocol_bytes),
        preregistration_sha256=prereg_sha, execution_roots=[str(Path(p).resolve()) for p in execution_roots],
        journal_inputs=[dict(attempt_id=e["attempt_id"], directory=e.get("directory"),
                            files=e.get("journal_files", []) + e.get("response_files", [])) for e in entries],
        output_files=[dict(path=str(p.relative_to(output)), sha256=digest(p.read_bytes()))
                      for p in sorted(output.rglob("*.json"))]))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preregistration", required=True)
    parser.add_argument("--prereg-sha256", required=True)
    parser.add_argument("--execution-root", action="append", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export_results(args.preregistration, args.prereg_sha256, args.execution_root, args.output)))


if __name__ == "__main__":
    main()
