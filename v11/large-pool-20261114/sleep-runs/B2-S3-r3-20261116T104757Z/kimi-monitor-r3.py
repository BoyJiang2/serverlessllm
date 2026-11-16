import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

run = Path(__file__).resolve().parent
if not re.fullmatch(r"B2-S3-r3-\d{8}T\d{6}Z", run.name):
    raise SystemExit(f"expected an existing S3 r3 run, received {run.name}")
campaign = run.parent.parent
controller = Path("/proc") / str(int(sys.argv[1]))
command = (controller / "cmdline").read_bytes().split(b"\0")
assert any(Path(os.fsdecode(argument)).name == "v11_run_b2.py" for argument in command)
assert command[command.index(b"--sequence") + 1] == b"S3"
assert command[command.index(b"--rep") + 1] == b"3"
controller_start = (controller / "stat").read_text().rsplit(")", 1)[1].split()[19]
booking = json.loads((run / "booking.json").read_bytes())
deadline_ns = booking["guard_deadline_ns"]

def write_json(name, record):
    with (run / name).open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")

def controller_alive():
    try:
        current = (controller / "stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return current[0] != "Z" and current[19] == controller_start

def running_container(index, model):
    receipt = run / "L2" / f"{index:02d}-{model}-created.json"
    while time.monotonic_ns() < deadline_ns and controller_alive():
        try:
            cid = json.loads(receipt.read_bytes())["id"]
            info = json.loads(subprocess.check_output(
                ["docker", "inspect", cid], stderr=subprocess.DEVNULL, timeout=5
            ))[0]
            assert info["Id"] == cid
            assert info["Name"] == f"/sllm-v11-{run.name}-L2-{model}"
            if info["State"]["Running"]:
                return info
        except (FileNotFoundError, json.JSONDecodeError, subprocess.CalledProcessError,
                subprocess.TimeoutExpired):
            pass
        time.sleep(2)
    raise RuntimeError(f"run ended before live container {model} could be observed")

instrumentation = json.loads((run / "instrumentation.json").read_bytes())
repro = run / "repro"
repro.mkdir(exist_ok=True)
for name, digest in instrumentation.items():
    source = campaign / name
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest, name
    target = repro / name
    if not target.exists():
        shutil.copyfile(source, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, name

reference = json.loads((campaign / "campaign.json").read_bytes())
v4 = running_container(0, "D")
mounts = []
for mount in v4["Mounts"]:
    source = Path(mount["Source"])
    if source.parent.name == "b2-v4-sink-router-overlay" or source == campaign / "layerwise.py":
        raw = source.read_bytes()
        mounts.append(dict(source=str(source), destination=mount["Destination"],
                           rw=mount["RW"], size=len(raw),
                           sha256=hashlib.sha256(raw).hexdigest()))
assert v4["Image"] == reference["expected_v4_image"]
assert v4["Config"]["Env"].count("SLLM_V11_V4_MARLIN_REBIND=1") == 1
candidate = [m for m in mounts if m["source"] == str(campaign / "layerwise.py")]
assert len(candidate) == 1 and not candidate[0]["rw"]
assert candidate[0]["destination"] == json.loads((campaign / "candidate-manifest.json").read_bytes())["target"]
assert candidate[0]["sha256"] == reference["candidate_overlay_sha256"]
assert sorted((m for m in mounts if m not in candidate), key=lambda m: m["source"]) == sorted(
    reference["expected_v4_overlay"], key=lambda m: m["source"])
write_json("v4-overlay-mounts.json", dict(
    run_id=run.name, container_id=v4["Id"], image_id=v4["Image"],
    observed_wall_ns=time.time_ns(), mounts=mounts))
print("V4: four live read-only overlay mounts verified", flush=True)

kimi = running_container(1, "K")
pid = kimi["State"]["Pid"]
group = next(line[3:] for line in Path(f"/proc/{pid}/cgroup").read_text().splitlines()
             if line.startswith("0::"))
cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
limit = 1664 * 1024**3
assert int((cgroup / "memory.max").read_text()) == limit
write_json("kimi-monitor-target.json", dict(
    run_id=run.name, container_id=kimi["Id"], container_pid=pid, cgroup=str(cgroup),
    memory_limit_bytes=limit, page_bytes=os.sysconf("SC_PAGE_SIZE"),
    sample_interval_seconds=5))

def properties(name):
    return {key: int(value) for key, value in
            (line.split() for line in (cgroup / name).read_text().splitlines())}

sample_file = run / f"kimi-cgroup-samples.{time.monotonic_ns()}.jsonl"
print(sample_file, flush=True)
with sample_file.open("x", buffering=1) as stream:
    while time.monotonic_ns() < deadline_ns + 480 * 10**9:
        start = time.monotonic_ns()
        try:
            current = int((cgroup / "memory.current").read_text())
            events = properties("memory.events")
            stat = properties("memory.stat")
        except FileNotFoundError:
            break
        row = dict(start_ns=start, end_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
                   current_bytes=current, events=events, stat={key: stat[key] for key in
                   ("anon", "file", "workingset_refault_file")})
        stream.write(json.dumps(row) + "\n")
        time.sleep(5)



