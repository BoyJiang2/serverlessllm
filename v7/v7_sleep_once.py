"""One bounded native Sleep case, using the existing exact-CID lifecycle guard."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
import traceback

from v7_admission import deadline_ns

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE/'v7'


def call(args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def write(path, value):
    pending = path.with_name(path.name+'.pending')
    with pending.open('x') as stream:
        json.dump(value, stream, indent=2)
    os.link(pending, path)
    pending.unlink()


def main(model, run_id):
    assert model in ('kimi', 'glm', 'v4') and re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
    protocol_name = os.environ.get('SLLM_V7_SLEEP_PROTOCOL', 'sleep-protocol-'+model+'.json')
    admission_name = os.environ.get('SLLM_V7_ADMISSION', 'resource-admission.json')
    with (V7/'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        protocol = json.loads((V7/protocol_name).read_bytes())
        admission = json.loads((V7/admission_name).read_bytes())
        assert socket.gethostname() == protocol.get('host', 'qjhs3')
        assert protocol['stage'] == 'frozen' and protocol['vllm_commit'] == '132765e3560659ff63ebd236203672e991b70e08'
        deadline = deadline_ns(admission, protocol, 3600)
        for path, digest in protocol['runtime_source_hashes'].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
        profile = json.loads((V7/'candidates-v2/sleep-l1/profiles.json').read_bytes())[model]
        assert profile['image'] == protocol['image_digest']
        source_input = V7/'candidates-v2/sleep-l1'/(model+'-input.json')
        assert hashlib.sha256(source_input.read_bytes()).hexdigest() == protocol['input_sha256']
        gpu = call(['nvidia-smi', '--query-gpu=index,uuid,memory.used', '--format=csv,noheader,nounits'])
        apps = call(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'])
        assert not apps
        rows = [line.split(',') for line in gpu.splitlines()]
        assert [r[1].strip() for r in rows] == protocol['gpu_uuids'] and all(int(r[2]) <= protocol['prelaunch_driver_baseline_mib'] for r in rows)
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:')))*1024
        assert available >= 1300*1024**3
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store must be shut down before this independent case'
        root = V7/'runs'/run_id
        root.mkdir(parents=True)
        output = root/'backend'
        output.mkdir()
        write(root/'protocol.json', protocol)
        write(root/'resource-admission.json', admission)
        write(root/'initial-resource.json', dict(gpu=gpu, apps=apps, available_bytes=available, store_port_closed=True))
        shutil.copy2(source_input, root/'input.json')
        cid = guard = observer = process = None
        outcome = dict(model=model, scenario='sleep_l1', qualified=False)
        handles = []
        try:
            log = (root/'observer.log').open('x'); handles.append(log)
            observer = subprocess.Popen(['python3', str(V7/'v7_observe.py'), str(root), str(deadline+240*10**9)], stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            until = time.monotonic()+10
            while not (root/'observer-started.json').exists():
                assert observer.poll() is None and time.monotonic() < until
                time.sleep(.1)
            args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
                    '--gpus', '"device='+','.join(protocol['gpu_uuids'])+'"', '--cpus', '32',
                    '--memory', '1536g', '--pids-limit', '4096', '--ulimit', 'memlock=-1:-1',
                    '--stop-signal', 'SIGINT', '--restart', 'no', '--user', '2147:2147',
                    '--label', 'sllm.v4.guard=shared-store-validation', '--name', 'sllm-'+run_id,
                    '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777', '--entrypoint', profile['python']]
            env = dict(profile['env'], CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
                       NVIDIA_VISIBLE_DEVICES=','.join(protocol['gpu_uuids']), HOME='/output', USER='boyjiang', LOGNAME='boyjiang',
                       SLLM_EVENT_DIR='/output/events', SLLM_EVENT_RUN_ID=run_id, SLLM_REQUEST_PREFIX=run_id,
                       PYTHONDONTWRITEBYTECODE='1', LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')
            for key, value in env.items():
                args += ['-e', key+'='+value]
            for source, target in profile['mounts']+[[str(V7/'v7_backend.py'), '/control/script.py'],
                    [str(root/'input.json'), '/control/input.json'], ['/data', '/data'],
                    ['/etc/passwd', '/etc/passwd'], ['/etc/group', '/etc/group']]:
                args += ['--mount', 'type=bind,src='+source+',dst='+target+',readonly']
            args += ['--mount', 'type=bind,src='+str(output)+',dst=/output', profile['image'], '/control/script.py']
            cid = call(args, timeout=120)
            state = json.loads(call(['docker', 'inspect', cid]))[0]
            write(root/'backend-created.json', dict(id=cid, inspect=state, command=args))
            assert state['Image'] == profile['image']
            assert state['HostConfig']['NanoCpus'] == 32*10**9 and state['HostConfig']['Memory'] == 1536*1024**3
            assert state['HostConfig']['DeviceRequests'][0]['DeviceIDs'] == protocol['gpu_uuids']
            log = (root/'guard.log').open('x'); handles.append(log)
            guard = subprocess.Popen(['python3', str(BASE/'sequence_guard.py'), cid, str(deadline), str(root/'backend-guard'), '--purpose', 'shared-store-validation'],
                                     stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONPATH=str(BASE/'full-preparation/v4-source/sllm_store')), start_new_session=True)
            until = time.monotonic()+15
            while not (root/'backend-guard/armed.json').exists():
                assert guard.poll() is None and time.monotonic() < until
                time.sleep(.1)
            log = (root/'backend.log').open('x'); handles.append(log)
            assert 0 <= time.time_ns()-admission['checked_at_wall_ns'] < 120e9
            assert not call(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'])
            process = subprocess.Popen(['docker', 'start', '--attach', cid], stdout=log, stderr=subprocess.STDOUT)
            until = time.monotonic()+30
            while not (output/'waiting.json').exists():
                assert process.poll() is None and time.monotonic() < until
                time.sleep(.1)
            write(root/'activation.json', dict(ns=time.monotonic_ns()))
            (output/'start-model').touch(exist_ok=False)
            code = process.wait(timeout=max(1, (deadline-time.monotonic_ns())/1e9+30))
            assert code == 0 and (output/'backend-passed.json').exists()
            guard_code = guard.wait(timeout=30)
            guard_result = json.loads((root/'backend-guard/result.json').read_bytes())
            # The guard can lose the container to a concurrent remove between
            # the exit and its final inspect (seen on 2026-11-10 23:17:24 +08).
            # Its recorded exit code remains the authoritative lifecycle fact.
            assert guard_result['reason'] == 'container_exit' and guard_result['exit_code'] == 0, guard_result
            try:
                state = json.loads(call(['docker', 'inspect', cid]))[0]
            except AssertionError:
                state = dict(
                    State=dict(ExitCode=guard_result['exit_code'], Running=False,
                               OOMKilled=False, Error=''),
                    Image=guard_result['identity']['image'],
                    Id=cid, removal_observed_by_guard=True,
                )
                write(root/'container-removed-before-inspect.json',
                      dict(cid=cid, guard=guard_result))
            write(root/'backend-final.json', state)
            write(root/'backend-guard-exit.json', dict(code=guard_code, result=guard_result))
            assert state['State']['ExitCode'] == 0 and not state['State']['Running']
            workers = json.loads((output/'sleep-before-receipt.json').read_bytes())['ranks']

            def live(identity):
                try:
                    ticks = int(Path('/proc', str(identity['pid']), 'stat').read_text().rsplit(')', 1)[1].split()[19])
                except FileNotFoundError:
                    return False
                return ticks == identity['process_start_ticks']

            # Children can outlive the container init by a few seconds; poll
            # instead of sampling once. The original worker must be gone.
            exit_until = time.monotonic() + 60
            while time.monotonic() < exit_until and any(live(row['identity']) for row in workers):
                time.sleep(1)
            alive = [row['rank'] for row in workers if live(row['identity'])]
            assert not alive, 'Original worker remains alive: ' + repr(alive)
            write(root/'workers-exited.json', dict(all_original_workers_gone=True, workers=workers,
                                                   wait_seconds=round(60 - max(0, exit_until - time.monotonic()), 1)))
            outcome['backend_passed'] = True
        except BaseException as error:
            outcome.update(error=repr(error), traceback=traceback.format_exc())
            write(root/'failure.json', outcome)
        finally:
            cleanup_errors = []
            if cid is not None:
                try:
                    state = json.loads(call(['docker', 'inspect', cid]))[0]
                    if state['State']['Running']:
                        call(['docker', 'stop', '--time', '120', cid], timeout=150)
                    write(root/'backend-cleanup.json', state)
                    (root/'backend-final.log').write_text(call(['docker', 'logs', cid]))
                    call(['docker', 'rm', cid])
                    write(output/'container-removal.json', dict(removed=True))
                except Exception as error:
                    if 'No such object' in repr(error) or 'No such container' in repr(error):
                        write(output/'container-removal.json',
                              dict(removed=False, already_gone=True, note=repr(error)))
                    else:
                        cleanup_errors.append(repr(error))
            if guard is not None:
                try: outcome['guard_exit'] = guard.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    cleanup_errors.append('Exact-CID guard still running; not interrupted')
            begin = time.monotonic_ns()
            # A failed primary observer must not suppress the final release record.
            from v7_observe import collect_sample
            with (root/'release-fallback.jsonl').open('x', buffering=1) as release_log:
                while time.monotonic_ns()-begin < 75e9:
                    release_log.write(json.dumps(collect_sample(root))+'\n')
                    time.sleep(1)
            write(root/'release-window.json', dict(begin_ns=begin, end_ns=time.monotonic_ns()))
            write(root/'observation-stop.json', dict(ns=time.monotonic_ns()))
            if observer is not None:
                try: outcome['observer_exit'] = observer.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    observer.terminate();observer.wait(timeout=10);cleanup_errors.append('Observer stop timed out')
            outcome['cleanup_errors'] = cleanup_errors
            if outcome.get('backend_passed') and outcome.get('observer_exit') == 0 and not cleanup_errors:
                result = subprocess.run(['python3', str(V7/'v7_qualify_sleep.py'), str(root)], capture_output=True, text=True, timeout=60)
                write(root/'qualification-command.json', dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr))
                outcome['qualified'] = result.returncode == 0
            write(root/'result.json', outcome)
            for stream in handles:
                stream.close()
        return 0 if outcome['qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
