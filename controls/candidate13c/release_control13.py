import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Read-only 60-second final release receipt; run after own workloads stop."""
import datetime,json,pathlib,subprocess,time,sys
root=pathlib.Path(__file__).resolve().parent
stage=sys.argv[1]
if stage not in ('ray13a','ray13b','allstopped'): raise RuntimeError('unknown release stage')
run=json.loads((root/'run-manifest.json').read_text())['run_id']
def call(*args): return subprocess.check_output(args,text=True,timeout=15).strip()
group=call('systemctl','show',run+'.slice','-p','ControlGroup','--value')
assert pathlib.PurePosixPath(group).name==run+'.slice'
cgroup=pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')
records=[]
for second in range(61):
    processes=[]
    for f in cgroup.rglob('cgroup.procs'):
        for pid in f.read_text().split():
            proc=pathlib.Path('/proc')/pid
            try:
                if group+'/' not in (proc/'cgroup').read_text(): continue
                status=(proc/'status').read_text()
                row=dict(pid=int(pid),uid=next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:')))
                if row['uid']=='2147':
                    row.update(status=status,stat=(proc/'stat').read_text())
                    try: row['smaps_rollup']=(proc/'smaps_rollup').read_text()
                    except PermissionError: row['PSS_unavailable']='permission denied'
                processes.append(row)
            except (FileNotFoundError,ProcessLookupError): pass
    records.append(dict(second=second,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic_ns=time.monotonic_ns(),
        gpu=call('nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader'),
        gpu_processes=call('nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader'),
        own_running=call('docker','ps','--filter','label=sllm.run_id='+run,'--format','{{.Names}}'),
    cgroup={name:(cgroup/name).read_text() for name in ('memory.current','memory.stat','memory.events','cpu.stat','pids.current')},
        host_meminfo=pathlib.Path('/proc/meminfo').read_text(),processes=processes))
    if second<60: time.sleep(1)
with (root/('release60-'+stage+'.json')).open('x') as stream: json.dump(records,stream,indent=2)
assert all(not row['gpu_processes'] for row in records), 'GPU process still present: no release pass'
if stage=='allstopped':
    assert all(not row['own_running'] and int(row['cgroup']['pids.current'])==0 for row in records), 'owned infrastructure still running'
    baseline=json.loads((root/'host-preparation.json').read_text())['gpu_baseline_mib']
    for row in records:
        used=[int(line.split(',')[-1].strip().split()[0]) for line in row['gpu'].splitlines()]
        assert len(used)==len(baseline)==8 and all(a<=b+16 for a,b in zip(used,baseline)), 'GPU memory retained'
else:
    assert all(set(row['own_running'].splitlines()) <= {run+'-monitor13'} for row in records), 'owned workload still running'
print('60s release passed:',stage,'; disk assets and reclaimable cache are retained and separately charged')
