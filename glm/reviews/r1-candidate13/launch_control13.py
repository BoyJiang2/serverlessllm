"""Isolated, untimed G1 launch using reviewed inputs and booking-derived window v3."""
import datetime
import json
import pathlib
import subprocess
import sys
import time
import os
from resource_window_v3 import load_window

root = pathlib.Path(__file__).resolve().parent
manifest = json.loads((root / 'run-manifest.json').read_text())
asset_root = pathlib.Path(manifest['parent_asset_root'])
assert str(asset_root) == '/data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57'
import hashlib
assert hashlib.sha256((asset_root/'run-manifest.json').read_bytes()).hexdigest() == manifest['parent_manifest_sha256']
run = manifest['run_id']
from materialize_control13 import validate_spec
assert pathlib.Path(sys.argv[1]).name == sys.argv[1]
for row in json.loads((root/'control-input-manifest.json').read_text())['files']:
    path=(root/row['path']).resolve()
    assert path.is_relative_to(root)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256'], row['path']
spec = json.loads((root / sys.argv[1]).read_text())
validate_spec(root, spec)
assert root == pathlib.Path('/data/brick1/boyjiang') / run
assert os.getuid() == manifest['uid'] == 2147
assert spec['name'].replace('_', '').isalnum()
window = load_window(root)
deadline = datetime.datetime.fromisoformat(window['cleanup_deadline'])
assert datetime.datetime.now(datetime.timezone.utc).timestamp() < deadline.timestamp() - 120

def call(*args):
    return subprocess.check_output(args, text=True).strip()

assert call('systemctl', 'is-active', window['guard_timer']) == 'active'
expected_next = deadline.strftime('%Y-%m-%d %H:%M:%S')
observed_next = call('systemctl', 'show', window['guard_timer'], '-p', 'NextElapseUSecRealtime', '--value')
assert expected_next in observed_next, (expected_next, observed_next)
if spec.get('gpus'):
    assert not call('nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader').strip(), 'GPU occupied'
args = ['docker', 'create', '--network=' + spec.get('network', 'none'), '--memory=' + spec['memory'],
        '--cpus=' + str(spec['cpus']), '--shm-size=32g', '--ulimit=core=0', '--restart=no', '--cgroup-parent=' + run + '.slice',
        '--name=' + run + '-' + spec['name'], '--label=sllm.run_id=' + run,
        '--label=sllm.uid=2147', '--user=2147:2147',
        '--env=USER=boyjiang', '--env=LOGNAME=boyjiang', '--env=HOME=/experiment/home',
        '--mount=type=bind,src=' + str(asset_root) + ',dst=/experiment',
        '--mount=type=bind,src=' + str(root) + ',dst=/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1',
        '--mount=type=bind,src=' + str(root/'run-manifest.json') + ',dst=/experiment/run-manifest.json,readonly',
        '--mount=type=bind,src=' + str(root/'resource-window-extension-v3.json') + ',dst=/experiment/resource-window-extension-v3.json,readonly']
if spec.get('gpus'):
    args += ['--gpus=all']
else:
    args += ['--runtime=runc']
if spec.get('cuda_toolkit'):
    args += ['--mount=type=bind,src=/usr/local/cuda-13.0,dst=/host-cuda,readonly']
if spec.get('raw_export_hook') or spec.get('phase_trace'):
    if spec.get('raw_export_hook'):
        assert spec['env'].get('SLLM_EXPORT_RAW_STORE') == '/experiment/store/vllm/glm51-fp8'
    args += ['--mount=type=bind,src=' + str(asset_root / 'patches/base_loader.py')
             + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/model_loader/base_loader.py,readonly']
if spec.get('phase_trace'):
    assert spec['env'].get('SLLM_EVENT_DIR', '').startswith('/experiment/')
    args += ['--mount=type=bind,src=' + str(asset_root / 'patches/gpu_worker.py')
             + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu_worker.py,readonly']
if spec.get('host_monitor'):
    args += ['--pid=host', '--cgroupns=host']
for key, value in spec.get('env', {}).items():
    args += ['--env=' + key + '=' + str(value)]
args += ['--entrypoint=' + spec['argv'][0], manifest['image_id'], *spec['argv'][1:]]
dispatch_monotonic_ns = time.monotonic_ns()
dispatch_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
with (root / (spec['name'] + '-dispatch.json')).open('x') as output:
    json.dump({'run_id':run,'spec':spec,'uid':os.getuid(),
               'dispatch_monotonic_ns':dispatch_monotonic_ns,'dispatch_utc':dispatch_utc,
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
          'dispatch_monotonic_ns':dispatch_monotonic_ns,'dispatch_utc':dispatch_utc,
          'docker_create_complete_ns':create_complete_ns,'docker_start_complete_ns':start_complete_ns,
          'launch_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
(root / (spec['name'] + '-identity.json')).write_text(json.dumps(record, indent=2))
print(json.dumps(record, indent=2))
