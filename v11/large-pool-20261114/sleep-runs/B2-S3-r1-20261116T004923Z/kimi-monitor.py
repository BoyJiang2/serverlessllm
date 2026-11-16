import json,pathlib,subprocess,time
run=pathlib.Path(__file__).resolve().parent
controller=pathlib.Path("/proc/2369606")
receipt=run/"L2/01-K-created.json"
deadline=time.monotonic()+9000
while time.monotonic()<deadline and (controller/"stat").exists():
    try:
        cid=json.loads(receipt.read_bytes())["id"]
        info=json.loads(subprocess.check_output(["docker","inspect",cid],stderr=subprocess.DEVNULL))[0]
        assert info["Id"]==cid and info["Name"]==f"/sllm-v11-{run.name}-L2-K"
        if info["State"]["Running"]:
            pid=info["State"]["Pid"]
            group=next(line[3:] for line in pathlib.Path(f"/proc/{pid}/cgroup").read_text().splitlines() if line.startswith("0::"))
            cgroup=pathlib.Path("/sys/fs/cgroup")/group.lstrip("/")
            assert (cgroup/"memory.max").read_text().strip()==str(1664*1024**3)
            break
    except (FileNotFoundError,json.JSONDecodeError,subprocess.CalledProcessError):
        pass
    time.sleep(2)
else:
    raise SystemExit("Kimi did not start while the S3 controller was available")
metadata=dict(run_id=run.name,container_id=cid,container_pid=pid,cgroup=str(cgroup),memory_limit_bytes=1664*1024**3,page_bytes=__import__("os").sysconf("SC_PAGE_SIZE"),sample_interval_seconds=5)
(run/"kimi-monitor-target.json").write_text(json.dumps(metadata,indent=2)+"\n")
def properties(path):
    return {key:int(value) for key,value in (line.split() for line in path.read_text().splitlines())}
sample_file=run/f"kimi-cgroup-samples.{time.monotonic_ns()}.jsonl"
print(sample_file,flush=True)
with sample_file.open("x",buffering=1) as stream:
    while time.monotonic()<deadline:
        start=time.monotonic_ns()
        try:
            current=int((cgroup/"memory.current").read_text())
            events=properties(cgroup/"memory.events")
            stat=properties(cgroup/"memory.stat")
        except FileNotFoundError:
            break
        row=dict(start_ns=start,end_ns=time.monotonic_ns(),wall_ns=time.time_ns(),current_bytes=current,events=events,stat={key:stat[key] for key in ("anon","file","workingset_refault_file")})
        stream.write(json.dumps(row)+"\n")
        time.sleep(5)
