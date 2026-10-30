import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import datetime,json,pathlib,subprocess,time
from resource_window_control13 import load_window
from budget_control13 import require_budget
from control_watchdog13 import supervise_child
root=pathlib.Path('/experiment/controls/sllm-glm51fp8-20261026T030041Z-5b688078')
mode,label=sys.argv[1:]
assert (mode=='ray' and label in ('ray13a','ray13b')) or (mode=='service' and label=='service13a')
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
deadline=datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
budget=require_budget(label,deadline-time.time())
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
def heartbeat():
    ready=json.loads((root/'monitor-v3-ready.json').read_text())
    manifest=json.loads((root/'run-manifest.json').read_text())
    if ready['run_id']!=manifest['run_id'] or ready['boot_id']!=manifest['runtime_identity']['boot_id'] or not ready['observers_ok'] or ready['foreign_gpu_pids'] or not 0<=time.monotonic_ns()-ready['last_sample_ns']<=5_000_000_000:
        raise RuntimeError('control stopped after monitor/observer heartbeat failure')
try:
    child=subprocess.Popen(['/experiment/.venv/bin/python','-u',str(root/script),label])
    record['child_pid']=child.pid
    record.update(supervise_child(child,record['begin_ns'],budget,heartbeat))
except BaseException as error:
    record.update(error=repr(error),end_ns=time.monotonic_ns())
finally:
    with (root/(label+'-supervisor-result.json')).open('x') as stream: json.dump(record,stream,indent=2)
if record['status']!='passed including cleanup': raise RuntimeError(record.get('error','control failed'))
