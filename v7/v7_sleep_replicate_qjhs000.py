"""Replicate the qualified B1 Sleep L1 case on qjhs0 (second node, no Store).

The qjhs3 case was qualified on 2026-11-10 23:37 +08. This node has its own GPU
UUIDs, its own RAID, and the same pinnings: same image digest, same V4
checkpoint (config sha256 5fe4568d…), same frozen sleep input. Nothing on qjhs3
is modified: a separate protocol and admission file are written here, and the
run directory is new.
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
TEMPLATE = V7 / 'sleep-protocol-v4.json'
PROTOCOL = V7 / 'sleep-protocol-v4-qjhs000.json'
ADMISSION = V7 / 'resource-admission-qjhs000.json'
MODEL = 'v4'
RESOURCE = 'h20/qjhs000'
HOST = 'qjhs0'
BASELINE_MIB = 16
QUALIFIED_QJHS3_RUN = 'v7-sleep-v4-20261110T153750Z'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def main():
    assert socket.gethostname() == HOST, socket.gethostname()
    with (V7 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        template = json.loads(TEMPLATE.read_bytes())
        template_sha = sha(TEMPLATE)
        gpu = call('nvidia-smi', '--query-gpu=index,uuid,memory.used',
                   '--format=csv,noheader,nounits')
        apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                    '--format=csv,noheader,nounits')
        assert not apps, apps
        rows = [line.split(',') for line in gpu.splitlines()]
        assert len(rows) == 8 and max(int(row[2]) for row in rows) <= BASELINE_MIB, gpu
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store must not run for B1'
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        assert available >= 1400 * 1024 ** 3, available

        protocol = dict(template)
        protocol.update(
            host=HOST,
            resource_id=RESOURCE,
            stage='frozen',
            gpu_uuids=[row[1].strip() for row in rows],
            prelaunch_driver_baseline_mib=BASELINE_MIB,
            previous_protocol_sha256=template_sha,
            replication_of=QUALIFIED_QJHS3_RUN,
            retry_of=None,
            scope=('Replication of the qualified B1 Sleep L1 case on qjhs0: native full-model '
                   'load, Sleep level 1, wake, correct requests; no Store and no KV persistence'),
            node_note=('qjhs0 has no V7 Store payload; only the Sleep baseline is replicated. '
                       'Same image digest, same V4 checkpoint config sha256, same frozen input.'),
            prelaunch_baseline_note=(
                'Same <=16 MiB driver baseline rule as the qjhs3 run; release qualification '
                'still requires the measured physical drop, not an exact zero at admission.'),
        )
        # qjhs0 admissions are direct user authorization; no reservation applies.
        protocol['booking_id'] = None
        protocol['admission_mode'] = 'user_authorized'
        hashes = {path: sha(path) for path in protocol['runtime_source_hashes']}
        for name in ('v7_admission.py', 'v7_sleep_once.py', 'v7_sleep_worker.py',
                     'v7_qualify_sleep.py', 'v7_backend.py', 'v7_observe.py', 'v7_trace.py'):
            hashes[str(V7 / name)] = sha(V7 / name)
        hashes[str(BASE / 'sequence_guard.py')] = sha(BASE / 'sequence_guard.py')
        protocol['runtime_source_hashes'] = hashes
        protocol['input_sha256'] = sha(V7 / 'candidates-v2/sleep-l1' / (MODEL + '-input.json'))

        samples = []
        for index in range(13):
            gpu = call('nvidia-smi', '--query-gpu=index,uuid,memory.used',
                       '--format=csv,noheader,nounits')
            apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                        '--format=csv,noheader,nounits')
            assert not apps, apps
            memory = [int(row.split(',')[2]) for row in gpu.splitlines()]
            assert max(memory) <= BASELINE_MIB, gpu
            samples.append(dict(wall_ns=time.time_ns(), ns=time.monotonic_ns(), gpu=gpu))
            if index < 12:
                time.sleep(5)
        span = (samples[-1]['wall_ns'] - samples[0]['wall_ns']) / 1e9
        tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        idle = V7 / f'sleep-qjhs000-prelaunch-idle-{tag}.json'
        idle.write_text(json.dumps(samples, indent=2) + '\n')
        now_ms = int(time.time() * 1000)
        admission = dict(
            mode='user_authorized', id='user-' + tag, resource_id=RESOURCE,
            checked_at_wall_ns=samples[-1]['wall_ns'], start_ms=now_ms,
            end_ms=now_ms + 4 * 3600 * 1000 - 60 * 1000,
            user_instruction=('用qjhs000可行么？ / 现在继续做呢？ — direct GPU use authorized '
                              'by the user; no reservation held on h20/qjhs000'),
            booking_state='no active booking on h20/qjhs000 at admission',
            prelaunch_idle_evidence=str(idle), idle_span_seconds=round(span, 3),
            available_bytes=available,
        )
        ADMISSION.write_text(json.dumps(admission, indent=2) + '\n')
        protocol['direct_admission_id'] = admission['id']
        body = json.dumps(protocol, indent=2) + '\n'
        PROTOCOL.write_text(body)
        protocol_sha = hashlib.sha256(body.encode()).hexdigest()

        run_id = 'v7-sleep-v4-qjhs000-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
        log = V7 / (run_id + '-controller.log')
        env = dict(os.environ,
                   SLLM_V7_SLEEP_PROTOCOL=PROTOCOL.name,
                   SLLM_V7_ADMISSION=ADMISSION.name)
        with log.open('x') as stream:
            process = subprocess.Popen(['python3', str(V7 / 'v7_sleep_once.py'), MODEL, run_id],
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       env=env, start_new_session=True)
        (V7 / (run_id + '-launch.json')).write_text(json.dumps(dict(
            run_id=run_id, pid=process.pid, wall_ns=time.time_ns(), log=str(log),
            host=HOST, resource=RESOURCE, protocol=str(PROTOCOL),
            protocol_sha256=protocol_sha, admission_id=admission['id'],
            replication_of=QUALIFIED_QJHS3_RUN,
            idle_span_seconds=round(span, 3),
        ), indent=2) + '\n')
        print(json.dumps(dict(run_id=run_id, pid=process.pid, protocol_sha256=protocol_sha,
                              admission_id=admission['id'],
                              gpu_uuids=protocol['gpu_uuids'],
                              idle_span_seconds=round(span, 3)), indent=2))


if __name__ == '__main__':
    main()
