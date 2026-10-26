"""Isolated, untimed G1 launch using reviewed inputs and booking-derived window v3."""
import hashlib
import datetime
import json
import pathlib
import subprocess
import sys
import time
import os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')

from workspace import SOURCE,HOST,RUN_ALIAS,run_root,historical_path,container_mounts,verify_input_layout
root = run_root()
manifest = json.loads((root/'run-manifest.json').read_text())
assert manifest['workspace_root']==str(HOST)
parent_manifest=pathlib.Path(str(HOST))/'protocol/historical-review-manifest-v11.json'
assert hashlib.sha256(parent_manifest.read_bytes()).hexdigest()=='da681f8d83ee3416faeffad30f230365e9956c8c6763aa30487d4bb809be81b4'
for row in json.loads(parent_manifest.read_text())['files']:
    path=historical_path(row['path'])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'],row['path']

run = manifest['run_id']
assert pathlib.Path(sys.argv[1]).name == sys.argv[1]
assert len(sys.argv)==4 and hashlib.sha256((root/'control-input-manifest.json').read_bytes()).hexdigest()==sys.argv[2], 'review manifest SHA required'
for row in json.loads((root/'control-input-manifest.json').read_text())['files']:
    path=(SOURCE/row['path']).resolve()
    assert path.is_relative_to(SOURCE)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'], row['path']
from materialize_control13 import validate_spec,validate_scope,verify_timeout_probe
from resource_window_control13 import load_window
spec = json.loads((root / sys.argv[1]).read_text())
validate_spec(root, spec)
from preflight_control13 import check
preflight=check(root,monitor_required=spec['name']!='monitor13')
preflight['input_layout']=verify_input_layout()
assert root == pathlib.Path(str(HOST))/'validation/controls'/run
assert os.getuid() == manifest['uid'] == 2147
assert spec['name'].replace('_', '').isalnum()
window = load_window(root)
deadline = datetime.datetime.fromisoformat(window['cleanup_deadline'])
from budget_control13 import require_budget,POOL_DIAGNOSTIC_SCOPE
diagnostic=validate_scope(root,spec['name'])
pool_diagnostic=manifest['scope']==POOL_DIAGNOSTIC_SCOPE
require_budget(spec['name'],deadline.timestamp()-time.time(),diagnostic=diagnostic,pool_diagnostic=pool_diagnostic)

def call(*args):
    return subprocess.check_output(args, text=True, timeout=15).strip()

assert call('systemctl', 'is-active', window['guard_timer']) == 'active'
expected_next = deadline.strftime('%Y-%m-%d %H:%M:%S')
observed_next = call('systemctl', 'show', window['guard_timer'], '-p', 'NextElapseUSecRealtime', '--value')
assert expected_next in observed_next, (expected_next, observed_next)
if spec.get('gpus') or spec['name'].startswith('head'):
    assert not call('nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader').strip(), 'GPU occupied'
if (diagnostic or pool_diagnostic) and spec['name']=='headservice13a':
    monitor=json.loads((root/'monitor13-identity.json').read_text())
    verify_timeout_probe(root,json.loads(call('docker','inspect',monitor['container_id']))[0])
args = ['docker', 'create', '--network=' + spec.get('network', 'none'), '--memory=' + spec['memory'],
        '--cpus=' + str(spec['cpus']), '--shm-size=32g', '--ulimit=core=0', '--restart=no', '--cgroup-parent=' + run + '.slice',
        '--name=' + run + '-' + spec['name'], '--label=sllm.run_id=' + run,
        '--label=sllm.uid=2147', '--user=2147:2147',
        '--env=USER=boyjiang', '--env=LOGNAME=boyjiang']
for mount in container_mounts():
    path=pathlib.Path(mount['source'])
    assert path.exists() and path.resolve().is_relative_to(pathlib.Path(str(HOST))), 'invalid canonical mount'
    args += ['--mount=type=bind,src='+mount['source']+',dst='+mount['destination']+(',readonly' if mount['readonly'] else '')]

if spec.get('gpus'):
    args += ['--gpus=all']
else:
    args += ['--runtime=runc']
if spec.get('cuda_toolkit'):
    args += ['--mount=type=bind,src=/usr/local/cuda-13.0,dst=/host-cuda,readonly']
if spec.get('raw_export_hook') or spec.get('phase_trace'):
    if spec.get('raw_export_hook'):
        assert spec['env'].get('SLLM_EXPORT_RAW_STORE') == '/experiment/store/vllm/glm51-fp8'
    args += ['--mount=type=bind,src=' + str(HOST / 'archive/original-run/patches/base_loader.py')
             + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/model_loader/base_loader.py,readonly']
if spec.get('phase_trace'):
    event_path=pathlib.PurePosixPath(spec['env'].get('SLLM_EVENT_DIR',''))
    assert event_path.is_relative_to(RUN_ALIAS) and '..' not in event_path.parts
    args += ['--mount=type=bind,src=' + str(HOST / 'archive/original-run/patches/gpu_worker.py')
             + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu_worker.py,readonly']
if spec.get('host_monitor'):
    args += ['--pid=host', '--cgroupns=host']
for key, value in spec.get('env', {}).items():
    args += ['--env=' + key + '=' + str(value)]
args += ['--entrypoint=' + spec['argv'][0], manifest['image_id'], *spec['argv'][1:]]
dispatch_monotonic_ns = time.monotonic_ns()
deployment=json.loads((root/'deployment.json').read_text())
assert deployment['run_id']==run and deployment['input_manifest_sha256']==sys.argv[2] and deployment['code_commit']==sys.argv[3]
assert len(deployment['code_commit'])==40 and all(c in '0123456789abcdef' for c in deployment['code_commit'])
dispatch_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
with (root / (spec['name'] + '-dispatch.json')).open('x') as output:
    json.dump({'run_id':run,'spec':spec,'uid':os.getuid(),
               'dispatch_monotonic_ns':dispatch_monotonic_ns,'dispatch_utc':dispatch_utc,'preflight':preflight,'code_commit':deployment['code_commit'],
               'boot_id':pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()},output,indent=2)
cid = call(*args)
create_complete_ns = time.monotonic_ns()
call('docker', 'start', cid)
start_complete_ns = time.monotonic_ns()
pid = int(call('docker', 'inspect', cid, '--format={{.State.Pid}}'))
assert pid > 1
cgroup = pathlib.Path(f'/proc/{pid}/cgroup').read_text()
if run + '.slice/' not in cgroup:
    subprocess.run(['docker', 'kill', cid], check=True)
    raise RuntimeError('container escaped guarded experiment cgroup')
record = {'run_id': run, 'spec': spec, 'container_id': cid, 'pid': pid, 'cgroup': cgroup,
          'code_commit':deployment['code_commit'],'manifest_sha256':sys.argv[2],
          'dispatch_monotonic_ns':dispatch_monotonic_ns,'dispatch_utc':dispatch_utc,'preflight':preflight,
          'docker_create_complete_ns':create_complete_ns,'docker_start_complete_ns':start_complete_ns,
          'launch_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
(root / (spec['name'] + '-identity.json')).write_text(json.dumps(record, indent=2))
print(json.dumps(record, indent=2))
