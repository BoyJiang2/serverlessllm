"""V11 B2 controller: native Sleep L1 and L2 in one group.

Both levels run the same frozen sequence in the same run directory (`L1/`, `L2/`).
Workers load natively (no SLLM Store), stay resident, and are driven by command
files; a model that reappears is woken in place, so its PID must not change.
Level 2 additionally reloads weights from the native checkpoint and records the
reload bytes/time. Nothing is faked: if a level cannot be run, it is recorded as
not passed with the concrete reason.
"""
import argparse
import csv
import fcntl
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import traceback

B = pathlib.Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = B / 'v7'
V11 = B / 'v11'
sys.path.insert(1, str(V11))
from v11_run import (  # noqa: E402
    MODEL_INPUT, MODEL_PATHS, PATH_SIZES, OBSERVATION_SECONDS, RELEASE_SECONDS,
    call, gpu_state, profiles, row, write, Sampler,
)

NATIVE_PATHS = {'kimi': '/mnt/raid/models/Kimi-K2.6',
                'glm': '/experiment/checkpoint',
                'v4': '/mnt/raid/models/DeepSeek-V4-Pro'}
# Weight bytes per model (identical to the frozen Store payload sizes); the L1
# sleep backup is one CPU copy of these bytes inside the worker's cgroup.
WEIGHT_BYTES = {'K': 617000763392, 'G': 762527481856, 'D': 877729513472}
RAM_MARGIN_BYTES = 200 * 2 ** 30


def native_input(run_dir, key):
    name = MODEL_INPUT[key]
    document = json.loads((V7 / 'candidates-v2' / (name + '-input.json')).read_bytes())
    config = document['config']
    config['load_format'] = 'auto'
    config['pretrained_model_name_or_path'] = NATIVE_PATHS[name]
    config['enable_prefix_caching'] = False
    config['enable_sleep_mode'] = True
    config['worker_extension_cls'] = 'sllm_store.v7_sleep_worker.V7SleepWorkerExtension'
    config.pop('model_loader_extra_config', None)
    path = run_dir / 'b2-inputs' / (name + '-input.json')
    path.write_text(json.dumps(document, indent=2) + '\n')
    return path


def worker_container(run_dir, level_dir, key, gpus, input_path):
    profile = profiles[MODEL_INPUT[key]]
    name = MODEL_INPUT[key]
    cid_name = f'sllm-v11-{run_dir.name}-{level_dir.name}-{key}'
    args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
            '--gpus', '"device=' + ','.join(gpus) + '"', '--cpus', '32', '--memory', '1400g',
            '--pids-limit', '4096', '--ulimit', 'memlock=-1:-1', '--stop-signal', 'SIGINT',
            '--restart', 'no', '--user', '2147:2147',
            '--label', 'sllm.v4.guard=shared-store-validation', '--name', cid_name,
            '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777', '--entrypoint', profile['python']]
    env = dict(profile['env'], CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
               NVIDIA_VISIBLE_DEVICES=','.join(gpus), HOME='/output', USER='boyjiang',
               LOGNAME='boyjiang', PYTHONDONTWRITEBYTECODE='1',
               SLLM_EVENT_DIR='/output/events', SLLM_EVENT_RUN_ID=run_dir.name,
               SLLM_REQUEST_PREFIX=level_dir.name + '-' + key,
               LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/host-cuda/lib64:'
                               '/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')
    for key_name, value in env.items():
        args += ['-e', key_name + '=' + value]
    mounts = list(profile['mounts']) + [
        (str(V11 / 'v11_sleep_backend.py'), '/control/script.py'),
        (str(V11 / 'v11_backend.py'), '/control/v11_lib.py'),
        (str(V7 / 'v7_backend.py'), '/control/v7_lib.py'),
        (str(input_path), '/control/input.json'),
        ('/data', '/data'), ('/etc/passwd', '/etc/passwd'), ('/etc/group', '/etc/group'),
    ]
    for source, target in mounts:
        args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + ',readonly']
    args += ['--mount', 'type=bind,src=' + str(level_dir / f'worker-{key}') + ',dst=/output']
    args += [profile['image'], '/control/script.py']
    cid = call(*args, timeout=120)
    return cid, args


def send(cid, output_dir, index, command):
    path = output_dir / f'cmd-{index:04d}.json'
    pending = path.with_name(path.name + '.pending')
    pending.write_text(json.dumps(command, indent=2) + '\n')
    os.link(pending, path)
    pending.unlink()
    ack = output_dir / f'ack-{index:04d}.json'
    deadline = time.monotonic() + 3600
    while not ack.exists():
        state = row(cid)
        assert state['State']['Running'] or ack.exists(), 'worker exited before ack'
        assert time.monotonic() < deadline, 'ack timeout'
        time.sleep(.2)
    return json.loads(ack.read_bytes())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequence', default=None)
    parser.add_argument('--models', default=None, help='ad-hoc model list for a smoke test')
    parser.add_argument('--levels', default='1,2')
    parser.add_argument('--reload-mode', default='checkpoint', choices=('checkpoint', 'kernel'))
    parser.add_argument('--rep', type=int, default=1)
    args = parser.parse_args()
    levels = [int(item) for item in args.levels.split(',') if item.strip()]
    assert socket.gethostname() == 'qjhs3'
    with (V11 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sequences = json.loads((V11 / 'switch-sequences.json').read_bytes())
        protocol_doc = json.loads((V11 / 'protocol-hash.json').read_bytes())
        if args.models:
            models = list(args.models)
            sequence_id = 'B2-smoke'
        else:
            assert args.sequence, 'either --sequence or --models is required'
            match = next(item for item in sequences['sequences'] if item['id'] == args.sequence)
            models = match['models']
            sequence_id = match['id']
        gpu, apps = gpu_state()
        uuids = [item.split(',')[1].strip() for item in gpu.splitlines()]
        assert len(uuids) == 8 and not apps and max(int(item.split(',')[2])
                                                    for item in gpu.splitlines()) <= 16
        available = int(next(line.split()[1] for line in
                             pathlib.Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        assert available >= 1700 * 1024 ** 3, available
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store must not run for B2'

        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        run_dir = V11 / 'runs' / f'B2-{sequence_id}-r{args.rep}-{stamp}'
        (run_dir / 'b2-inputs').mkdir(parents=True)
        write(run_dir / 'admission.json', dict(
            group='B2', levels=levels, sequence=sequence_id, rep=args.rep,
            protocol_hash=protocol_doc['protocol_hash'], gpu=gpu.splitlines(),
            apps=apps.splitlines(), available_bytes=available, gpus=uuids,
            checked_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
            instruction='V11 B2: native Sleep, no SLLM Store, L1 and L2 in one group'))
        inputs = {key: native_input(run_dir, key) for key in MODEL_INPUT}
        holder = {'cid': None, 'phase': 'startup'}
        sampler = Sampler(run_dir / 'resource-samples.jsonl', holder)
        sampler.start()

        results = []
        failures = {}
        try:
            for level in levels:
                level_dir = run_dir / f'L{level}'
                level_dir.mkdir()
                workers = {}
                counters = {key: 0 for key in MODEL_INPUT}
                cycles = {key: [] for key in MODEL_INPUT}
                records = []
                for index, key in enumerate(models):
                    phase = f'L{level}-{index:02d}-{key}'
                    holder['phase'] = phase
                    if key not in workers and level == 1:
                        available = int(next(
                            line.split()[1] for line in
                            pathlib.Path('/proc/meminfo').read_text().splitlines()
                            if line.startswith('MemAvailable:'))) * 1024
                        required = WEIGHT_BYTES[key] + RAM_MARGIN_BYTES
                        if available < required:
                            records.append(dict(
                                level=level, step=index, model=key,
                                stage=dict(kind='ram_infeasible'), sleep_seconds=None,
                                t4_ns=None, worker_pid=None, weights_cpu_backup_bytes=None,
                                weights_bytes=WEIGHT_BYTES[key], kv_bytes=None, checks={},
                                reason=dict(available_bytes=available,
                                            required_bytes=required,
                                            sleeping_backups={name: WEIGHT_BYTES[name]
                                                              for name in workers})))
                            write(level_dir / 'ram-infeasible.json', records[-1])
                            break
                    if key not in workers:
                        worker_dir = level_dir / f'worker-{key}'
                        worker_dir.mkdir()
                        cid, command = worker_container(run_dir, level_dir, key, uuids,
                                                        inputs[key])
                        write(level_dir / f'{index:02d}-{key}-created.json',
                              dict(id=cid, command=command, inspect=row(cid)))
                        log = (worker_dir / 'worker.log').open('w')
                        process = subprocess.Popen(['docker', 'start', '--attach', cid],
                                                   stdout=log, stderr=subprocess.STDOUT)
                        until = time.monotonic() + 2400
                        while not (worker_dir / 'ready.json').exists():
                            assert process.poll() is None and time.monotonic() < until, 'no ready'
                            time.sleep(.5)
                        load_seconds = (json.loads((worker_dir / 'ready.json').read_bytes())['ns']
                                        - json.loads((worker_dir / 'waiting.json').read_bytes())['ns']) / 1e9
                        workers[key] = dict(cid=cid, process=process, process_log=log,
                                            dir=worker_dir)
                        stage = dict(kind='load', seconds=round(load_seconds, 3))
                    else:
                        worker = workers[key]
                        started = time.monotonic_ns()
                        ack = send(worker['cid'], worker['dir'], counters[key],
                                   dict(op='wake', level=level, reload_mode=args.reload_mode))
                        counters[key] += 1
                        stage = dict(kind='wake', seconds=round((ack['finished_ns'] - started) / 1e9, 3),
                                     reload_seconds=round((ack.get('reload_ns') or 0) / 1e9, 3),
                                     receipt=ack['receipt'])
                    worker = workers[key]
                    run_ack = send(worker['cid'], worker['dir'], counters[key],
                                   dict(op='run', phase=f'c{len(cycles[key])}'))
                    counters[key] += 1
                    assert all(check['passed'] for check in run_ack['checks'].values()), run_ack['checks']
                    t4 = run_ack['finished_ns']
                    first = worker['dir'] / f"c{len(cycles[key])}-{list(run_ack['checks'])[0]}-first-token.json"
                    cycles[key].append(dict(index=index, t4_ns=t4, stage=stage,
                                            checks=run_ack['checks']))
                    sleep_started = time.monotonic_ns()
                    sleep_ack = send(worker['cid'], worker['dir'], counters[key],
                                     dict(op='sleep', level=level))
                    counters[key] += 1
                    sleep_seconds = (sleep_ack['finished_ns'] - sleep_started) / 1e9
                    backup = sum(row['tags']['weights']['cpu_backup_bytes']
                                 for row in sleep_ack['receipt']['ranks'])
                    records.append(dict(
                        level=level, step=index, model=key, stage=stage,
                        sleep_seconds=round(sleep_seconds, 3), t4_ns=t4,
                        worker_pid=sleep_ack['receipt']['ranks'][0]['identity']['pid'],
                        weights_cpu_backup_bytes=backup,
                        weights_bytes=sum(row['tags']['weights']['bytes']
                                          for row in sleep_ack['receipt']['ranks']),
                        kv_bytes=sum(row['tags']['kv_cache']['bytes']
                                     for row in sleep_ack['receipt']['ranks']),
                        checks=run_ack['checks'],
                    ))
                    holder['phase'] = phase + ':observation'
                    time.sleep(OBSERVATION_SECONDS)
                # shutdown all workers of this level
                for key, worker in workers.items():
                    ack = send(worker['cid'], worker['dir'], counters[key], dict(op='shutdown'))
                    counters[key] += 1
                    code = worker['process'].wait(timeout=1800)
                    assert code == 0, (key, code)
                    final = row(worker['cid'])
                    assert final['State']['ExitCode'] == 0 and not final['State']['Running']
                    write(level_dir / f'worker-{key}-final.json', final)
                    call('docker', 'rm', worker['cid'], timeout=60)
                pids = {}
                for key in {record['model'] for record in records}:
                    pids[key] = sorted({record['worker_pid'] for record in records
                                        if record['model'] == key})
                level_result = dict(
                    level=level, models=models, records=records, worker_pids=pids,
                    pid_continuity=all(len(value) == 1 for value in pids.values()),
                    golds_passed=all(all(check['passed'] for check in record['checks'].values())
                                     for record in records),
                )
                results.append(level_result)
                write(level_dir / 'level-result.json', level_result)
        except BaseException as error:
            failures['error'] = repr(error)
            failures['traceback'] = traceback.format_exc()
            write(run_dir / 'failure.json', failures)
        finally:
            sampler.stop.set()
            sampler.join(timeout=10)
            # Wait for a genuinely empty GPU state, then observe it for the full window.
            zero_until = time.monotonic() + 300
            wait_samples = []
            while time.monotonic() < zero_until:
                sample = sampler.sample('release-wait')
                wait_samples.append(sample)
                if (all(sample.get(f'gpu{i}', -1) == 0 for i in range(8))
                        and sample['compute_apps'] == 0):
                    break
                time.sleep(2)
            begin = time.monotonic_ns()
            release = []
            while time.monotonic_ns() - begin < RELEASE_SECONDS * 10 ** 9:
                release.append(sampler.sample('final-release'))
                time.sleep(1)
            all_zero = all(all(item.get(f'gpu{i}', -1) == 0 for i in range(8))
                           and item['compute_apps'] == 0 for item in release)
            release_seconds = (release[-1]['ns'] - release[0]['ns']) / 1e9 if release else 0
            write(run_dir / 'release-wait.json', dict(
                samples=len(wait_samples),
                zero_reached=all_zero,
                gpu_last=[item.get('gpu0') for item in wait_samples[-3:]],
            ))
            with (run_dir / 'switch-events.csv').open('w') as stream:
                fields = ['level', 'step', 'model', 'stage', 'sleep_seconds', 't4_ns',
                          'worker_pid', 'weights_cpu_backup_bytes', 'weights_bytes', 'kv_bytes']
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for result in results:
                    for record in result['records']:
                        writer.writerow(dict(
                            level=result['level'], step=record['step'], model=record['model'],
                            stage=record['stage']['kind'], sleep_seconds=record['sleep_seconds'],
                            t4_ns=record['t4_ns'], worker_pid=record['worker_pid'],
                            weights_cpu_backup_bytes=record['weights_cpu_backup_bytes'],
                            weights_bytes=record['weights_bytes'],
                            kv_bytes=record['kv_bytes']))
            samples = [json.loads(line) for line in (run_dir / 'resource-samples.jsonl').read_text().splitlines()]
            samples += release
            with (run_dir / 'resource-samples.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(samples[0].keys()))
                writer.writeheader()
                for item in samples:
                    writer.writerow(item)
            qualification = dict(
                group='B2', sequence=sequence_id, rep=args.rep, levels=levels,
                protocol_hash=protocol_doc['protocol_hash'], models=models,
                results=results, failures=failures,
                final_release_seconds=round(release_seconds, 3),
                final_all_gpu_zero=all_zero,
                passed=bool(results) and not failures
                       and all(result['pid_continuity'] and result['golds_passed']
                               for result in results),
            )
            write(run_dir / 'qualification.json', qualification)
            print(json.dumps(dict(run=run_dir.name,
                                  passed=qualification['passed'],
                                  levels=[dict(level=r['level'],
                                               pid_continuity=r['pid_continuity'],
                                               golds=r['golds_passed']) for r in results],
                                  final_release_seconds=qualification['final_release_seconds'],
                                  final_all_gpu_zero=all_zero), indent=2))


if __name__ == '__main__':
    main()
