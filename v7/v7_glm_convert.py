"""Run one guarded target-version GLM Store conversion and release audit."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import traceback

from v7_admission import deadline_ns
from v7_observe import collect_sample
from v7_qualify_sleep import gpu_window


BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE/'v7'


def call(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def write(path, value):
    pending = path.with_name(path.name+'.pending')
    with pending.open('x') as stream:
        json.dump(value, stream, indent=2)
    os.link(pending, path)
    pending.unlink()


def main(run_id):
    assert re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', run_id)
    assert socket.gethostname() == 'qjhs3'
    with (V7/'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        protocol = json.loads((V7/'glm-conversion-protocol.json').read_bytes())
        admission = json.loads((V7/'resource-admission.json').read_bytes())
        assert protocol['stage'] == 'frozen'
        assert protocol['vllm_commit'] == '132765e3560659ff63ebd236203672e991b70e08'
        deadline = deadline_ns(admission, protocol, protocol['minimum_window_seconds'])
        for path, digest in protocol['runtime_source_hashes'].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest, path
        identity = protocol['identity']
        model, store = Path(protocol['model_path']), Path(protocol['store_path'])
        assert not store.exists()
        assert hashlib.sha256((model/'config.json').read_bytes()).hexdigest() == identity['config_sha256']
        gpu = call(['nvidia-smi', '--query-gpu=index,uuid,memory.used', '--format=csv,noheader,nounits'])
        apps = call(['nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits'])
        assert not apps
        rows = [line.split(',') for line in gpu.splitlines()]
        assert [row[1].strip() for row in rows] == protocol['gpu_uuids']
        # 2026-11-10 19:32 +08: every GPU keeps 4 MiB after the previous
        # owner's engine exits, with no compute apps. Recorded as a driver
        # baseline instead of requiring an exact zero that nothing frees.
        assert max(int(row[2]) for row in rows) <= protocol['prelaunch_driver_baseline_mib']
        available = int(next(
            line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
            if line.startswith('MemAvailable:')
        ))*1024
        assert available >= protocol['minimum_available_bytes']
        root = V7/'runs'/run_id
        root.mkdir(parents=True)
        output = root/'conversion'
        output.mkdir()
        write(root/'protocol.json', protocol)
        write(root/'resource-admission.json', admission)
        write(root/'initial-resource.json', dict(
            gpu=gpu, apps=apps, available_bytes=available,
        ))
        write(root/'identity.json', identity)
        profile = json.loads((V7/'candidates-v2/profiles.json').read_bytes())['glm']
        cid = guard = observer = process = None
        handles = []
        outcome = dict(run_id=run_id, scenario='glm_target_store_conversion',
                       qualified=False)
        try:
            log = (root/'observer.log').open('x')
            handles.append(log)
            observer = subprocess.Popen(
                ['python3', str(V7/'v7_observe.py'), str(root),
                 str(deadline+240*10**9)],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
            )
            until = time.monotonic()+10
            while not (root/'observer-started.json').exists():
                assert observer.poll() is None and time.monotonic() < until
                time.sleep(.1)
            args = [
                'docker', 'create', '--network', 'host', '--ipc', 'host',
                '--pid', 'host', '--gpus',
                '"device='+','.join(protocol['gpu_uuids'])+'"',
                '--cpus', '32', '--memory', '1600g', '--pids-limit', '4096',
                '--ulimit', 'memlock=-1:-1', '--stop-signal', 'SIGINT',
                '--restart', 'no', '--user', '2147:2147',
                '--label', 'sllm.v4.guard=store-conversion',
                '--name', 'sllm-'+run_id,
                '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777',
                '--entrypoint', profile['python'],
            ]
            env = dict(
                profile['env'], STORAGE_PATH='/data',
                CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
                NVIDIA_VISIBLE_DEVICES=','.join(protocol['gpu_uuids']),
                HOME='/output', USER='boyjiang', LOGNAME='boyjiang',
                VLLM_PLUGINS='sllm_glm20_store',
                VLLM_USE_DEEP_GEMM_E8M0='0',
                SLLM_MODEL_REVISION=identity['source_revision'],
                SLLM_EXPORT_BARRIER='/output/export-barrier',
                PYTHONDONTWRITEBYTECODE='1',
                LD_LIBRARY_PATH=(
                    '/usr/local/nvidia/lib:/usr/local/nvidia/lib64:'
                    '/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/'
                    'site-packages/sllm_store'
                ),
            )
            for key, value in env.items():
                args += ['-e', key+'='+value]
            mounts = [(source, target, True) for source, target in profile['mounts']] + [
                (str(V7/'v7_glm_convert_once.py'), '/control/script.py', True),
                (str(root/'identity.json'), '/control/identity.json', True),
                ('/data', '/data', False), ('/etc/passwd', '/etc/passwd', True),
                ('/etc/group', '/etc/group', True),
            ]
            for source, target, readonly in mounts:
                spec = 'type=bind,src='+source+',dst='+target
                args += ['--mount', spec+',readonly' if readonly else spec]
            args += ['--mount', 'type=bind,src='+str(output)+',dst=/output',
                     profile['image'], '/control/script.py',
                     '--model', str(model), '--store', str(store),
                     '--identity', '/control/identity.json',
                     '--output', '/output']
            cid = call(args)
            state = json.loads(call(['docker', 'inspect', cid]))[0]
            write(root/'container-created.json', dict(
                id=cid, inspect=state, command=args,
            ))
            assert state['Image'] == profile['image']
            assert state['HostConfig']['NanoCpus'] == 32*10**9
            assert state['HostConfig']['Memory'] == 1600*1024**3
            assert state['HostConfig']['DeviceRequests'][0]['DeviceIDs'] == protocol['gpu_uuids']
            log = (root/'guard.log').open('x')
            handles.append(log)
            # sequence_guard caps store-conversion leases at 1200 s; the
            # measured attempt needed 810 s to reach 85% of the save.
            guard_deadline = min(
                deadline, time.monotonic_ns()+1180*10**9,
            )
            guard = subprocess.Popen(
                ['python3', str(BASE/'sequence_guard.py'), cid, str(guard_deadline),
                 str(root/'container-guard'), '--purpose', 'store-conversion'],
                stdout=log, stderr=subprocess.STDOUT,
                env=dict(os.environ, PYTHONPATH=str(
                    BASE/'full-preparation/v4-source/sllm_store')),
                start_new_session=True,
            )
            until = time.monotonic()+15
            while not (root/'container-guard/armed.json').exists():
                assert guard.poll() is None and time.monotonic() < until
                time.sleep(.1)
            log = (root/'container.log').open('x')
            handles.append(log)
            assert not call([
                'nvidia-smi', '--query-compute-apps=pid,used_gpu_memory',
                '--format=csv,noheader,nounits',
            ])
            process = subprocess.Popen(
                ['docker', 'start', '--attach', cid],
                stdout=log, stderr=subprocess.STDOUT,
            )
            code = process.wait(timeout=max(
                1, (deadline-time.monotonic_ns())/1e9
            ))
            assert code == 0 and (output/'candidate.json').exists()
            assert guard.wait(timeout=20) == 0
            state = json.loads(call(['docker', 'inspect', cid]))[0]
            write(root/'container-final.json', state)
            assert state['State']['ExitCode'] == 0 and not state['State']['Running']
            complete = json.loads((store/'COMPLETE.json').read_bytes())
            assert len(complete['ranks']) == 8
            assert {row['rank'] for row in complete['ranks']} == set(range(8))
            outcome['conversion_passed'] = True
            outcome['complete'] = complete
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
                    final = json.loads(call(['docker', 'inspect', cid]))[0]
                    write(root/'container-cleanup.json', final)
                    (root/'container-final.log').write_text(call(['docker', 'logs', cid]))
                    call(['docker', 'rm', cid])
                except Exception as error:
                    cleanup_errors.append(repr(error))
            if guard is not None:
                try:
                    outcome['guard_exit'] = guard.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    cleanup_errors.append('Guard still running; retained')
            release, begin = [], time.monotonic_ns()
            while time.monotonic_ns()-begin < 75e9:
                release.append(collect_sample(root))
                time.sleep(1)
            write(root/'release.json', release)
            write(root/'observation-stop.json', dict(ns=time.monotonic_ns()))
            if observer is not None:
                try:
                    outcome['observer_exit'] = observer.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    observer.terminate()
                    observer.wait(timeout=10)
                    cleanup_errors.append('Observer stop timed out')
            clock = (
                release[0]['boot_id'], release[0]['time_namespace']
            )
            try:
                window = gpu_window(
                    release, begin, time.monotonic_ns(), clock,
                    protocol['gpu_uuids'], minimum_seconds=60,
                )
                outcome['release_seconds'] = window['seconds']
                outcome['release_missing'] = len(window['missing'])
                outcome['all_gpu_zero'] = all(
                    not row['sample']['apps']['stdout'].strip()
                    and not any(row['memory']) for row in window['valid']
                )
            except Exception as error:
                outcome.update(
                    release_seconds=None, all_gpu_zero=None,
                    release_error=repr(error),
                )
            outcome['cleanup_errors'] = cleanup_errors
            outcome['qualified'] = bool(
                outcome.get('conversion_passed')
                and outcome.get('observer_exit') == 0
                and outcome.get('all_gpu_zero') is True
                and isinstance(outcome.get('release_seconds'), (int, float))
                and outcome['release_seconds'] >= 60
                and not cleanup_errors
            )
            write(root/'result.json', outcome)
            for stream in handles:
                stream.close()
        return 0 if outcome['qualified'] else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
