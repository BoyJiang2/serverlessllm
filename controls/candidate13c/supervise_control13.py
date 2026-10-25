import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
import json,pathlib,subprocess,sys,time
root=pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')
mode,label=sys.argv[1:]
assert mode in ('ray','service') and label.isalnum()
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
budget=330 if mode=='ray' else 3420
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
from resource_window_control13 import load_window
import datetime
from budget_control13 import require_budget
deadline=datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
require_budget(label,deadline-time.time())
child=None
try:
    child=subprocess.Popen(['/experiment/.venv/bin/python','-u',str(root/script),label])
    record['child_pid']=child.pid
    while child.poll() is None:
        if (time.monotonic_ns()-record['begin_ns'])/1e9>budget: raise TimeoutError('control hard budget exhausted')
        ready=json.loads((root/'monitor-v3-ready.json').read_text())
        if ready['run_id']!=json.loads((root/'run-manifest.json').read_text())['run_id'] or not ready['observers_ok'] or not 0<=time.monotonic_ns()-ready['last_sample_ns']<=5_000_000_000:
            raise RuntimeError('control stopped after monitor/observer heartbeat failure')
        time.sleep(2)
    if child.returncode!=0: raise RuntimeError('control child failed: '+str(child.returncode))
    record['status']='passed including cleanup'
except BaseException as error:
    record['error']=repr(error)
    raise
finally:
    if child is not None and child.poll() is None:
        child.terminate()
        try: child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
    record['end_ns']=time.monotonic_ns()
    with (root/(label+'-supervisor-result.json')).open('x') as stream:
        json.dump(record,stream,indent=2)
