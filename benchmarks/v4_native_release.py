"""Host-only post-exit observations for one native container, never launches work."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time

from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_evidence import release_coverage, require, timestamp


def call(*args):
    return subprocess.check_output(args, text=True, timeout=2).strip()


def snapshot(job):
    begin = time.monotonic_ns()
    identity = job["identity"]
    container, = json.loads(call("docker", "inspect", identity["container_id"]))
    require(container["Id"] == identity["container_id"] and container["Created"] == identity["created"]
            and container["Image"] == identity["image"], "container identity changed")
    selected = job["baseline_gpu_mib"]
    gpu_raw = call("nvidia-smi", "--id=" + ",".join(sorted(selected)),
                   "--query-gpu=uuid,memory.used", "--format=csv,noheader,nounits")
    app_raw = call("nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory",
                   "--format=csv,noheader,nounits")
    cgroup = Path(job["cgroup"])
    group = dict(exists=cgroup.exists(), files={})
    if group["exists"]:
        for path in cgroup.rglob("cgroup.procs"):
            group["files"][str(path.relative_to(cgroup))] = path.read_text()
        for name in ("memory.current", "memory.events", "memory.stat", "pids.current"):
            group["files"][name] = (cgroup / name).read_text()
    live = []
    for worker in job["worker_processes"]:
        try:
            raw = Path("/proc", str(worker["pid"]), "stat").read_text()
        except FileNotFoundError:
            continue
        if int(raw.rsplit(")", 1)[1].split()[19]) == worker["start_ticks"]:
            live.append(dict(worker, stat=raw))
    return dict(sample_begin_ns=begin, sample_end_ns=time.monotonic_ns(),
        container_state=container["State"], restart_count=container["RestartCount"],
        gpu_raw=gpu_raw, compute_apps_raw=app_raw, cgroup=group, original_workers_live=live)


def check_snapshot(sample, baseline):
    state = sample["container_state"]
    require(state["Status"] in ("exited", "dead") and not state["Running"] and state["Pid"] == 0
            and sample["restart_count"] == 0, "container still active or restarted")
    gpu = {}
    for line in sample["gpu_raw"].splitlines():
        uuid, memory = [value.strip() for value in line.split(",")]
        require(uuid not in gpu, "duplicate GPU sample")
        gpu[uuid] = int(memory)
    require(set(gpu) == set(baseline), "selected GPU coverage differs")
    require(all(0 <= gpu[uuid] <= baseline[uuid] + 16 for uuid in gpu), "GPU memory above baseline +16 MiB")
    for line in sample["compute_apps_raw"].splitlines():
        pid, uuid, memory = [value.strip() for value in line.split(",")]
        require(uuid not in baseline, "selected GPU still has a compute process")
    group = sample["cgroup"]
    if group["exists"]:
        require("cgroup.procs" in group["files"], "missing root cgroup process inventory")
        require(all(not value.strip() for name, value in group["files"].items()
                    if name.endswith("cgroup.procs")), "container cgroup still has processes")
        require(int(group["files"]["pids.current"]) == 0, "container cgroup pids retained")
    require(not sample["original_workers_live"], "original worker process remains")


def collect(job, output, sample=snapshot):
    require(re.fullmatch(r"[0-9a-f]{64}", job["identity"]["container_id"]), "full container ID required")
    require(len(job["baseline_gpu_mib"]) == 8
            and all(uuid.startswith("GPU-") and type(memory) is int and memory >= 0
                    for uuid, memory in job["baseline_gpu_mib"].items()), "requires frozen eight-GPU baseline")
    require(job["worker_processes"], "pre-exit worker inventory required")
    cgroup = Path(job["cgroup"])
    require(cgroup.is_absolute() and ".." not in cgroup.parts
            and cgroup.is_relative_to("/sys/fs/cgroup")
            and any(job["identity"]["container_id"] in part for part in cgroup.parts), "unbound cgroup")
    clock = process_identity()
    require(job["boot_id"] == clock["boot_id"] and job["time_namespace"] == clock["time_namespace"],
            "release clock identity differs")
    require(timestamp(job["preparation_begin_ns"]) <= timestamp(job["delete_ack_ns"])
            <= time.monotonic_ns() < timestamp(job["deadline_ns"])
            <= job["preparation_begin_ns"] + 1200_000_000_000, "release outside original deadline")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    write_once(output / "job.json", job)
    rows = []
    try:
        with (output / "samples.jsonl").open("x") as stream:
            for second in range(61):
                observation = sample(job)
                row = dict(second=second, monotonic_ns=time.monotonic_ns(),
                           delete_ack_ns=job["delete_ack_ns"], host_gpu_snapshot=observation)
                stream.write(json.dumps(row) + "\n")
                stream.flush()
                rows.append(row)
                require(row["monotonic_ns"] <= job["deadline_ns"], "release exceeded original deadline")
                check_snapshot(observation, job["baseline_gpu_mib"])
                if second < 60:
                    time.sleep(1)
        coverage = release_coverage(rows, job["delete_ack_ns"])
        write_once(output / "result.json", dict(native_release_observation_passed=True,
            coverage=coverage, physical_cleanup_verified=False, runtime_qualified=False,
            scope="Selected GPUs and one native container only; no SLLM Store/IPC or service lifetime qualification"))
    except BaseException as error:
        write_once(output / "failure.json", dict(error=repr(error), samples=len(rows),
                   native_release_observation_passed=False, physical_cleanup_verified=False))
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    collect(json.loads(args.job.read_bytes()), args.output)
