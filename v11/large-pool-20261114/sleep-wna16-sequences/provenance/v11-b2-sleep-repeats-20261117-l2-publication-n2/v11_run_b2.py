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
from datetime import datetime
import hashlib
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


CAMPAIGN = pathlib.Path(__file__).resolve().parent

def start_guard(cid, run_dir, deadline_ns, tag, purpose='shared-store-validation'):
    log = (run_dir / (tag + '-guard.log')).open('x')
    guard = subprocess.Popen(
        ['python3', str(B / 'sequence_guard.py'), cid, str(deadline_ns),
         str(run_dir / (tag + '-guard')), '--purpose', purpose],
        stdout=log, stderr=subprocess.STDOUT,
        env=dict(os.environ, PYTHONPATH=str(B / 'full-preparation/v4-source/sllm_store')),
        start_new_session=True)
    until = time.monotonic() + 15
    while not (run_dir / (tag + '-guard/armed.json')).exists():
        assert guard.poll() is None and time.monotonic() < until
        time.sleep(.1)
    return guard, log

def residency(containers):
    gpu, apps = gpu_state()
    processes = {}
    for name, cid in containers.items():
        pids = call('docker', 'top', cid, '-eo', 'pid').splitlines()[1:]
        rows = []
        for pid in map(str.strip, pids):
            proc = pathlib.Path('/proc') / pid
            row_data = {'pid': int(pid)}
            for filename in ('status', 'smaps_rollup'):
                try:
                    fields = (proc / filename).read_text().splitlines()
                except OSError:
                    continue
                for line in fields:
                    key, _, value = line.partition(':')
                    if key in ('VmRSS', 'VmLck', 'Pss', 'Threads'):
                        row_data[key] = value.strip()
            rows.append(row_data)
        processes[name] = rows
    return dict(ns=time.monotonic_ns(), gpu=gpu.splitlines(), compute_apps=apps.splitlines(),
                processes=processes, host_memory=pathlib.Path('/proc/meminfo').read_text())

def admit_sf(checkpoint, step_dir, deadline, run_id):
    code = B.parent / 'code'
    index = json.loads((checkpoint / 'model.safetensors.index.json').read_bytes())
    paths = {checkpoint / shard for shard in index['weight_map'].values()}
    helper = CAMPAIGN / 'sf_admission.py'
    output = step_dir / 'sf-admission'
    if all(path.stat().st_uid == os.geteuid() for path in paths):
        call(sys.executable, str(helper), str(checkpoint), str(code),
             str(output), str(deadline), run_id, timeout=300)
    else:
        profile = profiles['v4']
        # CPU-only helper for root-owned model files. It writes into a
        # temporary container directory, then transfers the receipt as UID 2147.
        command = (
            '"$1" /helper.py /checkpoint /source /tmp/receipt "$2" "$3"; result=$?; '
                'if test -d /tmp/receipt; then tar -C /tmp/receipt -cf - . || exit 1; fi; exit "$result"'
        )
        output.mkdir()
        with (output / 'receipt.tar').open('xb') as archive, (output / 'helper.log').open('xb') as errors:
            result = subprocess.run([
                'docker', 'run', '--rm', '--network', 'none', '--user', '0:0',
                '--cap-drop', 'ALL', '--cap-add', 'FOWNER', '--cap-add', 'DAC_READ_SEARCH',
                '-e', 'NVIDIA_VISIBLE_DEVICES=void',
                '--mount', f'type=bind,src={checkpoint},dst=/checkpoint,readonly',
                '--mount', f'type=bind,src={code},dst=/source,readonly',
                '--mount', f'type=bind,src={helper},dst=/helper.py,readonly',
                '--entrypoint', '/bin/sh', profile['image'], '-c', command, 'sf-admission',
                profile['python'], str(deadline), run_id,
            ], stdout=archive, stderr=errors, timeout=300)
        if (output / 'receipt.tar').stat().st_size:
            call('tar', '-xf', str(output / 'receipt.tar'), '-C', str(output))
        result.check_returncode()
    assert json.loads((output / 'summary.json').read_bytes())['cache_condition_passed'] is True

def native_input(run_dir, key, kv_cache_gib=None):
    name = MODEL_INPUT[key]
    document = json.loads((V7 / 'candidates-v2' / (name + '-input.json')).read_bytes())
    config = document['config']
    config['load_format'] = 'auto'
    config['pretrained_model_name_or_path'] = NATIVE_PATHS[name]
    config['enable_prefix_caching'] = False
    config['enable_sleep_mode'] = True
    config['worker_extension_cls'] = 'sllm_store.v7_sleep_worker.V7SleepWorkerExtension'
    config.pop('model_loader_extra_config', None)
    if kv_cache_gib is not None:
        config['kv_cache_memory_bytes'] = kv_cache_gib * 1024 ** 3
    path = run_dir / 'b2-inputs' / (name + '-input.json')
    path.write_text(json.dumps(document, indent=2) + '\n')
    return path


def worker_container(run_dir, level_dir, key, gpus, input_path, level=2):
    profile = profiles[MODEL_INPUT[key]]
    name = MODEL_INPUT[key]
    cid_name = f'sllm-v11-{run_dir.name}-{level_dir.name}-{key}'
    args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
            '--gpus', '"device=' + ','.join(gpus) + '"', '--cpus', '32', '--memory', '1664g' if key == 'K' else '1400g',
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
    sink_overlay = str(V11 / 'b2-v4-sink-router-overlay/deepseek_v4.py') if key == 'D' and level == 2 else None
    if sink_overlay:
        env['CUDA_LAUNCH_BLOCKING'] = '1'
    if key == 'D' and level == 2:
        env['SLLM_V11_V4_MARLIN_REBIND'] = '1'
    config = json.loads((CAMPAIGN / 'campaign.json').read_bytes())
    site = '/opt/sllm-v4/lib/python3.12/site-packages/'
    expected = {}
    if key == 'D' and level == 2:
        expected[site+'vllm/model_executor/model_loader/reload/layerwise.py'] = config['candidate_overlay_sha256']
        for overlay in config['expected_v4_overlay']:
            if overlay['destination'].endswith('deepseek_v4.py'):
                expected[overlay['destination']] = overlay['sha256']
    if key == 'K':
        expected.update({row['target']: row['candidate_sha256'] for row in config['kimi_overlays']})
    env['SLLM_B2_MODEL_KEY'] = key
    env['SLLM_B2_PATCH_IMPORTS'] = json.dumps({
        target.removeprefix(site).removesuffix('.py').replace('/', '.'): dict(path=target, sha256=fingerprint)
        for target, fingerprint in expected.items()})
    for key_name, value in env.items():
        args += ['-e', key_name + '=' + value]
    mounts = list(profile['mounts']) + [
        (str(CAMPAIGN / 'v11_sleep_backend.py'), '/control/script.py'),
        (str(V11 / 'v11_backend.py'), '/control/v11_lib.py'),
        (str(V7 / 'v7_backend.py'), '/control/v7_lib.py'),
        (str(input_path), '/control/input.json'),
        ('/data', '/data'), ('/etc/passwd', '/etc/passwd'), ('/etc/group', '/etc/group'),
    ]
    if sink_overlay:
        mounts.append((sink_overlay, '/opt/sllm-v4/lib/python3.12/site-packages/'
                       'vllm/model_executor/models/deepseek_v4.py'))
        overlay_dir = pathlib.Path(sink_overlay).parent
        pins_target = '/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/v4_source_pins.json'
        assert sum(target == pins_target for _, target in mounts) == 1
        mounts = [(str(overlay_dir / 'v4_source_pins.json') if target == pins_target
                   else source, target) for source, target in mounts]
        if (overlay_dir / 'meta.py').is_file():
            mounts.append((str(overlay_dir / 'meta.py'),
                           '/opt/sllm-v4/lib/python3.12/site-packages/'
                           'vllm/model_executor/model_loader/reload/meta.py'))
    mounts.append((str(CAMPAIGN / 'cumem.py'),
                   '/opt/sllm-v4/lib/python3.12/site-packages/vllm/device_allocator/cumem.py'))
    if key == 'D' and level == 2:
        assert all(target != '/opt/sllm-v4/lib/python3.12/site-packages/vllm/model_executor/model_loader/reload/layerwise.py' for _, target in mounts)
        mounts.append((str(CAMPAIGN / 'layerwise.py'), '/opt/sllm-v4/lib/python3.12/site-packages/vllm/model_executor/model_loader/reload/layerwise.py'))
    assert sum(target.endswith('/sllm_store/v7_sleep_worker.py') for _, target in mounts) == 1
    mounts = [(str(CAMPAIGN / 'v7_sleep_worker.py') if target.endswith('/sllm_store/v7_sleep_worker.py')
              else source, target) for source, target in mounts]
    if key == 'K':
        assert profile['image'] == config['expected_kimi_image']
        checkpoint = config['kimi_checkpoint']
        assert (checkpoint['host_path'], checkpoint['path']) in map(tuple, mounts)
        assert not {row['target'] for row in config['kimi_overlays']} & {target for _, target in mounts}
        mounts.extend((row['source'], row['target']) for row in config['kimi_overlays'])
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


class MemorySampler(Sampler):
    def sample(self, phase):
        sample = super().sample(phase)
        cid = self.holder.get('diagnostic_cid')
        if not cid:
            return sample
        try:
            pid = row(cid)['State']['Pid']
            if not pid:
                return sample
            relative = pathlib.Path(f'/proc/{pid}/cgroup').read_text().strip().removeprefix('0::')
            group = pathlib.Path('/sys/fs/cgroup') / relative.lstrip('/')
            memory = {}
            for name in ('current', 'max', 'peak', 'swap.current', 'events', 'stat'):
                path = group / ('memory.' + name)
                if path.exists():
                    raw = path.read_text().strip()
                    memory[name] = (dict((key, int(value)) for key, value in
                                         (line.split() for line in raw.splitlines()))
                                    if name in {'events', 'stat'} else raw)
            processes = {}
            for item in (group / 'cgroup.procs').read_text().split():
                try:
                    fields = pathlib.Path(f'/proc/{item}/status').read_text().splitlines()
                    processes[item] = dict(line.split(':', 1) for line in fields
                                           if line.startswith(('Name:', 'VmRSS:', 'RssAnon:',
                                                               'RssFile:', 'VmSwap:', 'VmLck:')))
                except (FileNotFoundError, ProcessLookupError):
                    continue
            sample.update(cgroup_memory=memory, cgroup_processes=processes)
        except (OSError, RuntimeError, KeyError, ValueError) as exc:
            sample['cgroup_sample_error'] = repr(exc)
        return sample

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequence', required=True, choices=('S1', 'S2', 'S3', 'L1-D'))
    parser.add_argument('--booking-start', required=True)
    parser.add_argument('--booking-end', required=True)
    parser.add_argument('--models', default=None, help='ad-hoc model list for a smoke test')
    parser.add_argument('--levels', default='1,2')
    parser.add_argument('--reload-mode', default='checkpoint', choices=('checkpoint', 'kernel'))
    parser.add_argument('--kv-cache-gib', type=int)
    parser.add_argument('--rep', type=int, default=1)
    args = parser.parse_args()
    l1 = args.sequence == 'L1-D'
    if args.models or args.reload_mode != 'checkpoint' or (
            (args.levels, args.kv_cache_gib) != (('1', None) if l1 else ('2', 16))):
        parser.error('this cohort requires fixed S1/S2/S3, L2 checkpoint and 16 GiB per rank')
    start, end = map(datetime.fromisoformat, (args.booking_start, args.booking_end))
    if start.utcoffset() is None or end.utcoffset() is None:
        parser.error('booking timestamps require explicit offsets')
    wall = time.time()
    available_s = end.timestamp() - wall - 480
    if wall < start.timestamp() or available_s <= 0:
        parser.error('outside booking execution window')
    deadline_ns = time.monotonic_ns() + int(min(available_s, 14000) * 10**9)
    levels = [int(item) for item in args.levels.split(',') if item.strip()]
    if args.kv_cache_gib is not None and (levels != [2] or args.kv_cache_gib <= 0):
        parser.error('--kv-cache-gib requires a positive value and --levels 2')
    assert socket.gethostname() == 'qjhs3'
    with (V11 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config = (CAMPAIGN / 'campaign.json').read_bytes()
        sequences = dict(sequences=[dict(id=k, models=v) for k, v in json.loads(config)['sequences'].items()])
        protocol_doc = json.loads(config)
        protocol_doc['protocol_hash'] = hashlib.sha256(config).hexdigest()
        assert hashlib.sha256((CAMPAIGN / 'layerwise.py').read_bytes()).hexdigest() == protocol_doc['candidate_overlay_sha256']
        assert profiles['v4']['image'] == protocol_doc['expected_v4_image']
        for item in protocol_doc['expected_v4_overlay']:
            source = pathlib.Path(item['source']).read_bytes()
            assert not item['rw'] and len(source) == item['size']
            assert hashlib.sha256(source).hexdigest() == item['sha256']
        assert profiles['kimi']['image'] == protocol_doc['expected_kimi_image']
        assert hashlib.sha256((CAMPAIGN / 'wna16-reload.patch').read_bytes()).hexdigest() == protocol_doc['kimi_patch_sha256']
        assert hashlib.sha256((CAMPAIGN / 'cumem.py').read_bytes()).hexdigest() == protocol_doc['allocator_sha256']
        assert hashlib.sha256((V7 / 'candidates-v2/profiles.json').read_bytes()).hexdigest() == protocol_doc['profile_sha256']
        for overlay in protocol_doc['kimi_overlays']:
            assert hashlib.sha256(pathlib.Path(overlay['source']).read_bytes()).hexdigest() == overlay['candidate_sha256']
        ckpt = protocol_doc['kimi_checkpoint']
        for file, key in (('config.json', 'config_sha256'), ('model.safetensors.index.json', 'safetensors_index_sha256')):
            assert hashlib.sha256((pathlib.Path(ckpt['host_path']) / file).read_bytes()).hexdigest() == ckpt[key]
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
        if l1:
            sealed = (CAMPAIGN / 'capacity-admission.json').read_bytes()
            assert hashlib.sha256(sealed).hexdigest() == protocol_doc['l1_capacity_sha256']
            profiles_l1 = json.loads(sealed)['cases']['L1-D']['records']
            assert all(available >= item['required_host_bytes'] for item in profiles_l1)
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store must not run for B2'

        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        run_dir = CAMPAIGN / 'runs' / (f'B2-{sequence_id}-r{args.rep}-{stamp}' if l1 else f'B2-WNA16-{sequence_id}-r{args.rep}-{stamp}')
        (run_dir / 'b2-inputs').mkdir(parents=True)
        write(run_dir / 'admission.json', dict(
            group='B2', levels=levels, sequence=sequence_id, rep=args.rep,
            kv_cache_gib=args.kv_cache_gib, reload_mode=args.reload_mode,
            protocol_hash=protocol_doc['protocol_hash'], gpu=gpu.splitlines(),
            apps=apps.splitlines(), available_bytes=available, gpus=uuids,
            checked_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
            instruction='V11 B2: native Sleep, no SLLM Store, L1 and L2 in one group'))
        inputs = {key: native_input(run_dir, key, args.kv_cache_gib) for key in MODEL_INPUT}
        for key, path in inputs.items():
            if l1 and key != 'D':
                continue
            expected_input = protocol_doc['l1_historical_input_sha256'] if l1 else protocol_doc['expected_input_sha256'][key]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_input, path
        write(run_dir / 'campaign-input.json', protocol_doc)
        write(run_dir / 'booking.json', dict(start=args.booking_start, end=args.booking_end,
              guard_deadline_ns=deadline_ns, cleanup_reserve_s=480))
        write(run_dir / 'instrumentation.json', {
            name: hashlib.sha256((CAMPAIGN / name).read_bytes()).hexdigest()
            for name in ('v11_run_b2.py', 'v11_sleep_backend.py', 'cumem.py',
                         'sf_admission.py', 'campaign.json', 'layerwise.py', 'candidate-manifest.json')
        })
        owned = {}
        holder = {'cid': None, 'phase': 'startup'}
        sampler = MemorySampler(run_dir / 'resource-samples.jsonl', holder)
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
                    if level == 1 or workers:
                        free = [int(line) for line in call(
                            'nvidia-smi', '--query-gpu=memory.free',
                            '--format=csv,noheader,nounits').splitlines()]
                        kv_bytes = protocol_doc['l1_kv_bytes']//8 if l1 else args.kv_cache_gib * 1024 ** 3
                        if l1 and key in workers:
                            # A sleeping L1 worker already owns its CUDA context. Native
                            # wake restores the tagged allocations without checkpoint scratch.
                            wake_bytes = workers[key]['l1_wake_max_bytes']
                            required_mib = (wake_bytes + 2**20 - 1) // 2**20
                        else:
                            required_mib = (WEIGHT_BYTES[key] // 8 + kv_bytes) // 1024 ** 2 + 8192
                        gate = dict(free_mib=free, required_mib=required_mib,
                                    passed=len(free) == 8 and min(free) >= required_mib)
                        write(level_dir / f'{index:02d}-{key}-vram-gate.json', gate)
                        if not gate['passed']:
                            break
                    if key not in workers:
                        worker_dir = level_dir / f'worker-{key}'
                        worker_dir.mkdir()
                        assert time.monotonic_ns() < deadline_ns
                        if index == 0:
                            (checkpoint,) = [src for src, dst in profiles[MODEL_INPUT[key]]['mounts']
                                             if dst == NATIVE_PATHS[MODEL_INPUT[key]]]
                            admit_sf(pathlib.Path(checkpoint), worker_dir, deadline_ns, run_dir.name)
                        cid, command = worker_container(run_dir, level_dir, key, uuids,
                                                        inputs[key], level)
                        write(level_dir / f'{index:02d}-{key}-created.json',
                              dict(id=cid, command=command, inspect=row(cid)))
                        owned[cid] = (None, None)
                        owned[cid] = start_guard(cid, run_dir, deadline_ns, phase)
                        log = (worker_dir / 'worker.log').open('w')
                        process = subprocess.Popen(['docker', 'start', '--attach', cid],
                                                   stdout=log, stderr=subprocess.STDOUT)
                        if l1:
                            holder['diagnostic_cid'] = cid
                        until = time.monotonic() + 2400
                        while not (worker_dir / 'ready.json').exists():
                            assert process.poll() is None and time.monotonic() < until, 'no ready'
                            time.sleep(.5)
                        load_seconds = (json.loads((worker_dir / 'ready.json').read_bytes())['ns']
                                        - json.loads((worker_dir / 'waiting.json').read_bytes())['ns']) / 1e9
                        workers[key] = dict(cid=cid, process=process, process_log=log,
                                            dir=worker_dir)
                        stage = dict(kind='load', seconds=round(load_seconds, 3),
                                     ready_ns=json.loads((worker_dir / 'ready.json').read_bytes())['ns'])
                    else:
                        worker = workers[key]
                        if key == 'K':
                            holder['diagnostic_cid'] = worker['cid']
                            holder['phase'] = phase + ':wake'
                            write(level_dir / f'{phase}-memory-before.json', sampler.sample(holder['phase'] + ':before'))
                        wake_command_index = counters[key]
                        started = time.monotonic_ns()
                        ack = send(worker['cid'], worker['dir'], counters[key],
                                   dict(op='wake', level=level, reload_mode=args.reload_mode))
                        counters[key] += 1
                        if key == 'K':
                            write(level_dir / f'{phase}-memory-after.json', sampler.sample(holder['phase'] + ':after'))
                            holder.pop('diagnostic_cid')
                        stage = dict(kind='wake', seconds=round((ack['finished_ns'] - started) / 1e9, 3),
                                     reload_seconds=round((ack.get('reload_ns') or 0) / 1e9, 3),
                                     ready_ns=ack['finished_ns'],
                                     phases_receipt=f'wake-{wake_command_index:04d}-phases.json',
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
                    if l1 and stage['kind'] == 'wake':
                        # The single-cycle protocol ends awake after the second gold.
                        ranks = stage['receipt']['ranks']
                        assert {rank['rank'] for rank in ranks} == set(range(8))
                        assert all(rank['tags']['weights']['cpu_backup_bytes'] == 0 for rank in ranks)
                        records.append(dict(
                            level=level, step=index, model=key, stage=stage,
                            sleep_seconds=None, t4_ns=t4, sleep_phases_receipt=None,
                            worker_pid=ranks[0]['identity']['pid'], weights_cpu_backup_bytes=0,
                            weights_bytes=sum(rank['tags']['weights']['bytes'] for rank in ranks),
                            kv_bytes=sum(rank['tags']['kv_cache']['bytes'] for rank in ranks),
                            checks=run_ack['checks'],
                        ))
                        write(level_dir / f'{phase}-awake-before-shutdown.json',
                              residency({name: item['cid'] for name, item in workers.items()}))
                        holder['phase'] = phase + ':awake-before-shutdown'
                        continue
                    if l1:
                        pressure = sampler.sample(phase + ':before-backup')
                        cgroup = pressure['cgroup_memory']
                        stat = cgroup['stat']
                        nonfile = max(int(cgroup['current']) - stat['file'], 0) + stat['shmem']
                        required = protocol_doc['l1_backup_bytes'] + RAM_MARGIN_BYTES
                        gate = dict(host_available_bytes=pressure['mem_available'], required_for_backup_bytes=required,
                                    cgroup_nonfile_bytes=nonfile, cgroup_limit_bytes=int(cgroup['max']))
                        write(level_dir / f'{phase}-backup-capacity.json', gate)
                        assert gate['host_available_bytes'] >= required and nonfile + required <= gate['cgroup_limit_bytes'], gate
                    sleep_command_index = counters[key]
                    sleep_started = time.monotonic_ns()
                    sleep_ack = send(worker['cid'], worker['dir'], counters[key],
                                     dict(op='sleep', level=level))
                    counters[key] += 1
                    sleep_seconds = (sleep_ack['finished_ns'] - sleep_started) / 1e9
                    backup = sum(row['tags']['weights']['cpu_backup_bytes']
                                 for row in sleep_ack['receipt']['ranks'])
                    if l1:
                        assert backup == protocol_doc['l1_backup_bytes'], backup
                        workers[key]['l1_wake_max_bytes'] = max(
                            rank['tags']['weights']['bytes'] + rank['tags']['kv_cache']['bytes']
                            for rank in sleep_ack['receipt']['ranks'])

                    records.append(dict(
                        level=level, step=index, model=key, stage=stage,
                        sleep_seconds=round(sleep_seconds, 3), t4_ns=t4,
                        sleep_phases_receipt=f'sleep-{sleep_command_index:04d}-phases.json',
                        worker_pid=sleep_ack['receipt']['ranks'][0]['identity']['pid'],
                        weights_cpu_backup_bytes=backup,
                        weights_bytes=sum(row['tags']['weights']['bytes']
                                          for row in sleep_ack['receipt']['ranks']),
                        kv_bytes=sum(row['tags']['kv_cache']['bytes']
                                     for row in sleep_ack['receipt']['ranks']),
                        checks=run_ack['checks'],
                    ))
                    write(level_dir / f'{phase}-post-sleep-residency.json',
                          residency({name: item['cid'] for name, item in workers.items()}))
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
                    guard, guard_log = owned[worker['cid']]
                    assert guard.wait(timeout=15) == 0
                    guard_log.close()
                    call('docker', 'rm', worker['cid'], timeout=60)
                    owned.pop(worker['cid'])
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
            for cid, (guard, guard_log) in owned.items():
                try:
                    if row(cid)['State']['Running']:
                        call('docker', 'stop', '--time', '30', cid, timeout=45)
                except Exception as error:
                    failures.setdefault('cleanup', {})[cid] = repr(error)
                if guard_log:
                    guard_log.close()
            sampler.stop.set()
            sampler.join(timeout=10)
            # Reset the observation when a sample is unavailable or the GPU is busy.
            zero_until = time.monotonic() + 300
            wait_samples = []
            release = []
            sample_errors = []
            while time.monotonic() < zero_until:
                try:
                    sample = sampler.sample('final-release')
                except (subprocess.TimeoutExpired, RuntimeError) as error:
                    sample_errors.append(dict(ns=time.monotonic_ns(), error=repr(error)))
                    release.clear()
                    continue
                zero = (all(sample.get(f'gpu{i}', -1) == 0 for i in range(8))
                        and sample['compute_apps'] == 0)
                if zero:
                    release.append(sample)
                    if release[-1]['ns'] - release[0]['ns'] >= RELEASE_SECONDS * 10 ** 9:
                        break
                else:
                    wait_samples.append(dict(sample, phase='release-wait'))
                    release.clear()
                time.sleep(1)
            all_zero = bool(release) and all(all(item.get(f'gpu{i}', -1) == 0 for i in range(8))
                           and item['compute_apps'] == 0 for item in release)
            release_seconds = (release[-1]['ns'] - release[0]['ns']) / 1e9 if release else 0
            write(run_dir / 'release-wait.json', dict(
                samples=len(wait_samples),
                sample_errors=sample_errors,
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
            with (run_dir / 'final-release.jsonl').open('x') as stream:
                for item in release:
                    stream.write(json.dumps(item) + '\n')
            samples += release
            fields = list(dict.fromkeys(key for item in samples for key in item))
            with (run_dir / 'resource-samples.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for item in samples:
                    writer.writerow(item)
            qualification = dict(
                group='B2', sequence=sequence_id, rep=args.rep, levels=levels,
                protocol_hash=protocol_doc['protocol_hash'], models=models,
                results=results, failures=failures,
                final_release_seconds=round(release_seconds, 3),
                final_all_gpu_zero=all_zero,
                passed=bool(results) and not failures and all_zero
                       and release_seconds >= RELEASE_SECONDS
                       and all(len(result['records']) == len(models)
                               and all(record['stage']['kind'] in ('load', 'wake')
                                       for record in result['records'])
                               and result['pid_continuity'] and result['golds_passed']
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
