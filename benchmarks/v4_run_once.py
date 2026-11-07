"""Qualified one-shot controller extended for Store conversion. Default plans only."""
from pathlib import Path
import datetime
import fcntl
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0] = [str(ROOT / 'sllm_store'), str(ROOT / 'benchmarks')]
from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_native_reference import model_inventory
from v4_native_release import collect, cgroup_telemetry

IMAGE = 'sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
MODEL = Path('/mnt/raid/models/DeepSeek-V4-Pro')
HEAD = sys.argv[2]  # The next caller must explicitly pin the reviewed source commit.
base = Path(sys.argv[1]).resolve()
assert base.parent == ROOT.parent / 'evidence' and not base.exists()
MODE = sys.argv[3] if len(sys.argv) > 3 else 'native-reference'
assert MODE in ('native-reference', 'store-conversion', 'store-recovery')
flags = sys.argv[4:]
assert flags in ([], ['--execute'], ['--diagnostic-pool'], ['--execute', '--diagnostic-pool'])
EXECUTE, DIAGNOSTIC = '--execute' in flags, '--diagnostic-pool' in flags
CONVERT = MODE == 'store-conversion'
RECOVER = MODE == 'store-recovery'
STORE = CONVERT or RECOVER
assert not DIAGNOSTIC or RECOVER
overlays = ('v4_convert.py', 'v4_grouped_state.py', 'vllm_v4_loader.py', 'v4_export_worker.py') if STORE else ()
STORAGE = Path('/data/brick1/boyjiang/sllm-v4-pro-store')
qualified = json.loads((ROOT/'docs/evidence/v4-store-data-integrity/qualified-store.json').read_bytes()) if RECOVER else None
store = Path(qualified['store_root']) if RECOVER else STORAGE / base.name
assert store.parent == STORAGE
if CONVERT:
    assert base.name.startswith('store-conversion-') and not store.exists()
if RECOVER:
    assert base.name.startswith('store-recovery-') and store.is_dir()
cache = (Path('/data/brick1/boyjiang/sllm-v4-pro-build/cache-native-reference-20261106T205752Z')
         if STORE else Path('/data/brick1/boyjiang/sllm-v4-pro-build') / ('cache-' + base.name))
if not EXECUTE:
    print(json.dumps(dict(source_head_requested=HEAD, purpose=MODE, image=IMAGE, output=str(base),
        model=str(MODEL), store=str(store) if STORE else None, cache=str(cache),
        converter_overlay=str(ROOT/'sllm_store/sllm_store/v4_convert.py') if STORE else None,
        container_seconds=1080, total_seconds=1200, cpu_limit=8, memory_limit_bytes=1024**4,
        store_byte_stop_limit=1024**4 if STORE else None,
        store_read_only=RECOVER, diagnostic_pool=DIAGNOSTIC,
        storage_free_floor_bytes=128*1024**3 if STORE else None,
        gpu_or_model_started=False, store_claimed=False,
        scope='Plan only; execution requires current stage authorization, fresh admission and GO')))
    raise SystemExit(0)
base.mkdir(mode=0o775)
os.umask(0o002)
origin = time.monotonic_ns()
deadline = origin + 1200_000_000_000
clock = process_identity()
cid, guard, group = None, None, None
workers = {}


def call(*args, timeout=5):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT, timeout=timeout).strip()


def inspected():
    row, = json.loads(call('docker', 'inspect', cid))
    assert row['Id'] == cid and row['Image'] == IMAGE
    return row


def gpu_snapshot():
    raw = call('nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.used', '--format=csv,noheader,nounits')
    apps = call('nvidia-smi', '--query-compute-apps=pid,gpu_uuid,used_memory', '--format=csv,noheader,nounits')
    rows = [[v.strip() for v in line.split(',')] for line in raw.splitlines()]
    return dict(raw=raw, apps=apps, rows=rows, monotonic_ns=time.monotonic_ns())


def store_limits(directory, storage):
    logical = allocated = 0
    for path in directory.rglob('*'):
        path.resolve().relative_to(directory.resolve())
        if path.is_file():
            stat = path.stat()
            logical += stat.st_size
            allocated += stat.st_blocks * 512
    free = shutil.disk_usage(storage).free
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available = int(memory['MemAvailable'].split()[0]) * 1024
    return dict(logical_bytes=logical, allocated_bytes=allocated, storage_free_bytes=free,
                host_available_bytes=available, within_limits=max(logical, allocated) <= 1024**4
                and free >= 128*1024**3 and available >= 64*1024**3)


def capture_group(pid):
    global group
    raw = Path('/proc', str(pid), 'cgroup').read_text()
    relative, = [line.split(':', 2)[2] for line in raw.splitlines() if line.startswith('0::')]
    candidate = Path('/sys/fs/cgroup') / relative.lstrip('/')
    assert cid in str(candidate) and '..' not in candidate.parts
    group = candidate


def sample_processes():
    rows = []
    if group and group.exists():
        for file in group.rglob('cgroup.procs'):
            for value in file.read_text().split():
                try:
                    stat = Path('/proc', value, 'stat').read_text()
                except FileNotFoundError:
                    continue
                start = int(stat.rsplit(')', 1)[1].split()[19])
                worker = dict(pid=int(value), start_ticks=start)
                workers[(int(value), start)] = worker
                rows.append(worker)
    return rows


try:
    lease = (ROOT.parent / 'native-run.lock').open('a')
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert call('git', '-C', str(ROOT), 'rev-parse', 'HEAD') == HEAD
    assert not call('git', '-C', str(ROOT), 'status', '--porcelain')
    initial_inventory = model_inventory(MODEL)
    old = json.loads((ROOT / 'docs/evidence/v4-native-entry-cpu/plan.json').read_bytes())['inventory']
    assert initial_inventory == old, 'Model file identity differs from frozen post-SHA plan'
    assets = json.loads((ROOT / 'docs/evidence/v4-full-weight-sha-p3/post-weight-assets.json').read_bytes())
    asset_states = {}
    for asset in assets['files']:
        if asset['runtime_input']:
            stat = (MODEL / asset['file']).stat()
            assert all(getattr(stat, key) == value for key, value in asset['file_state'].items())
            asset_states[asset['file']] = asset['file_state']
    initial = gpu_snapshot()
    assert len(initial['rows']) == 8 and not initial['apps']
    assert all('H20' in row[2] and int(row[3]) >= 140000 and int(row[4]) == 0 for row in initial['rows'])
    uuids = [row[1] for row in initial['rows']]
    assert len(set(uuids)) == 8
    indices = [int(row[0]) for row in initial['rows']]
    assert indices == list(range(8)), 'This native profile requires all eight physical ordinals'
    baseline = {row[1]: int(row[4]) for row in initial['rows']}
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(memory['MemAvailable'].split()[0]) * 1024 >= 1024**4 + 64 * 1024**3
    image, = json.loads(call('docker', 'image', 'inspect', IMAGE))
    assert image['Id'] == IMAGE
    output = base / 'output'
    output.mkdir(mode=0o775)
    conversion_identity = None
    if STORE:
        assert not STORAGE.is_symlink()
        assert call('findmnt', '--target', str(STORAGE.parent), '--noheadings', '--output', 'FSTYPE') == 'xfs'
        assert shutil.disk_usage(STORAGE.parent).free >= (1024**4 if CONVERT else 0) + 128*1024**3
        STORAGE.mkdir(mode=0o775, exist_ok=True)
        if CONVERT:
            assert not store.exists()
        else:
            assert hashlib.sha256((store/'COMPLETE.json').read_bytes()).hexdigest() == qualified['complete_sha256']
            assert qualified['store_data_integrity_qualified']
        manifest = json.loads((ROOT/'docs/evidence/v4-native-reference-success/native-jit-artifacts.json').read_bytes())
        assert manifest['cache_root'] == str(cache)
        for artifact in manifest['files']:
            path = cache / artifact['path']
            path.resolve().relative_to(cache.resolve())
            assert path.stat().st_size == artifact['bytes']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['sha256']
        conversion_identity = qualified['identity'] if RECOVER else json.loads((ROOT/'docs/evidence/v4-store-next-preparation/identity.json').read_bytes())
        if CONVERT:
            conversion_identity['conversion_id'] = base.name
        else:
            shutil.copyfile(ROOT/'docs/evidence/v4-store-data-integrity/pool-capacity-candidate.json', base/'pool-config.json')
        write_once(base/'conversion-identity.json', conversion_identity)
    else:
        cache.mkdir(mode=0o775)
    for name in ('home', 'hf', 'cuda', 'triton', 'pycache', 'xdg', 'torchinductor', 'torch_extensions'):
        (cache / name).mkdir(mode=0o775, exist_ok=STORE)
    profile = ROOT / 'docs/evidence/v4-runtime-user-failure/cpu-profile'
    profile_sha = {}
    for name in ('passwd', 'group'):
        data = (profile / name).read_bytes()
        assert any(line.startswith('boyjiang:x:2147:') for line in data.decode().splitlines())
        shutil.copy2(profile / name, base / ('run-user.' + name))
        profile_sha[name] = hashlib.sha256(data).hexdigest()
    wrapper = '''from pathlib import Path
import importlib.metadata as md,json,os,runpy,sys,tomllib
import torch
from sllm_store.v4_state import write_once
assert torch.cuda.is_available() and torch.cuda.device_count()==8
devices=[dict(index=i,name=torch.cuda.get_device_name(i),capability=torch.cuda.get_device_capability(i),total_memory=torch.cuda.get_device_properties(i).total_memory) for i in range(8)]
assert all('H20' in r['name'] and r['capability']==(9,0) for r in devices)
from vllm.platforms.cuda import CudaPlatform
raw_uuids=[CudaPlatform.get_device_uuid(i) for i in range(8)]
mapped_uuids=[value.decode('ascii') if isinstance(value,bytes) else value for value in raw_uuids]
expected_uuids=[row[1] for row in json.loads(Path('/control/preparation.json').read_bytes())['gpu_initial']['rows']]
write_once('/output/device-map-precheck.json',dict(raw_uuid_repr=[repr(value) for value in raw_uuids],mapped_uuids=mapped_uuids,expected_uuids=expected_uuids,nvidia_visible_devices_observed=os.environ.get('NVIDIA_VISIBLE_DEVICES')))
assert mapped_uuids==expected_uuids
maps=Path('/proc/self/maps').read_text()
cuda_maps=[line for line in maps.splitlines() if 'libcuda.so' in line]
assert cuda_maps and not any('/compat/' in line or '/stubs/' in line for line in cuda_maps)
lock=tomllib.loads(Path('/workspace/docs/evidence/v4-runtime-contract-cpu/final/pylock.v4-final.toml').read_text())
normalize=lambda n:n.lower().replace('_','-').replace('.','-')
installed={normalize(d.metadata['Name']):d.version for d in md.distributions()}
assert installed=={p['name']:p['version'] for p in lock['packages']}
write_once('/output/gpu-precheck.json',dict(devices=devices,libcuda_maps=cuda_maps,installed_packages=installed,cuda_initialized=torch.cuda.is_initialized(),cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'],vllm_mapped_gpu_uuids=mapped_uuids))
print('GPU_PRECHECK_OK',flush=True)
'''
    if STORE:
        wrapper += "import hashlib\nexpected=json.loads(Path('/control/preparation.json').read_bytes())['source_overrides']\nfor name,digest in expected.items():\n assert hashlib.sha256((Path('/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')/name).read_bytes()).hexdigest()==digest\n"
        entry = 'v4_store_recovery.py' if RECOVER else 'v4_store_conversion.py'
        argv = [entry, '--execute', '--model', str(MODEL), '--store', str(store),
                '--identity', '/control/conversion-identity.json',
                '--guard', '/control/guard/armed.json', '--output', '/output/recovery' if RECOVER else '/output/conversion']
        if RECOVER:
            argv += ['--pool-config', '/control/pool-config.json']
            if DIAGNOSTIC:
                argv += ['--diagnostic-pool']
    else:
        entry = 'v4_native_reference.py'
        argv = [entry, '--execute', '--guard', '/control/guard/armed.json',
                '--output', '/output/native']
    wrapper += 'sys.argv=' + repr(argv) + "\nrunpy.run_path(" + repr('/workspace/benchmarks/' + entry) + ",run_name='__main__')\n"
    (base / 'gpu_entry.py').write_text(wrapper)
    command = ['docker', 'create', '--name', base.name, '--runtime', 'nvidia',
        '--gpus', '"device=' + ','.join(uuids) + '"', '--network', 'none', '--restart', 'no',
        '--cpus', '8', '--memory', str(1024**4), '--memory-swap', str(1024**4),
        '--pids-limit', '1024', '--shm-size', '16g', '--ipc', 'private', '--init',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
        '--ulimit', 'memlock=-1:-1', '--user', '2147:2147', '--workdir', '/workspace',
        '--entrypoint', '/opt/sllm-v4/bin/python',
        '--label', 'sllm.v4.guard=' + MODE, '--label', 'sllm.run_id=' + base.name,
        '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777',
        '--mount', 'type=bind,src=' + str(ROOT) + ',dst=/workspace,readonly',
        '--mount', 'type=bind,src=' + str(MODEL) + ',dst=' + str(MODEL) + ',readonly',
        '--mount', 'type=bind,src=' + str(base) + ',dst=/control,readonly',
        '--mount', 'type=bind,src=' + str(output) + ',dst=/output',
        '--mount', 'type=bind,src=' + str(cache) + ',dst=/runtime-cache']
    if STORE:
        command += ['--mount', 'type=bind,src=' + str(STORAGE) + ',dst=' + str(STORAGE) + (',readonly' if RECOVER else '')]
        for name in overlays:
            command += ['--mount', 'type=bind,src=' + str(ROOT/'sllm_store/sllm_store'/name) +
                ',dst=/opt/sllm-v4/lib/python3.12/site-packages/sllm_store/' + name + ',readonly']
    for name in ('passwd', 'group'):
        command += ['--mount', 'type=bind,src=' + str(base / ('run-user.' + name)) + ',dst=/etc/' + name + ',readonly']
    env = dict(CUDA_VISIBLE_DEVICES=','.join(map(str,indices)), NVIDIA_VISIBLE_DEVICES=','.join(uuids),
        VLLM_PLUGINS='sllm_v4_store' if STORE else '', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        PYTHONPATH='/workspace/benchmarks', HOME='/runtime-cache/home', HF_HOME='/runtime-cache/hf',
        XDG_CACHE_HOME='/runtime-cache/xdg', TRITON_CACHE_DIR='/runtime-cache/triton',
        CUDA_CACHE_PATH='/runtime-cache/cuda', PYTHONPYCACHEPREFIX='/runtime-cache/pycache',
        USER='boyjiang', LOGNAME='boyjiang',
        TORCHINDUCTOR_CACHE_DIR='/runtime-cache/torchinductor', TORCH_EXTENSIONS_DIR='/runtime-cache/torch_extensions',
        LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64',
        OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONUNBUFFERED='1')
    if STORE:
        env.update(STORAGE_PATH=str(STORAGE), SLLM_EVENT_DIR='/output/events', SLLM_EVENT_RUN_ID=base.name)
    for key, value in env.items():
        command += ['--env', key + '=' + value]
    command += [IMAGE, '-u', '/control/gpu_entry.py']
    cid = call(*command, timeout=20)
    created = inspected()
    assert created['State']['Status'] == 'created' and not created['HostConfig']['AutoRemove']
    assert created['HostConfig']['DeviceRequests'][0]['DeviceIDs'] == uuids
    identity = dict(container_id=cid, created=created['Created'], image=IMAGE)
    write_once(base / 'preparation.json', dict(identity=identity, controller=clock,
        preparation_begin_ns=origin, deadline_ns=deadline, container_deadline_ns=origin+1080_000_000_000,
        gpu_initial=initial, baseline_gpu_mib=baseline, weight_inventory=initial_inventory,
        runtime_asset_metadata=asset_states, create_command=command,
        runtime_user='boyjiang:2147:2147', runtime_user_profile_sha256=profile_sha,
        source_head=HEAD, purpose=MODE, diagnostic_pool=DIAGNOSTIC, conversion_identity=conversion_identity,
        source_overrides={name: hashlib.sha256((ROOT/'sllm_store/sllm_store'/name).read_bytes()).hexdigest() for name in overlays},
        authorizing_instruction='Stage authorization must be recorded in GO; host use: User explicitly 我说过了 你直接用吧',
        reservation_use_basis='Direct user instruction after disclosed conflict; no existing booking changed',
        hardware_admission=True, model_run_started=False, writer_scope='Own process read-only mount plus metadata seal; no claim of global NFS writer lease'))
    host_env = dict(os.environ, PYTHONPATH=str(ROOT/'sllm_store')+':'+str(ROOT/'benchmarks'))
    with (base / 'guard.log').open('x') as log:
        guard = subprocess.Popen(['python3', str(ROOT/'benchmarks/v4_container_guard.py'), cid,
            str(origin+1080_000_000_000), str(base/'guard'), '--purpose', MODE], env=host_env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        if (base/'guard/armed.json').exists():
            break
        assert guard.poll() is None
        time.sleep(.05)
    armed = json.loads((base/'guard/armed.json').read_bytes())
    assert armed['identity'] == identity and armed['guard']['pid'] == guard.pid
    write_once(base/'READY.json', dict(identity=identity, controller=clock, guard=armed,
                                      ready_ns=time.monotonic_ns()))
    # Human notification happens before the control plane publishes GO.json.
    while not (base/'GO.json').exists():
        assert guard.poll() is None and time.monotonic_ns() < origin+120_000_000_000
        time.sleep(.2)
    go = json.loads((base/'GO.json').read_bytes())
    assert (go.get('purpose') == MODE and go.get('source_head') == HEAD
            and isinstance(go.get('authorization'), str) and go['authorization'].strip()
            and go.get('notified_before_gpu') is True), 'GO must bind current stage/source authorization and notification'
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(memory['MemAvailable'].split()[0]) * 1024 >= 1024**4 + 64*1024**3
    if STORE:
        assert shutil.disk_usage(STORAGE).free >= (1024**4 if CONVERT else 0) + 128*1024**3
        if CONVERT:
            assert not store.exists()
        else:
            assert hashlib.sha256((store/'COMPLETE.json').read_bytes()).hexdigest() == qualified['complete_sha256']
    recheck = gpu_snapshot()
    assert not recheck['apps'] and all(int(row[4]) == baseline[row[1]] for row in recheck['rows'])
    write_once(base/'launch.json', dict(launch_ns=time.monotonic_ns(), gpu_admission=recheck,
                                      model_attempt_consumed=True, authorization=go))
    call('docker', 'start', cid, timeout=20)
    inspection_timeouts = 0
    with (base/'telemetry.jsonl').open('x') as stream:
        while True:
            try:
                state = inspected()['State']
            except subprocess.TimeoutExpired as observation_error:
                inspection_timeouts += 1
                write_once(base/f'inspect-timeout-{inspection_timeouts}.json',
                           dict(error=repr(observation_error), observed_ns=time.monotonic_ns()))
                assert time.monotonic_ns() < origin+1100_000_000_000
                continue
            if state['Pid'] and group is None:
                capture_group(state['Pid'])
            processes = sample_processes()
            sample = dict(monotonic_ns=time.monotonic_ns(), state=state, workers=processes)
            try:
                sample['gpu'] = gpu_snapshot()
            except subprocess.TimeoutExpired as observation_error:
                sample.update(gpu=None, gpu_observation_error=repr(observation_error))
            if group and group.exists():
                sample['cgroup'] = cgroup_telemetry(group)
            if STORE:
                sample['store_limits'] = store_limits(store, STORAGE)
            stream.write(json.dumps(sample)+'\n')
            stream.flush()
            if STORE:
                assert sample['store_limits']['within_limits'], 'Store storage or host memory stop threshold crossed'
            if not state['Running']:
                break
            assert guard.poll() is None, 'Guard exited while target still running'
            assert time.monotonic_ns() < origin+1100_000_000_000
            time.sleep(2)
    final = inspected()
    write_once(base/'container-final.json', final)
    write_once(base/'container-logs.json', dict(text=call('docker','logs',cid,timeout=10)))
    assert final['State']['Pid'] == 0 and not final['State']['Running']
    assert group and workers, 'Missing pre-exit process/cgroup identity'
    job = dict(identity=identity, baseline_gpu_mib=baseline, cgroup=str(group),
        worker_processes=list(workers.values()), boot_id=clock['boot_id'],time_namespace=clock['time_namespace'],
        preparation_begin_ns=origin, delete_ack_ns=time.monotonic_ns(), deadline_ns=deadline)
    write_once(base/'release-job.json', job)
    collect(job, base/'release')
    unchanged = model_inventory(MODEL) == initial_inventory
    guard_code = guard.wait(timeout=5)
    result = dict(container_exit_code=final['State']['ExitCode'], guard_exit_code=guard_code,
        natural_process_exit=guard_code == 0, weight_metadata_unchanged=unchanged,
        **{('store_recovery_candidate_exists' if RECOVER else 'store_conversion_candidate_exists' if CONVERT else 'native_candidate_exists'):
               (output/('recovery' if RECOVER else 'conversion' if CONVERT else 'native')/'candidate.json').exists()},
        release_observation_passed=True, runtime_qualified=False,
        total_elapsed_seconds=(time.monotonic_ns()-origin)/1e9,
        no_retry=True, formal_id_consumed=False)
    result['status'] = ('candidate_pending_review' if final['State']['ExitCode'] == 0
        and guard_code == 0 and unchanged and result['store_recovery_candidate_exists' if RECOVER else 'store_conversion_candidate_exists' if CONVERT else 'native_candidate_exists']
        else ('failed_store_recovery_process' if RECOVER else 'failed_store_conversion_process' if CONVERT else 'failed_native_process'))
    write_once(base/'result.json', result)
    print(json.dumps(result), flush=True)
except BaseException as error:
    failure = dict(error=repr(error), error_message=str(error), traceback=traceback.format_exc(),
                   failed_ns=time.monotonic_ns(), container_id=cid,
                   model_attempt_consumed=(base/'launch.json').exists(), no_retry=True)
    if cid:
        try:
            row = inspected()
            if row['State']['Running']:
                call('docker','kill','--signal','KILL',cid)
            failure['final_inspect'] = inspected()
            failure['logs'] = call('docker','logs',cid,timeout=10)
        except BaseException as cleanup_error:
            failure['cleanup_error'] = repr(cleanup_error)
        # Even a failed initialization gets full post-exit observation when identifiable.
        if group and workers and not (base/'release').exists():
            try:
                job = dict(identity=identity, baseline_gpu_mib=baseline,cgroup=str(group),
                    worker_processes=list(workers.values()),boot_id=clock['boot_id'],time_namespace=clock['time_namespace'],
                    preparation_begin_ns=origin,delete_ack_ns=time.monotonic_ns(),deadline_ns=deadline)
                collect(job,base/'release')
                failure['release_observation_passed'] = True
            except BaseException as release_error:
                failure['release_error'] = repr(release_error)
        if (base/'launch.json').exists() and not failure.get('release_observation_passed'):
            # Preserve a full 60s observation even when lineage/first sample is incomplete.
            observed = []
            try:
                with (base/'failure-release60.jsonl').open('x') as stream:
                    for second in range(61):
                        assert time.monotonic_ns() < deadline
                        sample = dict(second=second, monotonic_ns=time.monotonic_ns(),
                                      gpu=gpu_snapshot(), container_state=inspected()['State'])
                        stream.write(json.dumps(sample)+'\n')
                        stream.flush()
                        observed.append(sample)
                        if second < 60:
                            time.sleep(1)
                failure['failure_observation_span_seconds'] = (observed[-1]['monotonic_ns']-observed[0]['monotonic_ns'])/1e9
                failure['failure_observation_scope'] = 'Raw temporal/GPU/container evidence only; incomplete lineage never becomes cleanup PASS'
            except BaseException as observe_error:
                failure['failure_observation_error'] = repr(observe_error)
    write_once(base/'failure.json',failure)
    print(json.dumps(failure),flush=True)
    raise
