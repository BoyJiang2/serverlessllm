"""Actual CPU-only container guard probes. No weights, environment install or GPU devices."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import time

root = Path('/home/boyjiang/sllm-v4-pro/code')
base = root.parent / 'evidence/container-guard-cpu-20261107'
base.mkdir(exist_ok=False)
image = 'sha256:ec9a1a479ad110600f15a0959a7b4c5bc9ddaed8b34a2d020abb7aea8b5b1707'
environment = dict(os.environ, PYTHONPATH=str(root / 'sllm_store'))


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=10).strip()


def wait_file(path, seconds):
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, str(path)
        time.sleep(0.05)
    return json.loads(path.read_bytes())


def process(pid):
    fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, state=fields[0], ppid=int(fields[1]), start_ticks=int(fields[19]))


results = []
for case in ('normal_exit', 'deadline', 'controller_exit'):
    directory = base / case
    directory.mkdir()
    command = 'sleep 1 & wait' if case == 'normal_exit' else "trap '' TERM; sleep 300 & sleep 300 & wait"
    container_id = docker('create', '--runtime', 'runc', '--network', 'none',
        '--cpus', '1', '--memory', '128m', '--pids-limit', '32', '--cap-drop', 'ALL',
        '--security-opt', 'no-new-privileges', '--read-only', '--user', '2147:2147',
        '--label', 'sllm.v4.guard=native-reference', '--name', 'sllm-v4-guard-cpu-' + case + '-20261107',
        image, '/bin/bash', '-c', command)
    initial, = json.loads(docker('inspect', container_id))
    assert initial['Image'] == image
    assert not initial['HostConfig'].get('Devices') and not initial['HostConfig'].get('DeviceRequests')
    assert not initial.get('Mounts')
    (directory / 'container-before.json').write_text(json.dumps(initial, indent=2) + '\n')
    deadline_ns = time.monotonic_ns() + (10 if case == 'normal_exit' else 4) * 1_000_000_000
    args = [sys.executable, str(root / 'benchmarks/v4_container_guard.py'),
            container_id, str(deadline_ns), str(directory / 'guard')]
    with (directory / 'guard.log').open('x') as output:
        if case == 'controller_exit':
            # This process exits before the target container starts. The guard must survive it.
            launcher = 'import subprocess,sys; p=subprocess.Popen(sys.argv[1:],start_new_session=True,stdin=subprocess.DEVNULL); print(p.pid,flush=True)'
            with (directory / 'launcher.log').open('x') as launch_log:
                parent = subprocess.run([sys.executable, '-c', launcher, *args],
                    env=environment, stdout=launch_log, stderr=output, timeout=3)
            assert parent.returncode == 0
            guard_pid = int((directory / 'launcher.log').read_text().splitlines()[0])
            guard = None
        else:
            guard = subprocess.Popen(args, env=environment, stdin=subprocess.DEVNULL,
                                     stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            guard_pid = guard.pid
        armed = wait_file(directory / 'guard/armed.json', 3)
        assert armed['guard']['pid'] == guard_pid
        identity = process(guard_pid)
        assert identity['start_ticks'] == armed['guard']['process_start_ticks']
        if case == 'controller_exit':
            assert identity['ppid'] == 1
            os.kill(guard_pid, 1)  # Detached guard also survives SSH-style SIGHUP.
        docker('start', container_id)
        pids = [int(line) for line in docker('top', container_id, '-eo', 'pid').splitlines()[1:]]
        workers = [process(pid) for pid in pids]
        assert len(workers) >= 2
        result = wait_file(directory / 'guard/result.json', 12)
        if guard:
            exit_code = guard.wait(timeout=3)
            assert exit_code == (0 if case == 'normal_exit' else 1)
        assert result['container_stopped']
        assert not result['physical_cleanup_verified'] and not result['runtime_qualified']
        assert result['reason'] == ('container_exit' if case == 'normal_exit' else 'deadline')
        if case != 'normal_exit':
            assert deadline_ns <= result['kill_requested_ns'] < deadline_ns + 1_000_000_000
        final, = json.loads(docker('inspect', container_id))
        assert final['State']['Status'] == 'exited' and final['State']['Pid'] == 0
        for worker in workers:
            try:
                current = process(worker['pid'])
            except FileNotFoundError:
                continue
            assert current['start_ticks'] != worker['start_ticks'], (worker, current)
        report = dict(case=case, container_id=container_id, guard_identity=identity,
            observed_workers=workers, all_original_worker_pids_gone=True,
            result=result, final_state=final['State'], cpu_only=True,
            gpu_or_model_run=False, physical_cleanup_verified=False)
        (directory / 'probe-result.json').write_text(json.dumps(report, indent=2) + '\n')
        results.append(report)
    # Remove only the exact stopped CPU probe container after retaining inspection and PID proof.
    assert docker('rm', container_id) == container_id
    print(case + ': PASS', flush=True)

report = dict(scope='Actual labelled CPU-only Docker containers; no model, GPU, Store or physical release qualification',
              cases=results,
              source_sha256=hashlib.sha256((root / 'benchmarks/v4_container_guard.py').read_bytes()).hexdigest())
(base / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(dict(cases=len(results), actual_cpu_containers_passed=True)))
