"""V11 campaign controller — path A (shared SLLM weight Store), G1 and B1.

One persistent Store, one CPU weight pool, N serial backend activations taken from
the frozen `switch-sequences.json`. Produces switch-events.csv, resource-samples.csv,
store-ledger.csv and qualification.json for the run directory.

Group B1 additionally runs one persistent LMCache MP server as the CPU Host KV layer
(no L2 adapter, prefix caching off) and clears that tier between activations, recording
memory_used_bytes before and after each clear. B2 (native Sleep) has its own runner.
"""
import argparse
import collections
import csv
import fcntl
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

B = pathlib.Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = B / 'v7'
V11 = B / 'v11'
sys.path.insert(1, str(B))
from cache_ledger import Ledger, self_test  # noqa: E402

MODEL_INPUT = {'K': 'kimi', 'G': 'glm', 'D': 'v4'}
STORE_POOL_BYTES = 1024 ** 4
STORE_CHUNK_BYTES = 32 * 1024 ** 2
OBSERVATION_SECONDS = 60
RELEASE_SECONDS = 60
LMCACHE_SITE = '/data/brick1/boyjiang/lmcache-site'
LMCACHE_HTTP_PORT = 9100
LMCACHE_MQ_PORT = 5555
LMCACHE_TIER_GB = 16

protocol = json.loads((V7 / 'protocol.json').read_bytes())
profiles = json.loads((V7 / 'candidates-v2/profiles.json').read_bytes())
MODEL_PATHS = {key: protocol['model_paths'][name] for key, name in MODEL_INPUT.items()}
PATH_SIZES = protocol['path_sizes']

def call(*args, timeout=60):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(str(args) + '\n' + result.stdout)
    return result.stdout.strip()


def row(cid):
    return json.loads(call('docker', 'inspect', cid))[0]


def write(path, data):
    pending = path.with_name(path.name + '.pending')
    with pending.open('x') as stream:
        json.dump(data, stream, indent=2)
    os.link(pending, path)
    pending.unlink()


def gpu_state():
    gpu = call('nvidia-smi', '--query-gpu=index,uuid,memory.used', '--format=csv,noheader,nounits')
    apps = call('nvidia-smi', '--query-compute-apps=pid,used_gpu_memory', '--format=csv,noheader,nounits')
    return gpu, apps


def http_json(method, path, body=None, port=LMCACHE_HTTP_PORT, timeout=10):
    """Minimal JSON call against the LMCache MP HTTP API."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f'http://127.0.0.1:{port}{path}', data=data,
                                     method=method,
                                     headers={'content-type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode()
            return dict(status=response.status, body=json.loads(payload or '{}'))
    except urllib.error.HTTPError as error:
        payload = error.read().decode()
        try:
            parsed = json.loads(payload or '{}')
        except json.JSONDecodeError:
            parsed = {'raw': payload}
        return dict(status=error.code, body=parsed)
    except Exception as error:  # connection refused while starting
        return dict(status=0, body={'error': repr(error)})


def lmcache_l1(status_body):
    try:
        return status_body['storage_manager']['l1_managers']['_default']
    except (KeyError, TypeError):
        return {}


def lmc_server_container(run_dir, gpus):
    profile = profiles['v4']
    (run_dir / 'lmcache').mkdir(parents=True, exist_ok=True)
    args = ['docker', 'create', '--network', 'host', '--ipc', 'host',
            '--gpus', '"device=' + ','.join(gpus) + '"',
            '--cpus', '8', '--memory', '128g', '--pids-limit', '4096',
            '--ulimit', 'memlock=-1:-1', '--restart', 'no', '--user', '2147:2147',
            '--label', 'sllm.v4.guard=shared-store-validation',
            '--name', 'sllm-v11-' + run_dir.name + '-lmcache',
            '--tmpfs', '/tmp:rw,exec,nosuid,size=4g,mode=1777',
            '--entrypoint', profile['python']]
    env = dict(profile['env'], HOME='/output', USER='boyjiang', LOGNAME='boyjiang',
               PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=LMCACHE_SITE,
               CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
               NVIDIA_VISIBLE_DEVICES=','.join(gpus))
    for key, value in env.items():
        args += ['-e', key + '=' + value]
    args += ['--mount', 'type=bind,src=' + LMCACHE_SITE + ',dst=' + LMCACHE_SITE + ',readonly']
    args += ['--mount', 'type=bind,src=' + str(run_dir / 'lmcache') + ',dst=/output']
    args += [profile['image'], '-m', 'lmcache.v1.multiprocess.http_server',
             '--host', '127.0.0.1', '--port', str(LMCACHE_MQ_PORT), '--transport', 'zmq',
             '--http-host', '0.0.0.0', '--http-port', str(LMCACHE_HTTP_PORT),
             '--l1-size-gb', str(LMCACHE_TIER_GB), '--l1-init-size-gb', str(LMCACHE_TIER_GB),
             '--eviction-policy', 'LRU']
    cid = call(*args, timeout=120)
    return cid, args


class Sampler(threading.Thread):
    """1 Hz resource sampling: 8 GPUs, host memory, Store process."""

    def __init__(self, path, store_cid_holder, poll_lmcache=False):
        super().__init__(daemon=True)
        self.path = path
        self.holder = store_cid_holder
        self.poll_lmcache = poll_lmcache
        self.stop = threading.Event()
        self.rows = 0

    def sample(self, phase):
        gpu, apps = gpu_state()
        memory = {}
        for line in pathlib.Path('/proc/meminfo').read_text().splitlines():
            key = line.split(':')[0]
            if key in ('MemAvailable', 'MemTotal', 'Mlocked'):
                memory[key] = int(line.split()[1]) * 1024
        store_pid = None
        if self.holder.get('cid'):
            try:
                store_pid = row(self.holder['cid'])['State']['Pid']
            except Exception:
                store_pid = None
        rss = None
        if store_pid:
            try:
                for line in pathlib.Path('/proc', str(store_pid), 'status').read_text().splitlines():
                    if line.startswith('VmRSS:'):
                        rss = int(line.split()[1]) * 1024
            except FileNotFoundError:
                rss = None
        per_gpu = [int(item.split(',')[2]) for item in gpu.splitlines()]
        row_data = dict(
            ns=time.monotonic_ns(), wall_ns=time.time_ns(), phase=phase,
            **{f'gpu{index}': value for index, value in enumerate(per_gpu)},
            compute_apps=len([line for line in apps.splitlines() if line.strip()]),
            mem_available=memory.get('MemAvailable'), mem_total=memory.get('MemTotal'),
            mlocked=memory.get('Mlocked'), store_pid=store_pid, store_rss=rss,
        )
        if self.poll_lmcache:
            status = http_json('GET', '/status', timeout=5)
            l1 = lmcache_l1(status['body']) if status['status'] == 200 else {}
            row_data.update(
                lmcache_status=status['status'],
                lmcache_used_bytes=l1.get('memory_used_bytes'),
                lmcache_ratio=l1.get('memory_usage_ratio'),
                lmcache_sessions=status['body'].get('active_sessions') if status['status'] == 200 else None,
            )
        return row_data

    def run(self):
        while not self.stop.is_set():
            row_data = self.sample(self.holder.get('phase', 'run'))
            with self.path.open('a') as stream:
                stream.write(json.dumps(row_data) + '\n')
            self.rows += 1
            self.stop.wait(1.0)


def store_container(run_dir, gpus):
    (run_dir / 'store').mkdir(parents=True, exist_ok=True)
    profile = profiles['v4']
    args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
            '--gpus', '"device=' + ','.join(gpus) + '"', '--cpus', '4', '--memory', '1280g',
            '--pids-limit', '4096', '--ulimit', 'memlock=-1:-1', '--stop-signal', 'SIGINT',
            '--restart', 'no', '--user', '2147:2147',
            '--label', 'sllm.v4.guard=shared-store-validation',
            '--name', 'sllm-v11-' + run_dir.name + '-store',
            '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777',
            '--entrypoint', profile['python']]
    env = dict(profile['env'], STORAGE_PATH='/data', CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
               NVIDIA_VISIBLE_DEVICES=','.join(gpus), HOME='/output', USER='boyjiang',
               LOGNAME='boyjiang', PYTHONDONTWRITEBYTECODE='1',
               LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:'
                               '/host-cuda/lib64:/usr/local/cuda/lib64:'
                               '/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')
    for key, value in env.items():
        args += ['-e', key + '=' + value]
    mounts = [
        (str(B / 'shared_store.py'), '/control/script.py'),
        ('/data', '/data'), ('/etc/passwd', '/etc/passwd'), ('/etc/group', '/etc/group'),
        (str(B / 'cache-observer-build-v1/observed_checkpoint_store.so'),
         '/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/'
         '_checkpoint_store.cpython-312-x86_64-linux-gnu.so'),
    ]
    for source, target in mounts:
        args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + ',readonly']
    args += ['--mount', 'type=bind,src=' + str(run_dir / 'store') + ',dst=/output',
             profile['image'], '/control/script.py',
             '--root', '/data', '--pool', str(STORE_POOL_BYTES), '--chunk', str(STORE_CHUNK_BYTES)]
    cid = call(*args, timeout=120)
    return cid, args


def backend_container(run_dir, step_dir, key, gpus, input_path=None, with_lmcache=False):
    name = MODEL_INPUT[key]
    profile = profiles[name]
    cid_name = 'sllm-v11-' + run_dir.name + '-' + step_dir.name
    args = ['docker', 'create', '--network', 'host', '--ipc', 'host', '--pid', 'host',
            '--gpus', '"device=' + ','.join(gpus) + '"', '--cpus', '32', '--memory', '512g',
            '--pids-limit', '4096', '--ulimit', 'memlock=-1:-1', '--stop-signal', 'SIGINT',
            '--restart', 'no', '--user', '2147:2147',
            '--label', 'sllm.v4.guard=shared-store-validation', '--name', cid_name,
            '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777', '--entrypoint', profile['python']]
    env = dict(profile['env'], STORAGE_PATH='/data', CUDA_VISIBLE_DEVICES='0,1,2,3,4,5,6,7',
               NVIDIA_VISIBLE_DEVICES=','.join(gpus), HOME='/output', USER='boyjiang',
               LOGNAME='boyjiang', PYTHONDONTWRITEBYTECODE='1',
               SLLM_EVENT_DIR='/output/events', SLLM_EVENT_RUN_ID=run_dir.name + '-' + step_dir.name,
               SLLM_REQUEST_PREFIX=step_dir.name,
               LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/host-cuda/lib64:'
                               '/usr/local/cuda/lib64:/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')
    if with_lmcache:
        env['PYTHONPATH'] = LMCACHE_SITE
    for key_name, value in env.items():
        args += ['-e', key_name + '=' + value]
    input_source = str(input_path) if input_path else str(
        V7 / 'candidates-v2' / (name + '-input.json'))
    mounts = list(profile['mounts']) + [
        (str(V11 / 'v11_backend.py'), '/control/script.py'),
        (str(V7 / 'v7_backend.py'), '/control/v7_lib.py'),
        (input_source, '/control/input.json'),
        ('/data', '/data'), ('/etc/passwd', '/etc/passwd'), ('/etc/group', '/etc/group'),
    ]
    if with_lmcache:
        mounts.append((LMCACHE_SITE, LMCACHE_SITE))
    for source, target in mounts:
        args += ['--mount', 'type=bind,src=' + source + ',dst=' + target + ',readonly']
    args += ['--mount', 'type=bind,src=' + str(step_dir) + ',dst=/output']
    args += [profile['image'], '/control/script.py']
    cid = call(*args, timeout=120)
    return cid, args


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


def snapshot_store(cid, parsed, ledger):
    raw = call('docker', 'logs', '--timestamps', cid)
    relevant = [line for line in raw.splitlines()
                if any(token in line for token in (
                    'SLLM_CACHE_', 'PinnedMemoryPool ', 'Deallocated ', ' is freed from memory',
                    'Loading model ', 'Finished loading model ', 'Not enough memory for model',
                    ' chunks, remaining ', 'Confirm model '))]
    if relevant[:len(parsed)] != parsed:
        raise RuntimeError('Store native log ordering changed')
    delta = relevant[len(parsed):]
    ledger.feed(delta)
    parsed.extend(delta)
    return delta, ledger.settled()


def rank_coverage(step_dir):
    events = collections.defaultdict(lambda: collections.Counter())
    for path in (step_dir / 'events').glob('*.jsonl'):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get('name') in ('full_weight_load_begin', 'full_weight_load_end'):
                rank = event.get('rank')
                assert type(rank) is int
                events[rank][event['name']] += 1
    assert set(events) == set(range(8)), sorted(events)
    for rank, counts in events.items():
        assert counts['full_weight_load_begin'] == 1 and counts['full_weight_load_end'] == 1, rank
    return dict(ranks=sorted(events), spans=8)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', required=True, choices=('G1', 'A', 'B1'))
    parser.add_argument('--sequence', default=None, help='sequence id from switch-sequences.json')
    parser.add_argument('--models', default=None, help='G1 only, e.g. KGD')
    parser.add_argument('--rep', type=int, default=1)
    args = parser.parse_args()

    assert socket.gethostname() == 'qjhs3'
    with (V11 / 'active.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sequences_doc = json.loads((V11 / 'switch-sequences.json').read_bytes())
        protocol_doc = json.loads((V11 / 'protocol-hash.json').read_bytes())
        protocol_hash = protocol_doc['protocol_hash']
        assert protocol_hash == protocol_doc['protocol_hash']
        self_test()

        if args.group == 'G1':
            models = list(args.models or 'KGD')
            sequence_id = 'G1-baseline'
        elif args.group == 'B1' and args.models:
            models = list(args.models)
            sequence_id = 'B1-smoke'
        else:
            assert args.sequence, 'path A needs a frozen sequence id'
            match = next(row for row in sequences_doc['sequences'] if row['id'] == args.sequence)
            models = match['models']
            sequence_id = match['id']

        gpu, apps = gpu_state()
        uuids = [item.split(',')[1].strip() for item in gpu.splitlines()]
        baseline = [int(item.split(',')[2]) for item in gpu.splitlines()]
        assert len(uuids) == 8 and not apps and max(baseline) <= 16, (gpu, apps)
        available = int(next(line.split()[1] for line in pathlib.Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        assert available >= 1700 * 1024 ** 3, available
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', 8073)) != 0, 'Store port must be closed before start'

        stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
        run_dir = V11 / 'runs' / f"{args.group}-{sequence_id}-r{args.rep}-{stamp}"
        run_dir.mkdir(parents=True)
        deadline_ns = time.monotonic_ns() + 14000 * 10 ** 9
        write(run_dir / 'admission.json', dict(
            group=args.group, sequence=sequence_id, rep=args.rep,
            protocol_hash=protocol_hash, hash_provisional=protocol_doc.get('provisional'),
            gpu=gpu.splitlines(), apps=apps.splitlines(), available_bytes=available,
            gpus=uuids, checked_ns=time.monotonic_ns(), wall_ns=time.time_ns(),
            instruction='V11 campaign: user asked to continue the new baseline on qjhs003'))
        write(run_dir / 'models.json', dict(models=models, sequence=sequence_id))

        b1_inputs = {}
        if args.group == 'B1':
            (run_dir / 'b1-inputs').mkdir()
            for key, name in MODEL_INPUT.items():
                document = json.loads((V7 / 'candidates-v2' / (name + '-input.json')).read_bytes())
                document['config']['v11_kv_transfer_config'] = dict(
                    kv_connector='LMCacheMPConnector',
                    kv_role='kv_both',
                    kv_parallel_size=8,
                    kv_connector_extra_config={
                        'lmcache.mp.server_urls': ['tcp://127.0.0.1:' + str(LMCACHE_MQ_PORT)],
                        'lmcache.mp.host': 'tcp://127.0.0.1',
                        'lmcache.mp.port': LMCACHE_MQ_PORT,
                    })
                document['config']['enable_prefix_caching'] = False
                path = run_dir / 'b1-inputs' / (name + '-input.json')
                path.write_text(json.dumps(document, indent=2) + '\n')
                b1_inputs[key] = path
            write(run_dir / 'b1-inputs.json',
                  {key: str(value) for key, value in b1_inputs.items()})

        holder = {'cid': None, 'phase': 'startup'}
        samples_path = run_dir / 'resource-samples.jsonl'
        sampler = Sampler(samples_path, holder, poll_lmcache=(args.group == 'B1'))
        sampler.start()

        containers = {}
        guards = []
        handles = []
        activations = []
        ledger = Ledger(PATH_SIZES)
        parsed = []
        events_rows = []
        try:
            if args.group == 'B1':
                # Ports must be free: a leftover LMCache server would silently
                # receive this run's KV and invalidate the evidence.
                for port in (LMCACHE_HTTP_PORT, LMCACHE_MQ_PORT):
                    with socket.socket() as probe_port:
                        assert probe_port.connect_ex(('127.0.0.1', port)) != 0, \
                            f'port {port} already in use; refusing to reuse a foreign LMCache server'
                lmc_cid, lmc_args = lmc_server_container(run_dir, uuids)
                containers['lmcache'] = lmc_cid
                write(run_dir / 'lmcache-created.json',
                      dict(id=lmc_cid, command=lmc_args, inspect=row(lmc_cid)))
                lmc_guard, lmc_log = start_guard(lmc_cid, run_dir, deadline_ns, 'lmcache')
                guards.append(lmc_guard)
                handles.append(lmc_log)
                call('docker', 'start', lmc_cid)
                until = time.monotonic() + 600
                while True:
                    probe = http_json('GET', '/status')
                    if probe['status'] == 200:
                        break
                    state = row(lmc_cid)
                    assert state['State']['Running'] or probe['status'] == 0, \
                        'LMCache server exited during startup'
                    assert time.monotonic() < until, 'LMCache HTTP API did not come up'
                    time.sleep(2)
                assert row(lmc_cid)['State']['Running'], 'LMCache server is not running'
                initial = probe['body']
                l1_initial = lmcache_l1(initial)
                tier_gib = (l1_initial.get('memory_total_bytes') or 0) / 2 ** 30
                assert abs(tier_gib - LMCACHE_TIER_GB) < 0.5, \
                    f'expected {LMCACHE_TIER_GB} GiB host tier, got {tier_gib} GiB'
                adapters = (initial.get('storage_manager', {}).get('store_controllers', {})
                            .get('_default', {}).get('num_l2_adapters'))
                assert adapters == 0, f'L2 adapter must not be configured, got {adapters}'
                write(run_dir / 'lmcache-initial-status.json', probe)
            cid, store_args = store_container(run_dir, uuids)
            containers['store'] = cid
            write(run_dir / 'store-created.json', dict(id=cid, command=store_args,
                                                       inspect=row(cid)))
            guard, log = start_guard(cid, run_dir, deadline_ns, 'store')
            guards.append(guard)
            handles.append(log)
            holder['cid'] = cid
            call('docker', 'start', cid)
            store_pid = row(cid)['State']['Pid']
            assert store_pid
            until = time.monotonic() + 1800
            while True:
                with socket.socket() as probe:
                    if probe.connect_ex(('127.0.0.1', 8073)) == 0:
                        break
                assert time.monotonic() < until, 'Store did not listen'
                time.sleep(2)
            holder['phase'] = 'empty-store-idle'
            time.sleep(OBSERVATION_SECONDS)
            delta, state = snapshot_store(cid, parsed, ledger)
            write(run_dir / 'empty-store-idle.json', dict(samples=sampler.rows,
                                                          pool=state,
                                                          events=len(delta)))

            for index, key in enumerate(models):
                name = MODEL_INPUT[key]
                step_dir = run_dir / f'{index:02d}-{key}-{name}'
                step_dir.mkdir()
                (step_dir / 'events').mkdir(exist_ok=True)
                expected = set(MODEL_PATHS[key])
                offset = len(ledger.events)
                before = set(ledger.resident) & expected
                input_doc = json.loads((V7 / 'candidates-v2' / (name + '-input.json')).read_bytes())
                request_names = [item['name'] for item in input_doc['requests']]
                cid_b, backend_args = backend_container(
                    run_dir, step_dir, key, uuids,
                    input_path=b1_inputs.get(key),
                    with_lmcache=(args.group == 'B1'))
                containers[step_dir.name] = cid_b
                write(step_dir / 'created.json', dict(id=cid_b, command=backend_args,
                                                      inspect=row(cid_b)))
                guard_b, log_b = start_guard(cid_b, run_dir, deadline_ns, step_dir.name)
                guards.append(guard_b)
                handles.append(log_b)
                holder['phase'] = step_dir.name + ':activate'
                backend_log = (step_dir / 'backend.log').open('w')
                process = subprocess.Popen(['docker', 'start', '--attach', cid_b],
                                           stdout=backend_log,
                                           stderr=subprocess.STDOUT)
                until = time.monotonic() + 60
                while not (step_dir / 'waiting.json').exists():
                    assert process.poll() is None and time.monotonic() < until
                    time.sleep(.1)
                t2 = time.monotonic_ns()
                (step_dir / 'start-model').touch(exist_ok=False)
                holder['phase'] = step_dir.name + ':loading'
                until = time.monotonic() + 2400
                while not (step_dir / 'ready.json').exists():
                    assert process.poll() is None and time.monotonic() < until, 'no ready.json'
                    time.sleep(.2)
                t3 = time.monotonic_ns()
                first_token_file = step_dir / f'before-{request_names[0]}-first-token.json'
                first_until = time.monotonic() + 1800
                while not first_token_file.exists():
                    assert process.poll() is None and time.monotonic() < first_until, 'no first token'
                    time.sleep(.2)
                first_token = json.loads(first_token_file.read_bytes())
                t3_5 = first_token['first_token_ns']
                checks_until = time.monotonic() + 1800
                while not all((step_dir / f'before-{item}-checks.json').exists()
                              and (step_dir / f'before-{item}-response.json').exists()
                              for item in request_names):
                    assert process.poll() is None and time.monotonic() < checks_until, 'no checks'
                    time.sleep(.2)
                t4 = time.monotonic_ns()
                checks = {}
                for item in request_names:
                    checks[item] = json.loads((step_dir / f'before-{item}-checks.json').read_bytes())
                assert all(value['passed'] for value in checks.values()), {
                    name_: value['checks'] for name_, value in checks.items() if not value['passed']}
                code = process.wait(timeout=1800)
                assert code == 0, code
                t1 = time.monotonic_ns()
                assert (step_dir / 'backend-passed.json').exists()
                assert guard_b.wait(timeout=30) == 0
                final = row(cid_b)
                assert final['State']['ExitCode'] == 0 and not final['State']['Running']
                write(step_dir / 'final.json', final)
                containers.pop(step_dir.name)
                subprocess.run(['docker', 'rm', cid_b], capture_output=True, text=True, timeout=30)
                coverage = rank_coverage(step_dir)
                delta, state = snapshot_store(cid, parsed, ledger)
                cache = ledger.step(offset, expected)
                write(step_dir / 'store-step.json', dict(cache=dict(
                    cache_state=cache['cache_state'], hit_paths=cache['hit_paths'],
                    miss_paths=cache['miss_paths'], hit_bytes=cache['hit_bytes'],
                    file_read_bytes=cache['file_read_bytes'],
                    allocated_blocks=cache['allocated_blocks'],
                    freed_blocks=cache['freed_blocks'],
                    evicted_paths=cache['evicted_paths'], pool_after=cache['pool_after']),
                    events=len(cache['events'])))
                for event in cache['events']:
                    events_rows.append(dict(
                        run=run_dir.name, group=args.group, sequence=sequence_id,
                        activation=index, model=key, kind=event['kind'],
                        path=event.get('path'), hit=event.get('hit'),
                        blocks=event.get('blocks'), free=event.get('free'),
                        bytes=event.get('bytes')))
                kv_row = {}
                if args.group == 'B1':
                    before_clear = http_json('GET', '/status')
                    clear_started = time.monotonic_ns()
                    cleared = http_json('POST', '/cache/clear', {'tier': 'l1'})
                    clear_finished = time.monotonic_ns()
                    after_clear = http_json('GET', '/status')
                    l1_before = lmcache_l1(before_clear['body'])
                    l1_after = lmcache_l1(after_clear['body'])
                    write(step_dir / 'kv-clear.json', dict(
                        before=before_clear, clear=cleared, after=after_clear,
                        seconds=(clear_finished - clear_started) / 1e9))
                    kv_row = dict(
                        kv_used_before_clear=l1_before.get('memory_used_bytes'),
                        kv_used_after_clear=l1_after.get('memory_used_bytes'),
                        kv_sessions_before=before_clear['body'].get('active_sessions'),
                        kv_clear_status=cleared['status'],
                        kv_clear_seconds=round((clear_finished - clear_started) / 1e9, 3))
                activations.append(dict(
                    index=index, model=key, model_name=name,
                    start_ns=t2, exit_ns=t1, t3_ns=t3, t3_5_ns=t3_5, t4_ns=t4,
                    store_pid=store_pid, container_exit=code, checks=checks,
                    rank_coverage=coverage, cache=cache['cache_state'],
                    file_read_bytes=cache['file_read_bytes'], hit_bytes=cache['hit_bytes'],
                    allocated_blocks=cache['allocated_blocks'], freed_blocks=cache['freed_blocks'],
                    evicted_paths=len(cache['evicted_paths']),
                    resident_before=sorted(before),
                    activation_to_correct_seconds=(t4 - t2) / 1e9,
                    first_token_seconds=(t3_5 - t2) / 1e9,
                    **kv_row,
                ))
                holder['phase'] = step_dir.name + ':observation'
                time.sleep(OBSERVATION_SECONDS)
            holder['phase'] = 'teardown'
        except BaseException as error:
            write(run_dir / 'failure.json', dict(error=repr(error),
                                                 traceback=traceback.format_exc()))
            activations and write(run_dir / 'activations-partial.json', activations)
        finally:
            holder['phase'] = 'teardown'
            for name, cid in list(containers.items()):
                try:
                    state = row(cid)
                    if state['State']['Running']:
                        call('docker', 'stop', '--time', '120', cid, timeout=180)
                    log_path = run_dir / ('lmcache' if name == 'lmcache' else name) / (name + '.log')
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_text(call('docker', 'logs', cid, timeout=120))
                    call('docker', 'rm', cid, timeout=60)
                except Exception:
                    pass
            for guard in guards:
                try:
                    guard.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass

            # final release observation
            release_path = run_dir / 'release-samples.jsonl'
            zero_until = time.monotonic() + 300
            while time.monotonic() < zero_until:
                sample = sampler.sample('release-wait')
                if (all(sample.get(f'gpu{index}', -1) == 0 for index in range(8))
                        and sample['compute_apps'] == 0):
                    break
                time.sleep(2)
            begin = time.monotonic_ns()
            release = []
            while time.monotonic_ns() - begin < RELEASE_SECONDS * 10 ** 9:
                release.append(sampler.sample('final-release'))
                with release_path.open('a') as stream:
                    stream.write(json.dumps(release[-1]) + '\n')
                time.sleep(1)
            sampler.stop.set()
            sampler.join(timeout=10)
            released = [row_data for row_data in release]
            all_zero = all(all(row_data.get(f'gpu{index}', -1) == 0 for index in range(8))
                           and row_data['compute_apps'] == 0 for row_data in released)
            release_seconds = (release[-1]['ns'] - release[0]['ns']) / 1e9 if release else 0

            # CSVs
            with (run_dir / 'switch-events.csv').open('w') as stream:
                fields = ['group', 'sequence', 'rep', 'step', 'from_model', 'to_model',
                          't0_ns', 't1_ns', 't2_ns', 't3_ns', 't3_5_ns', 't4_ns',
                          'switch_seconds', 'switch_excluding_observation_seconds',
                          'activation_to_correct_seconds', 'first_token_seconds',
                          'observation_seconds', 'cache_state', 'file_read_bytes', 'hit_bytes',
                          'allocated_blocks', 'freed_blocks', 'evicted_paths',
                          'kv_used_before_clear', 'kv_used_after_clear',
                          'kv_clear_status', 'kv_clear_seconds', 'kv_sessions_before',
                          'container_exit', 'store_pid']
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for index, act in enumerate(activations):
                    previous = activations[index - 1] if index else None
                    t0 = previous['t4_ns'] if previous else None
                    t1 = previous['exit_ns'] if previous else None
                    switch = (act['t4_ns'] - t0) / 1e9 if previous else None
                    writer.writerow(dict(
                        group=args.group, sequence=sequence_id, rep=args.rep,
                        step=index,
                        from_model=previous['model'] if previous else None,
                        to_model=act['model'],
                        t0_ns=t0, t1_ns=t1, t2_ns=act['start_ns'], t3_ns=act['t3_ns'],
                        t3_5_ns=act['t3_5_ns'], t4_ns=act['t4_ns'],
                        switch_seconds=None if switch is None else round(switch, 3),
                        switch_excluding_observation_seconds=(
                            None if switch is None else round(switch - OBSERVATION_SECONDS, 3)),
                        activation_to_correct_seconds=round(act['activation_to_correct_seconds'], 3),
                        first_token_seconds=round(act['first_token_seconds'], 3),
                        observation_seconds=OBSERVATION_SECONDS,
                        cache_state=act['cache'],
                        file_read_bytes=act['file_read_bytes'], hit_bytes=act['hit_bytes'],
                        allocated_blocks=act['allocated_blocks'],
                        freed_blocks=act['freed_blocks'], evicted_paths=act['evicted_paths'],
                        kv_used_before_clear=act.get('kv_used_before_clear'),
                        kv_used_after_clear=act.get('kv_used_after_clear'),
                        kv_clear_status=act.get('kv_clear_status'),
                        kv_clear_seconds=act.get('kv_clear_seconds'),
                        kv_sessions_before=act.get('kv_sessions_before'),
                        container_exit=act['container_exit'], store_pid=act['store_pid']))
            sample_rows = [json.loads(line) for line in samples_path.read_text().splitlines()]
            sample_rows += released
            with (run_dir / 'resource-samples.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(sample_rows[0].keys()))
                writer.writeheader()
                for row_data in sample_rows:
                    writer.writerow(row_data)
            with (run_dir / 'store-ledger.csv').open('w') as stream:
                writer = csv.DictWriter(stream, fieldnames=[
                    'run', 'group', 'sequence', 'activation', 'model', 'kind', 'path',
                    'hit', 'blocks', 'free', 'bytes'])
                writer.writeheader()
                for row_data in events_rows:
                    writer.writerow(row_data)
            failure_present = (run_dir / 'failure.json').exists()
            complete = len(activations) == len(models)
            qualification = dict(
                group=args.group, sequence=sequence_id, rep=args.rep,
                protocol_hash=protocol_hash,
                protocol_hash_provisional=protocol_doc.get('provisional'),
                models=models, activations=len(activations),
                requested_activations=len(models), complete=complete,
                failure_present=failure_present,
                passed=(complete and not failure_present
                        and all(act['container_exit'] == 0 for act in activations)
                        and all(all(check['passed'] for check in act['checks'].values())
                                for act in activations)),
                final_release_seconds=round(release_seconds, 3),
                final_all_gpu_zero=all_zero,
                image=profiles['v4']['image'],
                vllm='0.20.1',
                tensor_parallel=8,
                notes=['path A only; B1 waits for a locked LMCache version',
                       'B2 groups use their own runner'],
            )
            write(run_dir / 'qualification.json', qualification)
            write(run_dir / 'result.json', dict(
                run=run_dir.name, group=args.group, sequence=sequence_id, rep=args.rep,
                protocol_hash=protocol_hash, activations=activations,
                qualification=qualification))
            print(json.dumps(dict(run=run_dir.name, passed=qualification['passed'],
                                  activations=len(activations),
                                  final_release_seconds=qualification['final_release_seconds'],
                                  final_all_gpu_zero=all_zero), indent=2))


if __name__ == '__main__':
    main()
