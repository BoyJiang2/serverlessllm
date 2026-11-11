"""Fresh user-authorized GPU admission + frozen GLM v3 conversion protocol.

Retains the failed 2026-11-10 14:26 (+08) attempt: new Store directory, new
admission id, superseded protocol hash recorded rather than overwritten.
"""
import hashlib
import json
import os
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
STORE = '/data/brick3/boyjiang/glm5.1-fp8-store-v7-target-0201-20261110-v3'
PREVIOUS_ATTEMPT = 'v7-glm-rebuild-20261110T062606Z'
FILES = (
    'v7_glm_convert.py',
    'v7_glm_convert_once.py',
    'vllm_glm20_loader.py',
    'vllm_kimi20_loader.py',
    'v7_admission.py',
    'v7_observe.py',
    'v7_qualify_sleep.py',
    'v7_backend.py',
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def main():
    assert socket.gethostname() == 'qjhs3'
    apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                '--format=csv,noheader,nounits')
    assert not apps, apps
    samples = []
    for index in range(13):
        gpu = call('nvidia-smi', '--query-gpu=index,memory.used',
                   '--format=csv,noheader,nounits')
        memory = [int(line.split(',')[1]) for line in gpu.splitlines()]
        assert max(memory) <= 16, gpu
        samples.append(dict(wall_ns=time.time_ns(), ns=time.monotonic_ns(), gpu=gpu))
        if index < 12:
            time.sleep(5)
    span = (samples[-1]['wall_ns'] - samples[0]['wall_ns']) / 1e9
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    idle = V7 / f'glm-conversion-prelaunch-idle-v7-{tag}.json'
    idle.write_text(json.dumps(samples, indent=2) + '\n')

    previous = json.loads((V7 / 'resource-admission.json').read_bytes())
    now_ms = int(time.time() * 1000)
    admission = dict(
        mode='user_authorized',
        id='user-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'),
        resource_id='h20/qjhs003',
        checked_at_wall_ns=samples[-1]['wall_ns'],
        start_ms=now_ms,
        end_ms=now_ms + 4 * 3600 * 1000 - 60 * 1000,
        user_instruction=previous['user_instruction'],
        prelaunch_idle_evidence=str(idle),
        idle_span_seconds=round(span, 3),
        prior_admission_id=previous['id'],
    )
    (V7 / 'resource-admission.json').write_text(
        json.dumps(admission, indent=2) + '\n')

    old_path = V7 / 'glm-conversion-protocol.json'
    old_sha = sha(old_path)
    protocol = json.loads(old_path.read_bytes())
    protocol['runtime_source_hashes'] = {
        str(V7 / name): sha(V7 / name) for name in FILES
    }
    protocol['runtime_source_hashes'].update({
        str(V7 / 'candidates-v2' / name): sha(V7 / 'candidates-v2' / name)
        for name in ('profiles.json', 'entry_points.txt')
    })
    protocol['runtime_source_hashes'][
        '/home/boyjiang/sllm-v4-pro/cache-competition-20261109/sequence_guard.py'
    ] = sha('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/sequence_guard.py')
    protocol['store_path'] = STORE
    protocol['direct_admission_id'] = admission['id']
    protocol['stage'] = 'frozen'
    protocol['previous_protocol_sha256'] = old_sha
    protocol['retry_of'] = PREVIOUS_ATTEMPT
    protocol['supersedes'] = (
        'v7-glm-rebuild-20261110T062606Z was killed at 85% of the rank save '
        '(container exit 137, OOMKilled=false, host MemAvailable 1147 GiB). '
        'Its store directory is retained as failed evidence. This attempt uses '
        'a new store path, a 1600 GiB container limit, an eight-rank barrier '
        'before the read page cache is released, and fadvise DONTNEED of each '
        'fsynced tensor file. Identity and eight-rank schema are unchanged.'
    )
    protocol['memory_controls'] = dict(
        container_memory_bytes=1600 * 1024 ** 3,
        barrier_env='SLLM_EXPORT_BARRIER=/output/export-barrier',
        barrier_ranks=8,
        read_cache_release='posix_fadvise DONTNEED on checkpoint *.safetensors',
        write_cache_release='fsync then DONTNEED on tensor.data_* per rank',
    )
    body = json.dumps(protocol, indent=2) + '\n'
    old_path.write_text(body)
    print(json.dumps(dict(
        protocol_sha256=hashlib.sha256(body.encode()).hexdigest(),
        previous_protocol_sha256=old_sha,
        admission_id=admission['id'],
        idle_evidence=str(idle),
        idle_span_seconds=round(span, 3),
        store_path=STORE,
        runtime_source_hashes=len(protocol['runtime_source_hashes']),
    ), indent=2))


if __name__ == '__main__':
    main()
