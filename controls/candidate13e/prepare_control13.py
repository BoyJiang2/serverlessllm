"""Prepare isolated, untimed G1 sources for review; no remote mutations."""
import datetime
import difflib
import hashlib
import json
import pathlib
import secrets
import shutil

base = pathlib.Path(__file__).resolve().parent.parent
work = base/'work'
package = base/'outputs/deepseek-r1-control13'
package.mkdir(exist_ok=False)
run = 'sllm-glm51fp8-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+secrets.token_hex(4)
prefix = '/experiment/controls/'+run
asset = '/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57'

def save(name, source):
    if name.endswith('.py'):
        compile(source, name, 'exec')
    (package/name).write_text(source, encoding='utf-8', newline='\n')

def derive(old, new, replacements):
    source = (work/old).read_text(encoding='utf-8')
    original = source
    for before, after in replacements:
        assert before in source, (old, before)
        source = source.replace(before, after)
    save(new, source)
    save(new+'.diff', ''.join(difflib.unified_diff(original.splitlines(True), source.splitlines(True), old, new)))

derive('launch_workload_v12.py', 'launch_control13.py', [
    ('"""Launch a named workload', '"""Launch an isolated control workload'),
    ("manifest = json.loads((root / 'run-manifest.json').read_text())", "manifest = json.loads((root / 'run-manifest.json').read_text())\nasset_root = pathlib.Path(manifest['parent_asset_root'])\nassert str(asset_root) == "+repr(asset)+"\nimport hashlib\nassert hashlib.sha256((asset_root/'run-manifest.json').read_bytes()).hexdigest() == manifest['parent_manifest_sha256']"),
    ("str(root) + ',dst=/experiment'", "str(asset_root) + ',dst=/experiment',\n        '--mount=type=bind,src=' + str(root) + ',dst="+prefix+"',\n        '--mount=type=bind,src=' + str(root/'run-manifest.json') + ',dst=/experiment/run-manifest.json,readonly',\n        '--mount=type=bind,src=' + str(root/'resource-window-extension-v3.json') + ',dst=/experiment/resource-window-extension-v3.json,readonly'"),
    ("str(root / 'patches/", "str(asset_root / 'patches/"),
])
derive('ray_only_control_v12.py', 'ray_control13.py', [
    ("root = pathlib.Path('/experiment')", "root = pathlib.Path("+repr(prefix)+")"),
])
derive('resource_monitor_v12.py', 'monitor_control13.py', [
    ("log_name = os.environ.get", "output_root = pathlib.Path("+repr(prefix)+")\nlog_name = os.environ.get"),
    ("with (root/log_name).open", "with (output_root/log_name).open"),
    ("ready_path = root/", "ready_path = output_root/"),
    ("with (root/'interference-v3.jsonl')", "with (output_root/'interference-v3.jsonl')"),
])
derive('service_bootstrap_v11.py', 'bootstrap_control13.py', [
    ("out = root/('service-bootstrap-'+label)", "out = pathlib.Path("+repr(prefix)+")/('service-bootstrap-'+label)"),
    ("RAY_TMPDIR=str(root/('ray-'+label))", "RAY_TMPDIR=str(pathlib.Path("+repr(prefix)+")/('ray-'+label))"),
    ("env['RAY_ADDRESS'] = address", "env['RAY_ADDRESS'] = address\nos.environ.update(RAY_TMPDIR=env['RAY_TMPDIR'],RAY_ADDRESS=address,RAY_USAGE_STATS_ENABLED='0')"),
    ('from resource_window_v2 import load_window', 'from resource_window_v3 import load_window'),
    ("signal.alarm(min(7200 if validate_model else 300,budget))", "signal.alarm(min(2400 if validate_model else 300,budget))"),
    ('    import ray\n    ray.init(address=address,log_to_driver=False)', "    wait_ready(lambda:'Ray runtime started.' in (out/'ray-worker.log').read_text(),60,worker)\n    import ray\n    ray.init(address=address,_node_ip_address=ip,log_to_driver=False)\n    own = next(node for node in ray.nodes() if node['Alive'] and node['NodeManagerAddress']==ip)\n    assert own['NodeID'] == str(ray.get_runtime_context().get_node_id()) and own['Resources']['GPU']==8"),
])
save('supervise_control13.py', '''import json,pathlib,subprocess,sys,time
root=pathlib.Path('''+repr(prefix)+''')
mode,label=sys.argv[1:]
assert mode in ('ray','service') and label.isalnum()
script='ray_control13.py' if mode=='ray' else 'bootstrap_control13.py'
budget=330 if mode=='ray' else 2500
record=dict(begin_ns=time.monotonic_ns(),status='failed',timeout_seconds=budget)
try:
    subprocess.run(['/experiment/.venv/bin/python','-u',str(root/script),label],check=True,timeout=budget)
    record['status']='passed including cleanup'
except BaseException as error:
    record['error']=repr(error)
    raise
finally:
    record['end_ns']=time.monotonic_ns()
    with (root/(label+'-supervisor-result.json')).open('x') as stream:
        json.dump(record,stream,indent=2)
''')
for name in ['resource_window_v3.py','cleanup_v6.sh','install_guard_v6.sh','install_observers_v12.sh',
             'store_numa_exec_v10.py','service_model_validation.py','weight_contract.py',
             'compilation_guard.py','dsh-readonly-review.patch.yml']:
    shutil.copyfile(work/name,package/name)

head=json.loads((work/'ray-head-rayonly12.json').read_text())
head['env']={k:v for k,v in head['env'].items() if not k.startswith('SLLM_') or k=='SLLM_MODEL_REVISION'}
for index,label in enumerate(['ray13a','ray13b','service13a']):
    h=json.loads(json.dumps(head))
    h['name']='head'+label
    h['argv']=[v.replace('26520',str(26530+index)).replace('/experiment/ray-hrayonly12',prefix+'/ray-head-'+label) for v in h['argv']]
    save(h['name']+'.json',json.dumps(h,indent=2))
    s=json.loads((work/'ray-only12-template.json').read_text())
    s.update(name=label, argv=['/experiment/.venv/bin/python','-u',prefix+'/supervise_control13.py','ray' if index<2 else 'service',label])
    s['env']['PYTHONPATH']=prefix+':'+s['env']['PYTHONPATH']
    s['env']['SLLM_PRIVATE_RAY_ADDRESS']='PENDING_OBSERVED_HEAD_IP:'+str(26530+index)
    for key in list(s['env']):
        if key.startswith('SLLM_') and key not in ['SLLM_MODEL_REVISION','SLLM_PRIVATE_RAY_ADDRESS']:
            del s['env'][key]
    if index==2:
        s.update(cpus=32,memory='1700g',phase_trace=True)
        s['env'].update(SLLM_EVENT_DIR=prefix+'/events-'+label,SLLM_VALIDATE_FULL_MODEL='1',
            SLLM_SERVICE_WEIGHT_FORMAT='serverless_llm', SLLM_VERIFY_WEIGHTS='1',
            VLLM_ALLOW_INSECURE_SERIALIZATION='1',SLLM_SERVICE_WEIGHT_AUDIT_DIR=prefix+'/weight-audits-'+label)
    save(label+'-template.json',json.dumps(s,indent=2))

save('CONTROL-IDENTITY.json',json.dumps(dict(run_id=run,parent_asset_root=asset,
    cleanup_deadline='2026-10-26T02:50:00+08:00',formal_attempts_consumed=0),indent=2))
print(package)
print(run)
