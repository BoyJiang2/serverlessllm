"""One authorized native run. No retries, installs, downloads, Store or formal IDs."""
from pathlib import Path
import datetime
import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

ROOT = Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0] = [str(ROOT / 'sllm_store'), str(ROOT / 'benchmarks')]
from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_native_reference import model_inventory
from v4_native_release import collect

IMAGE = 'sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
MODEL = Path('/mnt/raid/models/DeepSeek-V4-Pro')
HEAD = sys.argv[2]  # The next caller must explicitly pin the reviewed source commit.
base = Path(sys.argv[1]).resolve()
assert base.parent == ROOT.parent / 'evidence' and not base.exists()
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
    baseline = {row[1]: int(row[4]) for row in initial['rows']}
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    assert int(memory['MemAvailable'].split()[0]) * 1024 >= 1024**4 + 64 * 1024**3
    image, = json.loads(call('docker', 'image', 'inspect', IMAGE))
    assert image['Id'] == IMAGE
    output = base / 'output'
    output.mkdir(mode=0o775)
    cache = Path('/data/brick1/boyjiang/sllm-v4-pro-build') / ('cache-' + base.name)
    cache.mkdir(mode=0o775)
    for name in ('home', 'hf', 'cuda', 'triton', 'pycache', 'xdg'):
        (cache / name).mkdir(mode=0o775)
    wrapper = '''from pathlib import Path
import importlib.metadata as md,json,os,runpy,sys,tomllib
import torch
from sllm_store.v4_state import write_once
assert torch.cuda.is_available() and torch.cuda.device_count()==8
devices=[dict(index=i,name=torch.cuda.get_device_name(i),capability=torch.cuda.get_device_capability(i),total_memory=torch.cuda.get_device_properties(i).total_memory) for i in range(8)]
assert all('H20' in r['name'] and r['capability']==(9,0) for r in devices)
maps=Path('/proc/self/maps').read_text()
cuda_maps=[line for line in maps.splitlines() if 'libcuda.so' in line]
assert cuda_maps and not any('/compat/' in line or '/stubs/' in line for line in cuda_maps)
lock=tomllib.loads(Path('/workspace/docs/evidence/v4-runtime-contract-cpu/final/pylock.v4-final.toml').read_text())
normalize=lambda n:n.lower().replace('_','-').replace('.','-')
installed={normalize(d.metadata['Name']):d.version for d in md.distributions()}
assert installed=={p['name']:p['version'] for p in lock['packages']}
write_once('/output/gpu-precheck.json',dict(devices=devices,libcuda_maps=cuda_maps,installed_packages=installed,cuda_initialized=torch.cuda.is_initialized()))
print('GPU_PRECHECK_OK',flush=True)
sys.argv=['v4_native_reference.py','--execute','--guard','/control/guard/armed.json','--output','/output/native']
runpy.run_path('/workspace/benchmarks/v4_native_reference.py',run_name='__main__')
'''
    (base / 'gpu_entry.py').write_text(wrapper)
    command = ['docker', 'create', '--name', base.name, '--runtime', 'nvidia',
        '--gpus', '"device=' + ','.join(uuids) + '"', '--network', 'none', '--restart', 'no',
        '--cpus', '8', '--memory', str(1024**4), '--memory-swap', str(1024**4),
        '--pids-limit', '1024', '--shm-size', '16g', '--ipc', 'private', '--init',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--read-only',
        '--ulimit', 'memlock=-1:-1', '--user', '2147:2147', '--workdir', '/workspace',
        '--entrypoint', '/opt/sllm-v4/bin/python',
        '--label', 'sllm.v4.guard=native-reference', '--label', 'sllm.run_id=' + base.name,
        '--tmpfs', '/tmp:rw,exec,nosuid,size=16g,mode=1777',
        '--mount', 'type=bind,src=' + str(ROOT) + ',dst=/workspace,readonly',
        '--mount', 'type=bind,src=' + str(MODEL) + ',dst=' + str(MODEL) + ',readonly',
        '--mount', 'type=bind,src=' + str(base) + ',dst=/control,readonly',
        '--mount', 'type=bind,src=' + str(output) + ',dst=/output',
        '--mount', 'type=bind,src=' + str(cache) + ',dst=/runtime-cache']
    env = dict(CUDA_VISIBLE_DEVICES=','.join(uuids), NVIDIA_VISIBLE_DEVICES=','.join(uuids),
        VLLM_PLUGINS='', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
        PYTHONPATH='/workspace/benchmarks', HOME='/runtime-cache/home', HF_HOME='/runtime-cache/hf',
        XDG_CACHE_HOME='/runtime-cache/xdg', TRITON_CACHE_DIR='/runtime-cache/triton',
        CUDA_CACHE_PATH='/runtime-cache/cuda', PYTHONPYCACHEPREFIX='/runtime-cache/pycache',
        LD_LIBRARY_PATH='/usr/local/nvidia/lib:/usr/local/nvidia/lib64:/usr/local/cuda/lib64',
        OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONUNBUFFERED='1')
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
        source_head=HEAD, authorizing_instruction='User explicitly: 我说过了 你直接用吧',
        reservation_use_basis='Direct user instruction after disclosed conflict; no existing booking changed',
        hardware_admission=True, model_run_started=False, writer_scope='Own process read-only mount plus metadata seal; no claim of global NFS writer lease'))
    host_env = dict(os.environ, PYTHONPATH=str(ROOT/'sllm_store')+':'+str(ROOT/'benchmarks'))
    with (base / 'guard.log').open('x') as log:
        guard = subprocess.Popen(['python3', str(ROOT/'benchmarks/v4_container_guard.py'), cid,
            str(origin+1080_000_000_000), str(base/'guard')], env=host_env,
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
    recheck = gpu_snapshot()
    assert not recheck['apps'] and all(int(row[4]) == baseline[row[1]] for row in recheck['rows'])
    write_once(base/'launch.json', dict(launch_ns=time.monotonic_ns(), gpu_admission=recheck,
                                      model_attempt_consumed=True))
    call('docker', 'start', cid, timeout=20)
    with (base/'telemetry.jsonl').open('x') as stream:
        while True:
            state = inspected()['State']
            if state['Pid'] and group is None:
                capture_group(state['Pid'])
            processes = sample_processes()
            sample = dict(monotonic_ns=time.monotonic_ns(), state=state, workers=processes,
                          gpu=gpu_snapshot())
            if group and group.exists():
                sample['cgroup'] = {n:(group/n).read_text() for n in ('memory.current','memory.peak','memory.events','pids.current')}
            stream.write(json.dumps(sample)+'\n')
            stream.flush()
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
        native_candidate_exists=(output/'native/candidate.json').exists(),
        release_observation_passed=True, runtime_qualified=False,
        total_elapsed_seconds=(time.monotonic_ns()-origin)/1e9,
        no_retry=True, formal_id_consumed=False)
    write_once(base/'result.json', result)
    print(json.dumps(result), flush=True)
except BaseException as error:
    failure = dict(error=repr(error), failed_ns=time.monotonic_ns(), container_id=cid,
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
