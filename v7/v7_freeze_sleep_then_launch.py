"""Freeze the independent B1 Sleep L1 protocol and launch it on qjhs3.

Runs only after the Store five-step chain has finished and the Store container
is gone: the Sleep case owns the GPUs and its Store port must be closed.
"""
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE / 'v7'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def main():
    assert socket.gethostname() == 'qjhs3'
    with (V7 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        protocol_path = V7 / 'sleep-protocol-v4.json'
        protocol = json.loads(protocol_path.read_bytes())
        previous = sha(protocol_path)
        assert protocol['stage'] == 'frozen'
        assert protocol['model'] == 'v4' and protocol['scenario'] == 'sleep_l1'
        assert not call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                        '--format=csv,noheader,nounits')
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store still listening'
        assert os.path.exists('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7/runs'), 'no runs dir'
        store_runs = sorted(
            path.name for path in (V7 / 'runs').glob('v7-store-*')
            if (path / 'result.json').exists()
        )
        assert store_runs, 'Store chain must have a recorded result first'
        store_result = json.loads((V7 / 'runs' / store_runs[-1] / 'result.json').read_bytes())
        assert store_result.get('fixed_chain_passed') is True, store_result.get('error')

        samples = []
        for index in range(13):
            gpu = call('nvidia-smi', '--query-gpu=index,memory.used',
                       '--format=csv,noheader,nounits')
            apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                        '--format=csv,noheader,nounits')
            assert not apps, apps
            memory = [int(row.split(',')[1]) for row in gpu.splitlines()]
            assert max(memory) <= 16, gpu
            samples.append(dict(wall_ns=time.time_ns(), ns=time.monotonic_ns(), gpu=gpu))
            if index < 12:
                time.sleep(5)
        span = (samples[-1]['wall_ns'] - samples[0]['wall_ns']) / 1e9
        tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        idle = V7 / f'sleep-prelaunch-idle-{tag}.json'
        idle.write_text(json.dumps(samples, indent=2) + '\n')
        now_ms = int(time.time() * 1000)
        previous_admission = json.loads((V7 / 'resource-admission.json').read_bytes())
        admission = dict(
            mode='user_authorized',
            id='user-' + tag,
            resource_id='h20/qjhs003',
            checked_at_wall_ns=samples[-1]['wall_ns'],
            start_ms=now_ms,
            end_ms=now_ms + 4 * 3600 * 1000 - 60 * 1000,
            user_instruction=previous_admission['user_instruction'],
            prelaunch_idle_evidence=str(idle),
            idle_span_seconds=round(span, 3),
            prior_admission_id=previous_admission['id'],
        )
        (V7 / 'resource-admission.json').write_text(json.dumps(admission, indent=2) + '\n')
        protocol['direct_admission_id'] = admission['id']
        protocol['admission_mode'] = 'user_authorized'
        protocol['prelaunch_driver_baseline_mib'] = 16
        protocol['prelaunch_baseline_note'] = (
            'Same 2026-11-10 19:32 +08 observation as the Store chain: 4 MiB on '
            'every GPU with no compute apps. Release qualification still requires '
            'the measured physical drop, not an exact zero at admission.'
        )
        protocol['previous_protocol_sha256'] = previous
        protocol['retry_of'] = 'none; first B1 launch'
        protocol['runtime_source_hashes'] = {
            path: sha(path) for path in protocol['runtime_source_hashes']
        }
        protocol['runtime_source_hashes'][str(V7 / 'v7_sleep_once.py')] = sha(V7 / 'v7_sleep_once.py')
        protocol['store_chain_context'] = dict(
            run=store_runs[-1],
            fixed_chain_passed=True,
            store_shutdown_observed=True,
            overlap='none by construction: Store is destroyed before B1',
        )
        body = json.dumps(protocol, indent=2) + '\n'
        protocol_path.write_text(body)
        protocol_sha = hashlib.sha256(body.encode()).hexdigest()

        run_id = 'v7-sleep-v4-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
        log = V7 / (run_id + '-controller.log')
        with log.open('x') as stream:
            process = subprocess.Popen(
                ['python3', str(V7 / 'v7_sleep_once.py'), 'v4', run_id],
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        (V7 / (run_id + '-launch.json')).write_text(json.dumps(dict(
            run_id=run_id, pid=process.pid, wall_ns=time.time_ns(),
            log=str(log), protocol_sha256=protocol_sha,
            admission_id=admission['id'], model='v4', scenario='sleep_l1',
        ), indent=2) + '\n')
        print(json.dumps(dict(run_id=run_id, pid=process.pid,
                              protocol_sha256=protocol_sha,
                              admission_id=admission['id'],
                              idle_span_seconds=round(span, 3)), indent=2))


if __name__ == '__main__':
    main()
