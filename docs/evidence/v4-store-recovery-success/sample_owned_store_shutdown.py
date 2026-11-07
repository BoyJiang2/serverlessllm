"""Read-only root observations of the one active, identity-bound Store daemon."""
import json
from pathlib import Path
import time

root=Path('/home/boyjiang/sllm-v4-pro/evidence')
h=json.loads((root/'store-recovery-current-handle.json').read_bytes())
base=Path(h['base'])
assert base.name=='store-recovery-20261107T063304Z'
cid=json.loads((base/'READY.json').read_bytes())['identity']['container_id']
assert cid=='1568a7d19e4d4ba8808efb2630c374f8aa5bf7e17a81f66419463a3f4d3bd243'
start_file=base/'output/recovery/daemon-shutdown-started.json'
wait_deadline=min(time.monotonic_ns()+180_000_000_000,
                  json.loads((base/'guard/armed.json').read_bytes())['deadline_ns'])
while not start_file.exists():
    assert time.monotonic_ns()<wait_deadline, 'shutdown observation start window exhausted'
    time.sleep(1)
started=json.loads(start_file.read_bytes())
deadline=min(started['deadline_ns'],started['begin_ns']+90_000_000_000)
namespace_pid=started['pid']
found=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():continue
    try:
        status=(proc/'status').read_text()
        ns=next(line for line in status.splitlines() if line.startswith('NSpid:')).split()
        if int(ns[-1])!=namespace_pid or cid not in (proc/'cgroup').read_text():continue
        stat=(proc/'stat').read_text().rsplit(')',1)[1].split()
        found.append((proc,int(stat[19])))
    except (OSError,StopIteration,ValueError):continue
assert len(found)==1,found
proc,start_ticks=found[0]
with (base/'stop-thread-samples.jsonl').open('x') as output:
    for index in range(16):
        if time.monotonic_ns()>=deadline:break
        row=dict(index=index,observed_ns=time.monotonic_ns(),host_pid=int(proc.name),namespace_pid=namespace_pid,start_ticks=start_ticks)
        try:
            assert int((proc/'stat').read_text().rsplit(')',1)[1].split()[19])==start_ticks
            assert cid in (proc/'cgroup').read_text()
            status=(proc/'status').read_text()
            row['memory_status']=[line for line in status.splitlines() if line.startswith(('VmRSS:','VmLck:','RssAnon:','RssFile:','Threads:'))]
            row['wchan']=(proc/'wchan').read_text()
            row['stack']=(proc/'stack').read_text()
            row['syscall']=(proc/'syscall').read_text()
            row['phase']='live'
        except FileNotFoundError:
            row['phase']='process_gone'
        output.write(json.dumps(row)+'\n');output.flush()
        if row['phase']=='process_gone':break
        time.sleep(2)
print(json.dumps(dict(base=str(base),samples=index+1,host_pid=int(proc.name),read_only=True)))
