"""Read-only resource monitor restricted to the experiment's exact cgroup."""
import datetime
import json
import os
import pathlib
import subprocess
import time
# CONTROL COPY (v11): the v1 overlay deadline (2026-10-25T05:50) has passed; this monitor
# reads the v2 additive window instead so it keeps sampling until the v2 deadline.
from resource_window_v3 import load_window
from compilation_guard import TOOLS, observed_interference

root = pathlib.Path('/experiment')
manifest = json.loads((root/'run-manifest.json').read_text())
control = pathlib.Path(os.environ['SLLM_CONTROL_GROUP'])
assert control.is_absolute() and control.name == manifest['run_id']+'.slice'
cgroup = pathlib.Path('/sys/fs/cgroup')/str(control).lstrip('/')
assert cgroup.is_dir()
deadline = datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
sampled_peak = 0
output_root = pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')
log_name = os.environ.get('SLLM_MONITOR_LOG_NAME','resource-monitor-v3.jsonl')
assert log_name in ('resource-monitor-v3.jsonl','resource-monitor-v4.jsonl','resource-monitor-v12.jsonl')
with (output_root/log_name).open('x') as output:
    while time.time() < deadline:
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
                row = {'pid':pid,'status':status,'stat':proc.joinpath('stat').read_text(),
                       'cmdline':proc.joinpath('cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')}
                try: row['smaps_rollup'] = proc.joinpath('smaps_rollup').read_text()
                except PermissionError: row['smaps_rollup_error'] = 'permission denied; PSS unavailable'
                processes.append(row)
            except (FileNotFoundError,ProcessLookupError):
                continue
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
               'processes':processes}
        output.write(json.dumps(row)+'\n')
        output.flush()
        ready_path = output_root/'monitor-v3-ready.json'
        temporary = ready_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'last_sample_ns':row['monotonic_ns'],
                                         'run_id':manifest['run_id'],
                                         'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()}))
        temporary.replace(ready_path)
        interference = observed_interference(processes)
        # Only names and PIDs are read outside the experiment; never record
        # other users' command arguments or act on their processes.
        for proc in pathlib.Path('/proc').iterdir():
            if not proc.name.isdigit():
                continue
            try:
                name = (proc/'comm').read_text().strip()
                if name in TOOLS:
                    interference.append({'pid':int(proc.name),'comm':name,
                                         'scope':'host compiler/download process observed'})
            except (FileNotFoundError,ProcessLookupError,PermissionError):
                pass
        for line in row['gpu_processes'].splitlines():
            pid = int(line.split(',',1)[0])
            try:
                if str(control)+'/' not in pathlib.Path(f'/proc/{pid}/cgroup').read_text():
                    interference.append({'pid':pid,'scope':'GPU process outside experiment cgroup'})
            except (FileNotFoundError,ProcessLookupError):
                pass
            except PermissionError:
                interference.append({'pid':pid,'scope':'GPU ownership could not be verified'})
        if interference:
            with (output_root/'interference-v3.jsonl').open('a') as events:
                events.write(json.dumps({'monotonic_ns':row['monotonic_ns'],
                                         'processes':interference})+'\n')
        time.sleep(1)
