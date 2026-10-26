import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Read-only resource monitor restricted to the experiment's exact cgroup."""
import datetime
import json
import os
import pathlib
import subprocess
import time
# CONTROL COPY (v11): the v1 overlay deadline (2026-10-25T05:50) has passed; this monitor
# reads the v2 additive window instead so it keeps sampling until the v2 deadline.
from resource_window_control13 import load_window
from compilation_guard import TOOLS, observed_interference

root = pathlib.Path('/experiment')
manifest = json.loads((root/'run-manifest.json').read_text())
control = pathlib.Path(os.environ['SLLM_CONTROL_GROUP'])
assert control.is_absolute() and control.name == manifest['run_id']+'.slice'
cgroup = pathlib.Path('/sys/fs/cgroup')/str(control).lstrip('/')
assert cgroup.is_dir()
deadline = datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
sampled_peak = 0
output_root = pathlib.Path('/experiment/controls/sllm-glm51fp8-20261026T030041Z-5b688078')
log_name = os.environ.get('SLLM_MONITOR_LOG_NAME','resource-monitor-v3.jsonl')
assert log_name in ('resource-monitor-v3.jsonl','resource-monitor-v4.jsonl','resource-monitor-v12.jsonl')
registration_path=output_root/'observer-registration13.json'
assert registration_path.stat().st_uid==0
registration=json.loads(registration_path.read_text())
assert registration['run_id']==manifest['run_id'] and registration['boot_id']==manifest['runtime_identity']['boot_id']
with (output_root/log_name).open('x') as output:
    while time.time() < deadline:
        sample_begin_ns=time.monotonic_ns()
        current = int((cgroup/'memory.current').read_text())
        sampled_peak = max(sampled_peak,current)
        pids = set()
        for path in cgroup.rglob('cgroup.procs'):
            try: pids.update(map(int,path.read_text().split()))
            except FileNotFoundError: pass
        processes = []
        for pid in sorted(pids):
            try:
                proc = pathlib.Path('/proc')/str(pid)
                status = proc.joinpath('status').read_text()
                uid = next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:'))
                process_cgroup = proc.joinpath('cgroup').read_text()
                if str(control)+'/' not in process_cgroup:
                    continue  # Process exited and its PID was reused outside this run.
                if uid != '2147':
                    processes.append({'pid':pid,'uid':uid,'cgroup':process_cgroup,
                                      'identity_anomaly':'details excluded; not experiment user'})
                    continue
                row = {'pid':pid,'status':status,'cgroup':process_cgroup,'stat':proc.joinpath('stat').read_text(),
                       'cmdline':proc.joinpath('cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')}
                try: row['pid_namespace']=proc.joinpath('ns/pid').readlink().as_posix()
                except PermissionError: row['pid_namespace_error']='permission denied; helper cannot be whitelisted'
                row['pss_collection']='separate bounded after-delete collector; excluded from fast heartbeat'
                processes.append(row)
            except (FileNotFoundError,ProcessLookupError):
                continue
        observer_states=[]
        for registered in registration['observers']:
            try:
                proc=pathlib.Path('/proc')/str(registered['pid'])
                same_start=proc.joinpath('stat').read_text().rpartition(')')[2].split()[19]==registered['start_ticks']
                same_group=proc.joinpath('cgroup').read_text()==registered['cgroup']
                observer_states.append(dict(unit=registered['unit'],pid=registered['pid'],ok=same_start and same_group))
            except (FileNotFoundError,ProcessLookupError,PermissionError):
                observer_states.append(dict(unit=registered['unit'],pid=registered['pid'],ok=False))
        kernel_peak = cgroup/'memory.peak'
        row = {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),
               'cgroup_memory_current':current,'sampled_memory_peak':sampled_peak,
               'kernel_memory_peak':int(kernel_peak.read_text()) if kernel_peak.exists() else None,
               'cgroup_cpu_stat':(cgroup/'cpu.stat').read_text(),
               'cgroup_memory_stat':(cgroup/'memory.stat').read_text(),
               'cgroup_pids_current':int((cgroup/'pids.current').read_text()),
               'memory_events_local':(cgroup/'memory.events.local').read_text() if (cgroup/'memory.events.local').exists() else None,
               'memory_events':(cgroup/'memory.events').read_text(),'host_meminfo':pathlib.Path('/proc/meminfo').read_text(),
               'numa_meminfo':{str(p):p.read_text() for p in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*/meminfo')},
               'gpu':subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.total,memory.used,utilization.gpu','--format=csv,noheader'],text=True),
               'gpu_processes':subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],text=True),
               'processes':processes,'observers':observer_states,'sample_begin_ns':sample_begin_ns,'sample_end_ns':time.monotonic_ns(),
               'sampling_scope':'non-atomic field reads bounded by begin/end; UTC is not a simultaneous snapshot'}
        interference = observed_interference(processes)
        foreign=[]
        for line in row['gpu_processes'].splitlines():
            pid=int(line.split(',',1)[0])
            try:
                if str(control)+'/' not in pathlib.Path(f'/proc/{pid}/cgroup').read_text(): foreign.append(pid)
            except (FileNotFoundError,ProcessLookupError): pass
            except PermissionError: foreign.append(pid)
        interference += [{'pid':pid,'scope':'GPU process outside experiment cgroup or ownership unavailable'} for pid in foreign]
        helper=None
        launch_path=output_root/'service-bootstrap-service13a/store-numa-launch.json'
        identity_path=output_root/'service13a-identity.json'
        if launch_path.exists() and identity_path.exists():
            from gpu_identity13 import store_identity
            helper=store_identity(json.loads(launch_path.read_text()),json.loads(identity_path.read_text()),processes,manifest['runtime_identity']['boot_id'])
        row.update(registered_store_helper=helper,gpu_pids=sorted({int(line.split(',',1)[0]) for line in row['gpu_processes'].splitlines()}),foreign_gpu_pids=foreign,sample_end_ns=time.monotonic_ns())
        encoded=json.dumps(row)+'\n'
        if output.tell()+len(encoded.encode())>4*1024**3:
            raise RuntimeError('finite control monitor exceeded4GiB log budget')
        output.write(encoded)
        output.flush()
        ready_path=output_root/'monitor-v3-ready.json'
        temporary=ready_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(last_sample_ns=row['monotonic_ns'],run_id=manifest['run_id'],observers_ok=all(item['ok'] for item in observer_states),boot_id=manifest['runtime_identity']['boot_id'],registered_store_helper=helper,gpu_pids=sorted({int(line.split(',',1)[0]) for line in row['gpu_processes'].splitlines()}),foreign_gpu_pids=foreign)))
        temporary.replace(ready_path)
        if interference:
            with (output_root/'interference-v3.jsonl').open('a') as events:
                events.write(json.dumps({'monotonic_ns':row['monotonic_ns'],'processes':interference})+'\n')
        time.sleep(1)
