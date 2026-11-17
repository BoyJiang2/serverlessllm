import json,pathlib,subprocess,time
root=pathlib.Path('runs/B2-L1-D-r1-20261117T085046Z')
container='sllm-v11-B2-L1-D-r1-20261117T085046Z-L1-D'
fmt='--format=csv,noheader,nounits'
with (root/'qualification-recovery-release.jsonl').open('x') as out:
    begin=None
    while True:
        ns=time.monotonic_ns()
        g=subprocess.check_output(['nvidia-smi','--query-gpu=memory.used',fmt],text=True,timeout=10)
        a=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid',fmt],text=True,timeout=10)
        running=json.loads(subprocess.check_output(['docker','inspect','--format={{json .State.Running}}',container],text=True,timeout=10))
        used=list(map(int,g.split()))
        assert len(used)==8 and not any(used) and not a.strip() and not running
        begin=ns if begin is None else begin
        out.write(json.dumps(dict(ns=ns, wall_ns=time.time_ns(), gpu_used_mib=used, compute_apps=0, container_running=running))+'\n')
        out.flush()
        if ns-begin>=60*10**9:
            break
        time.sleep(1)
