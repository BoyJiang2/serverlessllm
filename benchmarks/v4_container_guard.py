"""Host-side deadline for one pre-created V4 runtime container; never starts it."""
import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time

from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once


LABEL = "sllm.v4.guard"


def docker(*args, timeout=5):
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "docker command failed")
    return result.stdout


def inspect(container_id, timeout=5):
    row, = json.loads(docker("inspect", container_id, timeout=timeout))
    if row["Id"] != container_id:
        raise ValueError("container identity differs")
    return row


def watch(container_id, deadline_ns, directory, purpose="native-reference"):
    if purpose not in ("native-reference", "store-conversion", "store-recovery", "native-service", "formal-trial"):
        raise ValueError("unsupported guard purpose")
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise ValueError("full immutable container ID required")
    now = time.monotonic_ns()
    limit_seconds = 3480 if purpose == "formal-trial" else 1680 if purpose == "native-service" else 1200
    if type(deadline_ns) is not int or not now < deadline_ns <= now + limit_seconds * 1_000_000_000:
        raise ValueError(f"future deadline within {limit_seconds} seconds required")
    row = inspect(container_id)
    if (row["Config"].get("Labels", {}).get(LABEL) != purpose
            or row["State"]["Status"] != "created"
            or row["HostConfig"]["RestartPolicy"]["Name"] not in ("no", "")
            or row["HostConfig"]["AutoRemove"] or row["RestartCount"] != 0):
        raise ValueError("requires labelled, new, retained container with no restart")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    identity = dict(container_id=container_id, created=row["Created"], image=row["Image"])
    interrupted = []
    def interrupt(signum, frame):
        interrupted.append(signum)
    # Run detached from SSH with start_new_session=True; HUP must not remove the guard.
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, interrupt)
    signal.signal(signal.SIGINT, interrupt)
    result = dict(identity=identity, purpose=purpose, deadline_ns=deadline_ns, runtime_qualified=False,
                  physical_cleanup_verified=False, container_stopped=False)
    observation_timeouts = 0
    try:
        write_once(directory / "armed.json", dict(identity=identity, purpose=purpose, guard=process_identity(),
                   armed_ns=time.monotonic_ns(), deadline_ns=deadline_ns))
        while True:
            now = time.monotonic_ns()
            if interrupted or now >= deadline_ns:
                result["reason"] = "signal" if interrupted else "deadline"
                break
            try:
                row = inspect(container_id, timeout=min(1, max(0.001, (deadline_ns - now) / 1e9)))
            except subprocess.TimeoutExpired as error:
                observation_timeouts += 1
                write_once(directory / f"inspect-timeout-{observation_timeouts}.json",
                           dict(observed_ns=time.monotonic_ns(), error=repr(error), deadline_ns=deadline_ns))
                # A timed-out observation is not a terminal target; keep the original deadline.
                continue
            if row["Created"] != identity["created"] or row["Image"] != identity["image"]:
                raise ValueError("container creation identity differs")
            if row["RestartCount"] != 0:
                raise ValueError("container restarted")
            state = row["State"]
            if state["Status"] in ("exited", "dead"):
                result.update(reason="container_exit", exit_code=state["ExitCode"])
                break
            if state["Status"] not in ("created", "running"):
                raise ValueError("unexpected container state: " + state["Status"])
            time.sleep(min(0.2, max(0, (deadline_ns - time.monotonic_ns()) / 1e9)))
    except Exception as error:
        result.update(reason="guard_error", error=repr(error))
    # Full immutable ID remains the cleanup target even if inspection failed.
    if result.get("reason") != "container_exit":
        result["kill_requested_ns"] = time.monotonic_ns()
        try:
            result["kill_output"] = docker("kill", "--signal", "KILL", container_id)
        except Exception as error:
            result["kill_error"] = repr(error)
    try:
        row = inspect(container_id)
        result["final_state"] = row["State"]
        result["container_stopped"] = (row["Created"] == identity["created"]
            and row["Image"] == identity["image"] and row["RestartCount"] == 0
            and row["State"]["Status"] in ("created", "exited", "dead")
            and not row["State"]["Running"] and row["State"]["Pid"] == 0)
    except Exception as error:
        result["final_inspect_error"] = repr(error)
    result["completed_ns"] = time.monotonic_ns()
    write_once(directory / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("container_id")
    parser.add_argument("deadline_ns", type=int)
    parser.add_argument("directory")
    parser.add_argument("--purpose", choices=("native-reference", "store-conversion", "store-recovery", "native-service", "formal-trial"),
                        default="native-reference")
    args = parser.parse_args()
    result = watch(args.container_id, args.deadline_ns, args.directory, args.purpose)
    print(json.dumps(result))
    raise SystemExit(0 if result.get("reason") == "container_exit"
                     and result.get("exit_code") == 0 and result["container_stopped"]
                     and not result["final_state"].get("OOMKilled")
                     and not result["final_state"].get("Error") else 1)
