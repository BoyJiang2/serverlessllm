"""Freeze the already qualified idle CPU environment; no install/build/device use."""
from pathlib import Path
import datetime
import json
import shutil
import subprocess
import time

base = Path('/home/boyjiang/sllm-v4-pro/evidence/native-entry-cpu-20261107')
cid = 'ac3a81f1426c8697120ddcadca8f3688654d0aa7370acba9f86cee6b924f0d8c'
tag = 'sllm-v4-runtime:p4a-v4pro4-20261107'
def call(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()
before, = json.loads(call('docker', 'inspect', '--size', cid))
assert before['Id'] == cid and before['State']['Running']
assert before['Image'] == 'sha256:ec9a1a479ad110600f15a0959a7b4c5bc9ddaed8b34a2d020abb7aea8b5b1707'
assert before['HostConfig']['Runtime'] == 'runc'
assert not before['HostConfig'].get('Devices') and not before['HostConfig'].get('DeviceRequests')
top = call('docker', 'top', cid, '-eo', 'pid,args').splitlines()
assert len(top) == 2 and top[1].split()[1:] == ['/bin/sleep', 'infinity'], top
available = shutil.disk_usage('/data/brick0/docker').free
assert before['SizeRw'] < 20 * 1024**3
assert available - before['SizeRw'] > 128 * 1024**3
existing = subprocess.run(['docker', 'image', 'inspect', tag], capture_output=True, text=True)
assert existing.returncode != 0, 'Do not replace a prior image snapshot'
(base / 'snapshot-before.json').write_text(json.dumps(dict(container=before,
    processes=top, docker_root_free_bytes=available), indent=2) + '\n')
started = time.monotonic_ns()
with (base / 'snapshot.log').open('x') as log:
    result = subprocess.run(['docker', 'commit', '--pause=true', cid, tag],
                            stdout=log, stderr=subprocess.STDOUT, timeout=600)
assert result.returncode == 0
image, = json.loads(call('docker', 'image', 'inspect', tag))
assert image['Parent'] == before['Image']
after, = json.loads(call('docker', 'inspect', cid))
assert after['State']['StartedAt'] == before['State']['StartedAt'] and after['State']['Running']
assert image['RootFS']['Layers'][:-1] == json.loads(call('docker', 'image', 'inspect', before['Image']))[0]['RootFS']['Layers']
report = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    image_id=image['Id'], image_tag=tag, source_container_id=cid,
    elapsed_seconds=(time.monotonic_ns() - started) / 1e9,
    image_size_bytes=image['Size'], source_writable_bytes=before['SizeRw'],
    unchanged_base_layers=True, source_container_not_restarted=True,
    installed_environment_reused=True, gpu_or_model_run=False,
    scope='Docker snapshot of idle P4A filesystem only; no rebuild/install or new runtime qualification; bind-mounted data and weights excluded')
(base / 'snapshot-image.json').write_text(json.dumps(image, indent=2) + '\n')
(base / 'snapshot-result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report))
