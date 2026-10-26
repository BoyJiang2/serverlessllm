import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import datetime,json,pathlib,subprocess,time
from resource_window_control13 import load_window
from budget_control13 import require_budget,POOL_DIAGNOSTIC_SCOPE
from control_watchdog13 import supervise_child
from gpu_identity13 import require_monitor_ready
from workspace import SOURCE,run_root
root=run_root()
mode,label=sys.argv[1:]
assert (mode=='ray' and label in ('ray13a','ray13b')) or (mode=='service' and label=='service13a')
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
deadline=datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
budget=require_budget(label,deadline-time.time(),pool_diagnostic=json.loads((root/'run-manifest.json').read_text())['scope']==POOL_DIAGNOSTIC_SCOPE)
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
def heartbeat():
    ready=json.loads((root/'monitor-v3-ready.json').read_text())
    manifest=json.loads((root/'run-manifest.json').read_text())
    require_monitor_ready(ready,time.monotonic_ns(),manifest['run_id'],manifest['runtime_identity']['boot_id'])
try:
    child=subprocess.Popen(['/experiment/.venv/bin/python','-u',str(SOURCE/script),label])
    record['child_pid']=child.pid
    record.update(supervise_child(child,record['begin_ns'],budget,heartbeat))
except BaseException as error:
    record.update(error=repr(error),end_ns=time.monotonic_ns())
finally:
    with (root/(label+'-supervisor-result.json')).open('x') as stream: json.dump(record,stream,indent=2)
if record['status']!='passed including cleanup': raise RuntimeError(record.get('error','control failed'))
