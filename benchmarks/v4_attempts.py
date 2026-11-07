"""Durable V4 attempt evidence; never launches work or awards formal PASS."""
import hashlib
import json
import os
from pathlib import Path
import time

from sllm_store.event_trace import process_identity, read_event_window
from sllm_store.v4_state import checked_file, write_once
from v4_evidence import (cache_scan_checks, cache_timing_checks, phase_metrics,
                         release_coverage, require, response_checks, timestamp,
                         timing_metrics, validate_business_event_order)

STAGES = ("origin", "startup", "trigger", "business", "workload32", "delete", "release", "result")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def frozen_protocol(path, expected_sha):
    data = Path(path).read_bytes()
    require(digest(data) == expected_sha, "preregistration SHA changed")
    protocol = json.loads(data)
    rows = protocol["attempts"]
    ids = [row["attempt_id"] for row in rows]
    schema = protocol.get("schema", 1)
    require(type(schema) is int and schema in (1, 2), "unsupported protocol schema")
    blocks = range(1, 4)
    if schema == 2:
        require(protocol.get("campaign") == "v4_4x10_20261107"
                and type(protocol.get("denominator_per_condition")) is int
                and protocol.get("denominator_per_condition") == 10,
                "new campaign requires four conditions times ten")
        require(all(isinstance(value, str) and value.startswith("v4n40_") for value in ids),
                "new campaign requires separate IDs")
        blocks = range(1, 11)
    count = 4 * len(blocks)
    require(len(rows) == count and len(set(ids)) == count, "frozen ID count differs")
    conditions = {"safetensors_disk_cold", "safetensors_disk_hot", "store_disk_cold", "store_pool_hit"}
    require(all(type(row["block"]) is int and row["block"] in blocks
                and row["condition"] in conditions
                and isinstance(row["attempt_id"], str)
                and row["attempt_id"].replace("_", "").isalnum()
                for row in rows), "invalid frozen attempt")
    require({(r["block"], r["condition"]) for r in rows}
            == {(b, c) for b in blocks for c in conditions}, "incomplete paired blocks")
    timeout = protocol["attempt_timeout_seconds"]
    require(type(timeout) is int and timeout in ((1200, 3600) if schema == 2 else (1200,)),
            "unsupported frozen attempt timeout")
    return [dict(row, attempt_timeout_seconds=timeout) for row in rows] if schema == 2 else rows


class AttemptJournal:
    """One writer, no resume. Use as a context manager around external operations.

    Publication is atomic/no-replace; directory claims remain consumed after a
    crash. This is evidence bookkeeping, not a resource or runtime admission gate.
    """
    def __init__(self, root, attempt_id, preregistration, prereg_sha, run_id, work_end_ns, source, execution_roots):
        rows = frozen_protocol(preregistration, prereg_sha)
        matches = [row for row in rows if row["attempt_id"] == attempt_id]
        require(len(matches) == 1, "unknown frozen attempt ID")
        require(isinstance(run_id, str) and run_id, "missing run identity")
        require(set(source) == {"code_commit", "source_manifest_sha256"}
                and all(isinstance(source[k], str) and len(source[k]) == size
                        and all(c in "0123456789abcdef" for c in source[k])
                        for k, size in (("code_commit", 40), ("source_manifest_sha256", 64))),
                "missing frozen source identity")
        require(Path(root).resolve() in {Path(p).resolve() for p in execution_roots},
                "current root missing from declared execution history")
        history = collect_attempts(preregistration, prereg_sha, execution_roots)
        require(all(not row["consumed"] or row["status"] == "evidence_complete_unqualified"
                    for row in history), "previous failed/incomplete attempt blocks continuation")
        unstarted = [row["attempt_id"] for row in history if not row["consumed"]]
        require(unstarted and attempt_id == unstarted[0], "frozen attempt order or consumed ID")
        begin = time.monotonic_ns()
        require(timestamp(work_end_ns) > begin, "resource work window exhausted")
        self.writer_identity = process_identity()
        self.identity = dict(self.writer_identity, run_id=run_id, preregistration_sha256=prereg_sha, **source)
        self.business_observation = None
        self.workload32_observation = None
        self.response_sha256 = {}
        self.record = dict(matches[0], preparation_begin_ns=begin,
                           attempt_deadline_ns=min(work_end_ns, begin + matches[0].get("attempt_timeout_seconds", 1200) * 1_000_000_000))
        self.path = Path(root) / attempt_id
        require(self.path.parent.is_dir() and not self.path.parent.is_symlink(), "invalid execution root")
        self.path.mkdir(exist_ok=False)
        descriptor = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.sequence, self.previous, self.stage = 0, None, None
        self.last_time = begin
        self._append("origin", self.record, begin)

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        if self.stage == "result":
            return False
        if error is None:
            error = RuntimeError("attempt scope ended without complete evidence")
        try:
            self.fail(error)
        except BaseException as record_error:
            if kind is not None:
                error.sllm_record_error = type(record_error).__name__
                return False
            raise
        if kind is None:
            raise error
        return False

    def _append(self, stage, data, now):
        require(process_identity() == self.writer_identity, "attempt writer process changed")
        require(self.stage != "result", "attempt already terminal")
        require(stage == "result" or STAGES[self.sequence] == stage, "attempt transition out of order")
        require(timestamp(now) >= self.last_time, "attempt clock moved backwards")
        observed = time.monotonic_ns()
        require(now <= observed, "attempt event lies in the future")
        if stage != "result" and observed >= self.record["attempt_deadline_ns"]:
            raise TimeoutError("attempt deadline exhausted")
        row = dict(schema=1, identity=self.identity, attempt_id=self.record["attempt_id"],
                   sequence=self.sequence, previous_sha256=self.previous, stage=stage,
                   monotonic_ns=now, data=data)
        # Validate JSON before any publication. Never let callers replace identity.
        row = json.loads(json.dumps(row, allow_nan=False))
        path = self.path / f"{self.sequence:02d}-{stage}.json"
        write_once(path, row)
        self.previous = digest(path.read_bytes())
        self.sequence += 1
        self.stage = stage
        self.last_time = now
        if stage != "result" and time.monotonic_ns() >= self.record["attempt_deadline_ns"]:
            raise TimeoutError("attempt publication exceeded deadline")

    def startup(self, preparation, target, target_files, target_vectors,
                other, other_files, other_vectors, cache_identity):
        require(cache_identity == {k: self.identity[k] for k in
                                  ("run_id", "boot_id", "time_namespace", "clock_id")},
                "cache run/clock identity differs")
        target_check = cache_scan_checks(target, target_files, target_vectors)
        other_check = cache_scan_checks(other, other_files, other_vectors)
        allowed = {"store_validation_complete_ns", "store_registration_reused",
                   "pool_confirmation_begin_ns", "pool_confirmation_complete_ns", "pool_confirmed"}
        require(set(preparation) <= allowed, "preparation cannot replace frozen attempt fields")
        now = time.monotonic_ns()
        record = dict(self.record, **preparation, preparation_complete_ns=now, startup_command_ns=now)
        cache_timing_checks(record, target, other, self.record["condition"],
                            self.record.get("attempt_timeout_seconds", 1200))
        self.record = record
        self._append("startup", dict(record=record, cache_identity=cache_identity,
                     target=target, target_files=target_files, target_check=target_check,
                     other=other, other_files=other_files, other_check=other_check), now)

    def trigger(self, register_complete_ns, request_id):
        now = time.monotonic_ns()
        require(self.record["startup_command_ns"] <= timestamp(register_complete_ns) <= now,
                "registration/trigger out of order")
        require(isinstance(request_id, str) and request_id, "missing business request identity")
        self._append("trigger", dict(register_complete_ns=register_complete_ns,
                                    business_request_id=request_id), now)
        self.record.update(register_complete_ns=register_complete_ns,
                           business_trigger_ns=now, business_request_id=request_id)

    def _save_response(self, purpose, response, native_reference, received_ns):
        require(process_identity() == self.writer_identity, "attempt writer process changed")
        require(self.stage != "result", "attempt already terminal")
        path = self.path / (purpose + ".response")
        write_once(path, dict(schema=1, identity=self.identity,
                   observation=dict(response=response, native_reference=native_reference,
                                    received_ns=received_ns)))
        raw = path.read_bytes()
        self.response_sha256[purpose] = digest(raw)
        return json.loads(raw)["observation"]

    def business(self, response, native_reference, event_directory):
        received = time.monotonic_ns()
        self.business_observation = self._save_response("business", response, native_reference, received)
        response, native_reference = (self.business_observation["response"],
                                      self.business_observation["native_reference"])
        checks = response_checks(response, native_reference, "business")
        if checks["complete_business_success"]:
            self.record["correct_business_response_ns"] = received
        require(all(checks.values()), "business/native reference checks failed")
        events = read_event_window(event_directory, self.identity["run_id"],
            self.record["startup_command_ns"], received, boot_id=self.identity["boot_id"],
            time_namespace=self.identity["time_namespace"])
        self.business_observation["events"] = events
        validate_business_event_order(events, self.record["business_request_id"],
                                     self.record["startup_command_ns"],
                                     self.record["business_trigger_ns"], received)
        first = [e for e in events if e["name"] == "backend_first_token_observed"
                 and e.get("request_id") == self.record["business_request_id"]]
        require(len(first) == 1, "missing/duplicate business first-token event")
        record = dict(self.record, correct_business_response_ns=received,
                      first_token_observed_ns=first[0]["monotonic_ns"])
        metrics = timing_metrics(record, events, True)
        metrics.update(phase_metrics(events, record["startup_command_ns"]))
        self.record = record
        self._append("business", dict(response=response, native_reference=native_reference,
                     checks=checks, metrics=metrics, events=events,
                     observation_sha256=self.response_sha256["business"]), received)

    def workload32(self, response, native_reference):
        received = time.monotonic_ns()
        self.workload32_observation = self._save_response("workload32", response, native_reference, received)
        response, native_reference = (self.workload32_observation["response"],
                                      self.workload32_observation["native_reference"])
        checks = response_checks(response, native_reference, "workload32")
        require(all(checks.values()), "fixed32/native reference checks failed")
        self._append("workload32", dict(response=response, native_reference=native_reference,
                                       checks=checks, observation_sha256=self.response_sha256["workload32"]), received)

    def delete(self, delete_command_ns):
        now = time.monotonic_ns()
        require(self.last_time <= timestamp(delete_command_ns) <= now,
                "delete acknowledgement out of order")
        self._append("delete", dict(delete_command_ns=delete_command_ns), now)
        self.record.update(delete_command_ns=delete_command_ns, delete_complete_ns=now)

    def release(self, rows):
        now = time.monotonic_ns()
        coverage = release_coverage(rows, self.record["delete_complete_ns"])
        require(rows[-1]["monotonic_ns"] <= now, "release evidence lies in the future")
        self._append("release", dict(coverage=coverage, rows=rows), now)
        self._append("result", dict(status="evidence_complete_unqualified", success=False,
            checks_passed=True, runtime_qualified=False, physical_cleanup_verified=False,
            completed_ns=now), now)

    def fail(self, error):
        now = time.monotonic_ns()
        timed_out = isinstance(error, TimeoutError) or now >= self.record["attempt_deadline_ns"]
        self._append("result", dict(status="timeout" if timed_out else "failed", success=False,
            failed_stage=self.stage, error_type=type(error).__name__, error=str(error),
            failure_ns=now, correct_business_response_observed="correct_business_response_ns" in self.record,
            right_censored=timed_out and "correct_business_response_ns" not in self.record,
            startup_censor_elapsed_seconds=(now - self.record["startup_command_ns"]) / 1e9
                if "startup_command_ns" in self.record else None,
            cleanup_pending=True, runtime_qualified=False, record=self.record,
            business_observation=self.business_observation,
            workload32_observation=self.workload32_observation,
            response_sha256=self.response_sha256), now)


def collect_attempts(preregistration, prereg_sha, execution_roots):
    """Inspect all declared roots. Incomplete/corrupt/duplicate IDs block admission."""
    attempts = frozen_protocol(preregistration, prereg_sha)
    expected = {row["attempt_id"]: row for row in attempts}
    roots = [Path(root) for root in execution_roots]
    require(roots and len({p.resolve() for p in roots}) == len(roots), "duplicate/missing execution roots")
    found = {}
    for root in roots:
        require(root.is_dir() and not root.is_symlink(), "invalid execution root")
        for directory in root.iterdir():
            require(directory.name in expected and directory.is_dir() and not directory.is_symlink(),
                    "unexpected/linked attempt entry")
            require(directory.name not in found, "attempt ID appears in multiple executions")
            entries = list(directory.iterdir())
            raw_files = {p.name.removesuffix(".response"): p for p in entries
                         if p.name in ("business.response", "workload32.response")}
            files = sorted(p for p in entries if p not in raw_files.values())
            previous, identity, last, rows, receipts = None, None, None, [], []
            for sequence, path in enumerate(files):
                require(not path.is_symlink() and path.is_file(), "linked/non-file journal entry")
                raw = path.read_bytes()
                row = json.loads(raw)
                stage = row["stage"]
                require(type(row["schema"]) is int and row["schema"] == 1
                        and stage in STAGES and path.name == f"{sequence:02d}-{stage}.json"
                        and type(row["sequence"]) is int and row["sequence"] == sequence
                        and row["previous_sha256"] == previous, "broken attempt journal chain")
                require(stage == "result" and sequence > 0 or STAGES[sequence] == stage,
                        "attempt journal transition out of order")
                require(last is None or last["stage"] != "result", "entries after terminal result")
                require(row["attempt_id"] == directory.name
                        and row["identity"]["preregistration_sha256"] == prereg_sha,
                        "attempt identity differs")
                if identity is None:
                    identity = row["identity"]
                require(row["identity"] == identity, "attempt identity changed")
                now = timestamp(row["monotonic_ns"])
                require(last is None or timestamp(last["monotonic_ns"]) <= now, "journal clock moved backwards")
                if sequence == 0:
                    require(all(row["data"][k] == expected[directory.name][k]
                                for k in ("attempt_id", "condition", "block")), "origin differs from frozen attempt")
                rows.append(row)
                last, previous = row, digest(raw)
                receipts.append(dict(path=path.name, sha256=previous, bytes=len(raw)))
            terminal = last is not None and last["stage"] == "result"
            if terminal:
                require(last["data"]["success"] is False
                        and last["data"]["status"] in ("failed", "timeout", "evidence_complete_unqualified"),
                        "bookkeeping cannot award formal PASS")
            declared, response_files = {}, []
            for row in rows:
                if row["stage"] in ("business", "workload32") and "observation_sha256" in row["data"]:
                    declared[row["stage"]] = row["data"]["observation_sha256"]
                if row["stage"] == "result":
                    for purpose, sha in row["data"].get("response_sha256", {}).items():
                        require(purpose not in declared or declared[purpose] == sha,
                                "response digest changed in journal")
                        declared[purpose] = sha
            require(set(declared) <= set(raw_files), "missing raw response file")
            if terminal:
                require(set(raw_files) == set(declared), "unbound terminal response file")
            for purpose, path in raw_files.items():
                raw = checked_file(directory, path.name).read_bytes()
                document = json.loads(raw)
                require(document.get("schema") == 1 and document.get("identity") == identity,
                        "raw response identity differs")
                received = timestamp(document["observation"]["received_ns"])
                require(rows[0]["monotonic_ns"] <= received
                        and (not terminal or received <= last["monotonic_ns"]), "raw response time differs")
                require(purpose not in declared or digest(raw) == declared[purpose], "raw response digest differs")
                for row in rows:
                    if row["stage"] == purpose:
                        saved = dict(response=row["data"]["response"],
                                     native_reference=row["data"]["native_reference"],
                                     received_ns=row["monotonic_ns"])
                    elif row["stage"] == "result" and row["data"].get(purpose + "_observation") is not None:
                        saved = {k: row["data"][purpose + "_observation"][k]
                                 for k in ("response", "native_reference", "received_ns")}
                    else:
                        continue
                    require(json.dumps(saved, sort_keys=True) == json.dumps(document["observation"], sort_keys=True),
                            "journal response payload differs from preserved raw response")
                response_files.append(dict(path=path.name, sha256=digest(raw), bytes=len(raw)))
            found[directory.name] = dict(attempt_id=directory.name, consumed=True,
                status=last["data"]["status"] if terminal else "incomplete",
                terminal=terminal, directory=str(directory), journal=rows, journal_files=receipts,
                response_files=response_files)
    return [found.get(row["attempt_id"], dict(attempt_id=row["attempt_id"],
            consumed=False, status="unstarted", terminal=False)) for row in attempts]
