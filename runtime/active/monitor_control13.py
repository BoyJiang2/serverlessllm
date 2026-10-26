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

trace_output=None
trace_sequence=0
trace_io_ns=None
TRACE_LIMIT=256*1024**2

def trace_event(event):
    global trace_io_ns
    begin=time.monotonic_ns()
    data=(json.dumps(event,separators=(',',':'))+'\n').encode()
    if trace_output.tell()+len(data)>TRACE_LIMIT:
        raise RuntimeError('finite monitor diagnostic trace exceeded256MiB')
    trace_output.write(data)
    trace_output.flush()
    trace_io_ns=time.monotonic_ns()-begin

def measured(operation,action,pid=None,start_ticks=None,child_cpu=False,target=None,expected_start_ticks=None):
    """Diagnostic only: record locations, never substitute values or change gates."""
    global trace_sequence
    if trace_output is None:return action()
    trace_sequence+=1
    identity=dict(sequence=trace_sequence,operation=operation,pid=pid,start_ticks=start_ticks,target=target,expected_start_ticks=expected_start_ticks)
    trace_event(dict(identity,event='begin',event_ns=time.monotonic_ns(),end_ns=None,
                     outcome='pending',previous_trace_io_ns=trace_io_ns))
    begin_write_ns=trace_io_ns
    if child_cpu:
        import resource
        usage=resource.getrusage(resource.RUSAGE_CHILDREN)
        child_begin=usage.ru_utime+usage.ru_stime
    begin=time.monotonic_ns();cpu=time.thread_time_ns()
    outcome='ok';error=None;action_failure=None
    try:
        return action()
    except BaseException as failure:
        action_failure=failure
        outcome='error';error=type(failure).__name__[:128]
        raise
    finally:
        try:
            end=time.monotonic_ns();cpu_end=time.thread_time_ns()
            child_ns=None
            if child_cpu:
                usage=resource.getrusage(resource.RUSAGE_CHILDREN)
                child_ns=int((usage.ru_utime+usage.ru_stime-child_begin)*1e9)
            trace_event(dict(identity,event='end',begin_ns=begin,end_ns=end,
                             duration_ns=end-begin,thread_cpu_ns=cpu_end-cpu,child_cpu_ns=child_ns,
                             begin_trace_io_ns=begin_write_ns,outcome=outcome,error=error))
        except BaseException:
            if action_failure is None:raise
# CONTROL COPY (v11): the v1 overlay deadline (2026-10-25T05:50) has passed; this monitor
# reads the v2 additive window instead so it keeps sampling until the v2 deadline.
from resource_window_control13 import load_window
from compilation_guard import TOOLS, observed_interference

from workspace import run_root
root = run_root()
manifest = json.loads((root/'run-manifest.json').read_text())
control = pathlib.Path(os.environ['SLLM_CONTROL_GROUP'])
assert control.is_absolute() and control.name == manifest['run_id']+'.slice'
cgroup = pathlib.Path('/sys/fs/cgroup')/str(control).lstrip('/')
assert cgroup.is_dir()
deadline = datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
sampled_peak = 0
output_root = root
trace_enabled=manifest.get('monitor_diagnostic_trace',False)
if type(trace_enabled) is not bool:raise RuntimeError('diagnostic trace flag must be boolean')
if trace_enabled:
    assert manifest['scope']=='monitor diagnostics only; no model or pool'
    trace_output=(output_root/'monitor-diagnostic-trace.jsonl').open('xb')
log_name = os.environ.get('SLLM_MONITOR_LOG_NAME','resource-monitor-v3.jsonl')
assert log_name in ('resource-monitor-v3.jsonl','resource-monitor-v4.jsonl','resource-monitor-v12.jsonl')
registration_path=output_root/'observer-registration13.json'
assert registration_path.stat().st_uid==0
registration=json.loads(registration_path.read_text())
assert registration['run_id']==manifest['run_id'] and registration['boot_id']==manifest['runtime_identity']['boot_id']
with (output_root/log_name).open('x') as output:
    while time.time() < deadline:
        sample_begin_ns=time.monotonic_ns()
        current = int(measured('cgroup.memory.current',lambda:(cgroup/'memory.current').read_text()))
        sampled_peak = max(sampled_peak,current)
        pids = set()
        paths=cgroup.rglob('cgroup.procs')
        if trace_output is not None:paths=measured('cgroup.procs.scan',lambda:list(paths))
        for path in paths:
            try: pids.update(map(int,measured('cgroup.procs.read',lambda:path.read_text(),target=str(path)).split()))
            except FileNotFoundError: pass
        processes = []
        for pid in sorted(pids):
            try:
                proc = pathlib.Path('/proc')/str(pid)
                status = measured('proc.status',lambda:proc.joinpath('status').read_text(),pid=pid)
                uid = next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:'))
                process_cgroup = measured('proc.cgroup',lambda:proc.joinpath('cgroup').read_text(),pid=pid)
                if str(control)+'/' not in process_cgroup:
                    continue  # Process exited and its PID was reused outside this run.
                if uid != '2147':
                    processes.append({'pid':pid,'uid':uid,'cgroup':process_cgroup,
                                      'identity_anomaly':'details excluded; not experiment user'})
                    continue
                stat_text=measured('proc.stat',lambda:proc.joinpath('stat').read_text(),pid=pid)
                birth=stat_text.rpartition(')')[2].split()[19]
                row = {'pid':pid,'status':status,'cgroup':process_cgroup,'stat':stat_text,
                       'cmdline':measured('proc.cmdline',lambda:proc.joinpath('cmdline').read_bytes(),pid=pid,start_ticks=birth).replace(b'\0',b' ').decode(errors='replace')}
                try: row['pid_namespace']=measured('proc.ns.pid',lambda:proc.joinpath('ns/pid').readlink().as_posix(),pid=pid,start_ticks=birth)
                except PermissionError: row['pid_namespace_error']='permission denied; helper cannot be whitelisted'
                row['pss_collection']='separate bounded after-delete collector; excluded from fast heartbeat'
                processes.append(row)
            except (FileNotFoundError,ProcessLookupError):
                continue
        observer_states=[]
        for registered in registration['observers']:
            try:
                proc=pathlib.Path('/proc')/str(registered['pid'])
                observed_birth=measured('observer.stat',lambda:proc.joinpath('stat').read_text(),pid=registered['pid'],expected_start_ticks=registered['start_ticks']).rpartition(')')[2].split()[19]
                same_start=observed_birth==registered['start_ticks']
                same_group=measured('observer.cgroup',lambda:proc.joinpath('cgroup').read_text(),pid=registered['pid'],start_ticks=observed_birth,expected_start_ticks=registered['start_ticks'])==registered['cgroup']
                observer_states.append(dict(unit=registered['unit'],pid=registered['pid'],ok=same_start and same_group))
            except (FileNotFoundError,ProcessLookupError,PermissionError):
                observer_states.append(dict(unit=registered['unit'],pid=registered['pid'],ok=False))
        kernel_peak = cgroup/'memory.peak'
        row = {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'monotonic_ns':time.monotonic_ns(),
               'cgroup_memory_current':current,'sampled_memory_peak':sampled_peak,
               'kernel_memory_peak':int(measured('cgroup.memory.peak',lambda:kernel_peak.read_text())) if measured('cgroup.memory.peak.exists',kernel_peak.exists) else None,
               'cgroup_cpu_stat':measured('cgroup.cpu.stat',lambda:(cgroup/'cpu.stat').read_text()),
               'cgroup_memory_stat':measured('cgroup.memory.stat',lambda:(cgroup/'memory.stat').read_text()),
               'cgroup_pids_current':int(measured('cgroup.pids.current',lambda:(cgroup/'pids.current').read_text())),
               'memory_events_local':measured('cgroup.memory.events.local',lambda:(cgroup/'memory.events.local').read_text()) if measured('cgroup.memory.events.local.exists',lambda:(cgroup/'memory.events.local').exists()) else None,
               'memory_events':measured('cgroup.memory.events',lambda:(cgroup/'memory.events').read_text()),'host_meminfo':measured('proc.meminfo',lambda:pathlib.Path('/proc/meminfo').read_text()),
               'numa_meminfo':{str(p):measured('numa.meminfo',lambda:p.read_text(),target=str(p)) for p in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*/meminfo')},
               'gpu':measured('gpu.query-gpu',lambda:subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.total,memory.used,utilization.gpu','--format=csv,noheader'],text=True),child_cpu=True),
               'gpu_processes':measured('gpu.query-compute-apps',lambda:subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],text=True),child_cpu=True),
               'processes':processes,'observers':observer_states,'sample_begin_ns':sample_begin_ns,'sample_end_ns':time.monotonic_ns(),
               'sampling_scope':'non-atomic field reads bounded by begin/end; UTC is not a simultaneous snapshot'}
        interference = measured('observed.interference',lambda:observed_interference(processes))
        foreign=[]
        for line in row['gpu_processes'].splitlines():
            pid=int(line.split(',',1)[0])
            try:
                if str(control)+'/' not in measured('gpu.foreign-cgroup',lambda:pathlib.Path(f'/proc/{pid}/cgroup').read_text(),pid=pid): foreign.append(pid)
            except (FileNotFoundError,ProcessLookupError): pass
            except PermissionError: foreign.append(pid)
        interference += [{'pid':pid,'scope':'GPU process outside experiment cgroup or ownership unavailable'} for pid in foreign]
        helper=None
        launch_path=output_root/'service-bootstrap-service13a/store-numa-launch.json'
        identity_path=output_root/'service13a-identity.json'
        if launch_path.exists() and identity_path.exists():
            module=measured('import.gpu_identity13',lambda:__import__('gpu_identity13'))
            helper=measured('helper.derive',lambda:module.store_identity(json.loads(launch_path.read_text()),json.loads(identity_path.read_text()),processes,manifest['runtime_identity']['boot_id']))
        row.update(registered_store_helper=helper,gpu_pids=sorted({int(line.split(',',1)[0]) for line in row['gpu_processes'].splitlines()}),foreign_gpu_pids=foreign,sample_end_ns=time.monotonic_ns())
        encoded=measured('json.encode',lambda:json.dumps(row)+'\n')
        if measured('log.budget',lambda:output.tell()+len(encoded.encode())>4*1024**3):
            raise RuntimeError('finite control monitor exceeded4GiB log budget')
        measured('log.write',lambda:output.write(encoded))
        measured('log.flush',lambda:output.flush())
        ready_path=output_root/'monitor-v3-ready.json'
        temporary=ready_path.with_suffix('.tmp')
        ready_encoded=measured('ready.encode',lambda:json.dumps(dict(last_sample_ns=row['monotonic_ns'],sample_begin_ns=row['sample_begin_ns'],run_id=manifest['run_id'],observers_ok=all(item['ok'] for item in observer_states),boot_id=manifest['runtime_identity']['boot_id'],registered_store_helper=helper,gpu=row['gpu'],gpu_processes=row['gpu_processes'],gpu_pids=row['gpu_pids'],foreign_gpu_pids=foreign)))
        measured('ready.write',lambda:temporary.write_text(ready_encoded))
        measured('ready.rename',lambda:temporary.replace(ready_path))
        if interference:
            with measured('interference.open',lambda:(output_root/'interference-v3.jsonl').open('a')) as events:
                measured('interference.append',lambda:events.write(json.dumps({'monotonic_ns':row['monotonic_ns'],'processes':interference})+'\n'))
                measured('interference.flush',events.flush)
        measured('sample.sleep',lambda:time.sleep(1))
if trace_output is not None:trace_output.close()
