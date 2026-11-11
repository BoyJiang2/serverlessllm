"""Native (Store-free) GLM 0.20.1 reference: same backend, same requests.

The unified chain restored the rebuilt GLM raw payload and matched the two
frozen short golds, but the 32-token greedy continuation of the 0.19.1.dev1
gold differed. This case loads the same checkpoint natively on the same image
and sends the identical token requests, so a difference can be attributed to
the engine version rather than to the Store restore path.
"""
import fcntl
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE / 'v7'
PROTOCOL = V7 / 'native-ref-protocol.json'
INPUT = V7 / 'glm-native-input.json'
IMAGE = 'sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
MODEL = Path('/data/brick1/boyjiang/glm5.1-fp8/checkpoint')
PORT = 8073


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(args, timeout=60):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def write(path, value):
    pending = path.with_name(path.name + '.pending')
    with pending.open('x') as stream:
        json.dump(value, stream, indent=2)
    os.link(pending, path)
    pending.unlink()


def gpu_state():
    gpu = call(['nvidia-smi', '--query-gpu=index,uuid,memory.used',
                '--format=csv,noheader,nounits'])
    apps = call(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                 '--format=csv,noheader,nounits'])
    return gpu, apps


def freeze():
    profile = json.loads((V7 / 'candidates-v2/profiles.json').read_bytes())['glm']
    gpu, apps = gpu_state()
    assert not apps, apps
    gpu_uuids = [row.split(',')[1].strip() for row in gpu.splitlines()]
    protocol = dict(
        stage='frozen',
        booking_id=None,
        admission_mode='user_authorized',
        scope='Native GLM-5.1-FP8 TP8 reference on vLLM 0.20.1; no Store, no KV persistence',
        model=str(MODEL),
        model_config_sha256=sha(MODEL / 'config.json'),
        vllm_commit='132765e3560659ff63ebd236203672e991b70e08',
        image_digest=IMAGE,
        gpu_uuids=gpu_uuids,
        input_sha256=sha(INPUT),
        minimum_available_bytes=1400 * 1024 ** 3,
        prelaunch_driver_baseline_mib=16,
        runtime_source_hashes={str(V7 / name): sha(V7 / name) for name in (
            'v7_backend.py', 'v7_observe.py', 'v7_qualify_sleep.py', 'v7_trace.py',
            'v7_sleep_worker.py', 'vllm_glm20_loader.py')},
        source='same requests as candidates-v2/glm-input.json; weight source is the checkpoint',
    )
    protocol['runtime_source_hashes'][str(BASE / 'sequence_guard.py')] = sha(BASE / 'sequence_guard.py')
    protocol['runtime_source_hashes'][str(V7 / 'candidates-v2/profiles.json')] = sha(V7 / 'candidates-v2/profiles.json')
    PROTOCOL.write_text(json.dumps(protocol, indent=2) + '\n')
    return protocol


def sample_idle(protocol):
    samples = []
    for index in range(13):
        gpu, apps = gpu_state()
        assert not apps, apps
        memory = [int(row.split(',')[2]) for row in gpu.splitlines()]
        assert max(memory) <= protocol['prelaunch_driver_baseline_mib'], gpu
        samples.append(dict(wall_ns=time.time_ns(), ns=time.monotonic_ns(), gpu=gpu))
        if index < 12:
            time.sleep(5)
    span = (samples[-1]['wall_ns'] - samples[0]['wall_ns']) / 1e9
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    idle = V7 / f'native-ref-prelaunch-idle-{tag}.json'
    idle.write_text(json.dumps(samples, indent=2) + '\n')
    now_ms = int(time.time() * 1000)
    previous = json.loads((V7 / 'resource-admission.json').read_bytes())
    admission = dict(
        mode='user_authorized', id='user-' + tag, resource_id='h20/qjhs003',
        checked_at_wall_ns=samples[-1]['wall_ns'], start_ms=now_ms,
        end_ms=now_ms + 4 * 3600 * 1000 - 60 * 1000,
        user_instruction=previous['user_instruction'],
        prelaunch_idle_evidence=str(idle), idle_span_seconds=round(span, 3),
        prior_admission_id=previous['id'],
    )
    (V7 / 'resource-admission.json').write_text(json.dumps(admission, indent=2) + '\n')
    return admission, span


def run(run_id, protocol, admission):
    from v7_admission import deadline_ns
    deadline = deadline_ns(admission, protocol, 1800)
    root = V7 / 'runs' / run_id
    root.mkdir(parents=True)
    output = root / 'backend'
    output.mkdir()
    write(root / 'protocol.json', protocol)
    write(root / 'resource-admission.json', admission)
    profile = json.loads((V7 / 'candidates-v2/profiles.json').read_bytes())['glm']
    assert profile['image'] == protocol['image_digest']
    gpu, apps = gpu_state()
    available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))) * 1024
    write(root / 'initial-resource.json', dict(gpu=gpu, apps=apps, available_bytes=available))
    cid = guard = observer = process = None
    handles = []
    outcome = dict(run_id=run_id, scenario='native_glm_reference', qualified=False)
    try:
        log = (root / 'observer.log').open('x')
        handles.append(log)
        observer = subprocess.Popen(['python3', str(V7 / 'v7_observe.py'), str(root),
                                     str(deadline + 240 * 10**9)],
                                    stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        until = time.monotonic() + 10
        while not (root / 'observer-started.json').exists():
            assert observer.poll() is None and time.monotonic() < until
            time.sleep(.1)
        args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
                '--gpus', '"device=' + ','.join(protocol['gpu_uuids']) + '"',
                '--cpus', '32', '--memory', '1536g', '--pids-limit', '4096',
                '--ulimit', 'memlock=-1:-1', '--stop-signal', 'SIGINT', '--restart', 'no',
                '--user', '2147:2147', '--label', 'sllm.v4.guard=native-service',
                '--name', 'sllm-' + run_id,
                '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777',
                '--entrypoint', profile['python']]
        env = dict(profile['env'], CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
                   NVIDIA_VISIBLE_DEVICES=','.join(protocol['gpu_uuids']),
                   HOME='/output', USER='boyjiang', LOGNAME='boyjiang',
                   SLLM_EVENT_DIR='/output/events', SLLM_EVENT_RUN_ID=run_id,
                   SLLM_REQUEST_PREFIX=run_id, PYTHONDONTWRITEBYTECODE='1',
                   VLLM_USE_DEEP_GEMM_E8M0='0',
                   LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:'
                                   '/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/'
                                   'site-packages/sllm_store')
        for key, value in env.items():
            args += ['-e', key + '=' + value]
        mounts = list(profile['mounts']) + [
            (str(V7 / 'v7_backend.py'), '/control/script.py'),
            (str(INPUT), '/control/input.json'),
            ('/data', '/data'), ('/etc/passwd', '/etc/passwd'), ('/etc/group', '/etc/group'),
        ]
        for source, target in mounts:
            args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + ',readonly']
        args += ['--mount', 'type=bind,src=' + str(output) + ',dst=/output',
                 profile['image'], '/control/script.py']
        cid = call(args, timeout=120)
        state = json.loads(call(['docker', 'inspect', cid]))[0]
        write(root / 'container-created.json', dict(id=cid, inspect=state, command=args))
        assert state['Image'] == profile['image']
        assert state['HostConfig']['Memory'] == 1536 * 1024**3
        log = (root / 'guard.log').open('x')
        handles.append(log)
        guard_deadline = min(deadline, time.monotonic_ns() + 1600 * 10**9)
        guard = subprocess.Popen(['python3', str(BASE / 'sequence_guard.py'), cid,
                                  str(guard_deadline), str(root / 'container-guard'),
                                  '--purpose', 'native-service'],
                                 stdout=log, stderr=subprocess.STDOUT,
                                 env=dict(os.environ, PYTHONPATH=str(
                                     BASE / 'full-preparation/v4-source/sllm_store')),
                                 start_new_session=True)
        until = time.monotonic() + 15
        while not (root / 'container-guard/armed.json').exists():
            assert guard.poll() is None and time.monotonic() < until
            time.sleep(.1)
        log = (root / 'backend.log').open('x')
        handles.append(log)
        assert not call(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                         '--format=csv,noheader,nounits'])
        process = subprocess.Popen(['docker', 'start', '--attach', cid],
                                   stdout=log, stderr=subprocess.STDOUT)
        until = time.monotonic() + 30
        while not (output / 'waiting.json').exists():
            assert process.poll() is None and time.monotonic() < until
            time.sleep(.1)
        write(root / 'activation.json', dict(ns=time.monotonic_ns()))
        (output / 'start-model').touch(exist_ok=False)
        code = process.wait(timeout=1700)
        assert code == 0 and (output / 'backend-passed.json').exists()
        assert guard.wait(timeout=20) == 0
        state = json.loads(call(['docker', 'inspect', cid]))[0]
        write(root / 'backend-final.json', state)
        assert state['State']['ExitCode'] == 0 and not state['State']['Running']
        outcome['backend_passed'] = True
    except BaseException as error:
        outcome.update(error=repr(error), traceback=traceback.format_exc())
        write(root / 'failure.json', outcome)
    finally:
        errors = []
        if cid is not None:
            try:
                state = json.loads(call(['docker', 'inspect', cid]))[0]
                if state['State']['Running']:
                    call(['docker', 'stop', '--time', '120', cid], timeout=150)
                write(root / 'container-cleanup.json',
                      json.loads(call(['docker', 'inspect', cid]))[0])
                (root / 'container-final.log').write_text(call(['docker', 'logs', cid]))
                call(['docker', 'rm', cid])
            except Exception as error:
                errors.append(repr(error))
        if guard is not None:
            try:
                outcome['guard_exit'] = guard.wait(timeout=30)
            except subprocess.TimeoutExpired:
                errors.append('guard still running')
        from v7_observe import collect_sample
        release, begin = [], time.monotonic_ns()
        while time.monotonic_ns() - begin < 75e9:
            release.append(collect_sample(root))
            time.sleep(1)
        write(root / 'release.json', release)
        write(root / 'observation-stop.json', dict(ns=time.monotonic_ns()))
        if observer is not None:
            try:
                outcome['observer_exit'] = observer.wait(timeout=30)
            except subprocess.TimeoutExpired:
                observer.terminate(); observer.wait(timeout=10)
                errors.append('observer stop timed out')
        from v7_qualify_sleep import gpu_window
        clock = (release[0]['boot_id'], release[0]['time_namespace'])
        try:
            window = gpu_window(release, begin, time.monotonic_ns(), clock,
                                protocol['gpu_uuids'], minimum_seconds=60)
            outcome['release_seconds'] = window['seconds']
            outcome['all_gpu_zero'] = all(
                not row['sample']['apps']['stdout'].strip()
                and not any(row['memory']) for row in window['valid'])
        except Exception as error:
            outcome.update(release_seconds=None, all_gpu_zero=None,
                           release_error=repr(error))
        outcome['cleanup_errors'] = errors
        outcome['qualified'] = bool(outcome.get('backend_passed')
                                    and outcome.get('observer_exit') == 0
                                    and outcome.get('all_gpu_zero') is True
                                    and isinstance(outcome.get('release_seconds'), (int, float))
                                    and outcome['release_seconds'] >= 60
                                    and not errors)
        write(root / 'result.json', outcome)
        for stream in handles:
            stream.close()
    return 0 if outcome['qualified'] else 1


def main():
    assert socket.gethostname() == 'qjhs3'
    with (V7 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if PROTOCOL.exists():
            protocol = json.loads(PROTOCOL.read_bytes())
            assert protocol['stage'] == 'frozen'
        else:
            protocol = freeze()
        assert hashlib.sha256(INPUT.read_bytes()).hexdigest() == protocol['input_sha256']
        for path, digest in protocol['runtime_source_hashes'].items():
            assert sha(path) == digest, path
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        assert available >= protocol['minimum_available_bytes'], available
        admission, span = sample_idle(protocol)
        protocol['direct_admission_id'] = admission['id']
        body = json.dumps(protocol, indent=2) + '\n'
        PROTOCOL.write_text(body)
        run_id = 'v7-native-glm-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
        (V7 / (run_id + '-launch.json')).write_text(json.dumps(dict(
            run_id=run_id, wall_ns=time.time_ns(),
            protocol_sha256=hashlib.sha256(body.encode()).hexdigest(),
            admission_id=admission['id'], idle_span_seconds=round(span, 3),
            input=str(INPUT), model=str(MODEL),
        ), indent=2) + '\n')
        print(json.dumps(dict(run_id=run_id, admission=admission['id'])), flush=True)
        return run(run_id, protocol, admission)


if __name__ == '__main__':
    raise SystemExit(main())
