"""Optional GLM-compatible host events with explicit Linux clock/process identity."""
from contextlib import contextmanager
import functools
import inspect
import json
import os
from pathlib import Path
import socket
import threading
import time

_LOCK = threading.Lock()
_IDENTITY = None
_SEQUENCE = 0


def _reset_process():
    global _LOCK, _IDENTITY, _SEQUENCE
    _LOCK, _IDENTITY, _SEQUENCE = threading.Lock(), None, 0


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process)


def process_identity():
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    return dict(container_hostname=socket.gethostname(), pid=os.getpid(),
        process_start_ticks=int(fields[19]), clock_ticks_per_second=os.sysconf("SC_CLK_TCK"),
        boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        time_namespace=os.readlink("/proc/self/ns/time"),
        pid_namespace=os.readlink("/proc/self/ns/pid"), clock_id="CLOCK_MONOTONIC")


def record_event(name, **values):
    global _IDENTITY, _SEQUENCE
    directory = os.environ.get("SLLM_EVENT_DIR")
    if not directory:
        return
    now = time.monotonic_ns()
    run_id = os.environ.get("SLLM_EVENT_RUN_ID")
    path = Path(directory)
    if not run_id or not path.is_absolute():
        raise ValueError("Tracing requires an absolute SLLM_EVENT_DIR and SLLM_EVENT_RUN_ID")
    if not isinstance(name, str) or not name or not name.replace("_", "").isalnum():
        raise ValueError("Invalid event name")
    with _LOCK:
        if _IDENTITY is None or _IDENTITY["pid"] != os.getpid():
            _IDENTITY = process_identity()
        reserved = set(_IDENTITY) | {"name", "monotonic_ns", "trace_schema", "run_id", "sequence"}
        if reserved.intersection(values):
            raise ValueError("Event fields cannot replace clock/process identity")
        row = dict(_IDENTITY, trace_schema=1, run_id=run_id, name=name,
                   monotonic_ns=now, sequence=_SEQUENCE, **values)
        if name == "backend_engine_create_begin":
            row["boottime_minus_monotonic_ns"] = (
                time.clock_gettime_ns(time.CLOCK_BOOTTIME) - time.monotonic_ns())
        data = (json.dumps(row, allow_nan=False) + "\n").encode()
        path.mkdir(parents=True, exist_ok=True)
        filename = f"process-{row['container_hostname']}-{row['pid']}-{row['process_start_ticks']}.jsonl"
        descriptor = os.open(path / filename, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            while data:
                count = os.write(descriptor, data)
                if count <= 0:
                    raise OSError("Trace append did not make progress")
                data = data[count:]
        finally:
            os.close(descriptor)
        _SEQUENCE += 1


def record_failure(name, error, **values):
    try:
        record_event(name, error_type=type(error).__name__, **values)
    except BaseException as trace_error:
        error.sllm_trace_error = type(trace_error).__name__


@contextmanager
def event_span(name, **values):
    record_event(name + "_begin", **values)
    try:
        yield
    except BaseException as error:
        record_failure(name + "_failed", error, **values)
        raise
    record_event(name + "_end", **values)


def traced(name, values_from=None):
    def decorate(function):
        def values(args, kwargs):
            if values_from is not None:
                return values_from(args, kwargs)
            rank = getattr(args[0], "rank", None) if args else None
            if name == "communication_groups":
                rank = kwargs.get("rank", args[1] if len(args) > 1 else None)
            return dict(rank=rank)
        if inspect.iscoroutinefunction(function):
            @functools.wraps(function)
            async def wrapped(*args, **kwargs):
                if not os.environ.get("SLLM_EVENT_DIR"):
                    return await function(*args, **kwargs)
                with event_span(name, **values(args, kwargs)):
                    return await function(*args, **kwargs)
        else:
            @functools.wraps(function)
            def wrapped(*args, **kwargs):
                if not os.environ.get("SLLM_EVENT_DIR"):
                    return function(*args, **kwargs)
                with event_span(name, **values(args, kwargs)):
                    return function(*args, **kwargs)
        wrapped._sllm_trace_name = name
        return wrapped
    return decorate


def read_event_window(directory, run_id, begin_ns, end_ns, *, boot_id, time_namespace):
    if (type(begin_ns) is not int or type(end_ns) is not int
            or not 0 <= begin_ns <= end_ns or not all(isinstance(v, str) and v
                                                     for v in (run_id, boot_id, time_namespace))):
        raise ValueError("Invalid event window/clock identity")
    rows, processes = [], {}
    for path in sorted(Path(directory).glob("process-*.jsonl")):
        if path.is_symlink() or not path.is_file():
            raise ValueError("Linked/non-file event input")
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if (type(row.get("trace_schema")) is not int or row["trace_schema"] != 1
                    or row.get("run_id") != run_id
                    or row.get("boot_id") != boot_id or row.get("time_namespace") != time_namespace
                    or row.get("clock_id") != "CLOCK_MONOTONIC"):
                raise ValueError("Event run/clock domain differs from the control")
            timestamp, pid, start = row.get("monotonic_ns"), row.get("pid"), row.get("process_start_ticks")
            if (type(timestamp) is not int or timestamp < 0 or type(pid) is not int or pid <= 0
                    or type(start) is not int or start <= 0
                    or not isinstance(row.get("pid_namespace"), str) or not row["pid_namespace"]
                    or not isinstance(row.get("container_hostname"), str) or not row["container_hostname"]
                    or not isinstance(row.get("name"), str) or not row["name"]
                    or type(row.get("clock_ticks_per_second")) is not int or row["clock_ticks_per_second"] <= 0):
                raise ValueError("Missing process/timestamp identity")
            if begin_ns <= timestamp <= end_ns:
                key = row["container_hostname"], pid
                identity = row["pid_namespace"], start
                if key in processes and processes[key] != identity:
                    raise ValueError("Legacy host/pid key is ambiguous in this window")
                processes[key] = identity
                rows.append(row)
    if not rows:
        raise ValueError("No events in the selected window")
    return sorted(rows, key=lambda row: row["monotonic_ns"])
