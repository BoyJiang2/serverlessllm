import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import datetime,json,pathlib,subprocess,time,signal
from resource_window_control13 import load_window
from budget_control13 import require_budget,POOL_DIAGNOSTIC_SCOPE
from control_watchdog13 import supervise_child
from gpu_identity13 import require_monitor_ready
from workspace import SOURCE,run_root
root=run_root()
mode,label=sys.argv[1:]
formal_purpose=json.loads((root/'run-manifest.json').read_text()).get('execution_purpose')=='formal-continuation'
assert (formal_purpose and mode in ('head','service') and label in ('service13a','formal13')) or (not formal_purpose and ((mode=='ray' and label in ('ray13a','ray13b')) or (mode=='service' and label=='service13a')))
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
deadline=datetime.datetime.fromisoformat(load_window(root)['cleanup_deadline']).timestamp()
batch=None
if formal_purpose:
    from budget_control13 import load_formal_batch,FORMAL_BATCH_MAX_SECONDS,NORMAL_BATCH_MAX_SECONDS,head_stop_requested
    batch=load_formal_batch(root,label)
    budget=NORMAL_BATCH_MAX_SECONDS if label=='service13a' else FORMAL_BATCH_MAX_SECONDS
else:
    budget=require_budget(label,deadline-time.time(),pool_diagnostic=json.loads((root/'run-manifest.json').read_text())['scope']==POOL_DIAGNOSTIC_SCOPE)
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
if formal_purpose:
    dispatch=json.loads((root/(('head' if mode=='head' else '')+label+'-dispatch.json')).read_text())
    record.update({key:dispatch[key] for key in ('run_id','code_commit','manifest_sha256','boot_id','dispatch_monotonic_ns')})
def heartbeat():
    ready=json.loads((root/'monitor-v3-ready.json').read_text())
    manifest=json.loads((root/'run-manifest.json').read_text())
    require_monitor_ready(ready,time.monotonic_ns(),manifest['run_id'],manifest['runtime_identity']['boot_id'])
def unexpected_stop(signum,frame):raise RuntimeError('unexpected supervisor signal '+str(signum))
if formal_purpose:signal.signal(signal.SIGTERM,unexpected_stop)
try:
    argv=['/experiment/.venv/bin/python','-u',str(SOURCE/script),label]
    if mode=='head':
        argv=json.loads((SOURCE/'headservice13a.json').read_text())['argv']
        if label=='formal13':argv=[v.replace('26532','26533').replace('ray-head-service13a','ray-head-formal13') for v in argv]
    if batch is not None:
        from budget_control13 import require_batch_start
        require_batch_start(batch)
    child=subprocess.Popen(argv)
    record['child_pid']=child.pid
    options={}
    if batch is not None:
        options=dict(absolute_end_ns=batch['head_end_ns'] if mode=='head' else batch['total_end_ns'],cleanup_seconds=10 if mode=='head' else 25)
        if mode=='head':options['stop_requested']=lambda:head_stop_requested(root,label)
    record.update(supervise_child(child,record['begin_ns'],budget,heartbeat,**options))
except BaseException as error:
    record.update(error=repr(error),end_ns=time.monotonic_ns())
finally:
    with (root/(('head' if mode=='head' else '')+label+'-supervisor-result.json')).open('x') as stream: json.dump(record,stream,indent=2)
if record['status']!='passed including cleanup': raise RuntimeError(record.get('error','control failed'))
