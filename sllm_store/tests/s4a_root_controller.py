"""S4a root integration draft; no execution authorization."""
import argparse, hashlib, json, math, os, pathlib, re, subprocess, time

ROOT = pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
BASE = pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/s4a-readback-revised-20261104')
UUID = 'GPU-736d7f37-f647-b82d-ab11-693753ac6822'
GUARD = ROOT/'sllm_store/tests/guard_cuda_micro.py'
DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
NVIDIA_SMI = '/usr/bin/nvidia-smi'
CALL_TIMEOUT = 3
GPU_QUERY_TIMEOUT = 15
MAX_CONSECUTIVE_GPU_QUERY_FAILURES = 3
SAMPLE_INTERVAL_SECONDS = .5

def call(argv, timeout=CALL_TIMEOUT):
    return subprocess.check_output(argv, text=True, timeout=timeout)

def save(name, row, mode=0o600, base=None):
    base = BASE if base is None else base
    path = base/name
    assert not path.exists()
    temporary = base/(name+'.tmp')
    fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, mode)
    os.fchmod(fd, mode)
    with os.fdopen(fd, 'w') as stream:
        json.dump(row, stream); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(base, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def unitprops(unit):
    return dict(line.split('=',1) for line in call(['/usr/bin/systemctl','show',unit,
        '--property=MainPID,ExecMainCode,ExecMainStatus,Result,ActiveState,ActiveEnterTimestampMonotonic,RuntimeMaxUSec,TimeoutStopUSec']).splitlines())

def healthy(unit, finalname):
    row = unitprops(unit)
    full_start = json.loads((BASE/'prepared.json').read_text())['preparation_started']
    assert row['RuntimeMaxUSec'] == '2min 20s' and row['TimeoutStopUSec'] == '3s'
    assert int(row['ActiveEnterTimestampMonotonic']) > 0
    assert int(row['ActiveEnterTimestampMonotonic'])/1e6 + 143 <= full_start + 178
    if int(row['MainPID']) == 0:
        assert (BASE/finalname).exists() and row['ExecMainCode'] == '1' and row['ExecMainStatus'] == '0' and row['Result'] == 'success'
    else:
        assert row['ActiveState'] == 'active'
    return row

def gpu():
    return gpu_states([UUID])[UUID]

def service(unit, argv, remain=False):
    full_start = json.loads((BASE/'prepared.json').read_text())['preparation_started']
    assert time.monotonic() - full_start <= 32, 'independent service deadline exceeds full budget'
    call(['/usr/bin/systemd-run', '--unit='+unit, '--property=Type=exec',
          '--property=RuntimeMaxSec=140', '--property=TimeoutStopSec=3',
          '--property=Restart=no', '--property=KillMode=control-group',
          '--property=SendSIGKILL=yes', '--property=FinalKillSignal=SIGKILL',
          '--property=MemoryMax=256M', '--property=TasksMax=64',
          *(['--property=RemainAfterExit=yes'] if remain else []),
          '--property=StandardOutput=append:'+str(BASE/(unit+'.stdout')),
          '--property=StandardError=append:'+str(BASE/(unit+'.stderr')), *argv])

def gpu_states(uuids, observe_memory=None):
    assert len(uuids) in (1, 8) and len(set(uuids)) == len(uuids)
    assert all(re.fullmatch(r'GPU-[0-9a-fA-F-]{36}', uuid) for uuid in uuids)
    # Query only the memory and process fields needed for release observation.
    try:
        memory = call([NVIDIA_SMI, '--id='+','.join(uuids), '--query-gpu=uuid,memory.used',
                       '--format=csv,noheader,nounits'], timeout=GPU_QUERY_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError('GPU memory query failed: '+str(error)) from error
    used = {}
    for line in memory.splitlines():
        if line.strip():
            uuid, mib = [field.strip() for field in line.split(',')]
            assert uuid not in used, 'duplicate GPU memory row'
            used[uuid] = int(mib)
            assert used[uuid] >= 0, 'negative GPU memory'
    assert set(used) == set(uuids), 'GPU inventory differs from the requested set'
    if observe_memory is not None:
        observe_memory(used)
    try:
        apps = call([NVIDIA_SMI, '--query-compute-apps=gpu_uuid,pid',
                     '--format=csv,noheader'], timeout=GPU_QUERY_TIMEOUT)
    except (subprocess.SubprocessError, OSError) as error:
        raise RuntimeError('GPU process query failed: '+str(error)) from error
    processes = {}
    for line in apps.splitlines():
        if line.strip():
            uuid, pid = [field.strip() for field in line.split(',')]
            assert pid.isdecimal() and int(pid) > 0, 'invalid GPU process PID'
            processes.setdefault(uuid, []).append(pid)
    return {uuid: {'used_mib': used[uuid], 'processes': processes.get(uuid, [])} for uuid in uuids}


def observer(evidence=None, uuids=None, seconds=135, limit_mib=2048):
    base = BASE if evidence is None else pathlib.Path(evidence)
    uuids = [UUID] if uuids is None else uuids
    assert base.is_absolute() and base.is_dir() and not base.is_symlink()
    assert math.isfinite(seconds) and seconds > 60 and limit_mib > 0
    start = time.monotonic(); quiet = None; peaks = {uuid: 0 for uuid in uuids}
    zero = {'used_mib': 0, 'processes': []}
    consecutive = 0; samples = 0; gaps = 0; memory_samples = 0
    max_query_seconds = 0.0; max_memory_interval = 0.0; last_memory = None
    try:
        states = gpu_states(uuids)
        assert all(row == zero for row in states.values())
        last_memory = time.monotonic()
        identity = {'gpu_uuid': uuids[0]} if len(uuids) == 1 else {'gpu_uuids': uuids}
        save('observer-ready.json', {'pid': os.getpid(), **identity}, base=base)
        with (base/'gpu-observer.jsonl').open('x') as stream:
            def record_memory(used):
                nonlocal memory_samples, max_memory_interval, last_memory
                now = time.monotonic()
                if last_memory is not None:
                    max_memory_interval = max(max_memory_interval, now-last_memory)
                last_memory = now; memory_samples += 1
                for uuid, mib in used.items(): peaks[uuid] = max(peaks[uuid], mib)
                stream.write(json.dumps({'mono': now, 'memory_mib_by_uuid': used})+'\n'); stream.flush()
                assert max(used.values()) <= limit_mib, 'observed GPU memory exceeds limit'
            while time.monotonic()-start < seconds:
                began = time.monotonic()
                try:
                    states = gpu_states(uuids, record_memory)
                except RuntimeError as error:
                    # A failed query leaves a gap and restarts the quiet window.
                    consecutive += 1; gaps += 1; quiet = None
                    max_query_seconds = max(max_query_seconds, time.monotonic()-began)
                    stream.write(json.dumps({'mono': time.monotonic(), 'sample_error':
                        {'error': str(error), 'seconds': time.monotonic()-began,
                         'consecutive': consecutive}})+'\n'); stream.flush()
                    assert consecutive < MAX_CONSECUTIVE_GPU_QUERY_FAILURES, 'observer monitor blackout'
                    time.sleep(SAMPLE_INTERVAL_SECONDS); continue
                max_query_seconds = max(max_query_seconds, time.monotonic()-began)
                assert time.monotonic()-start < seconds, 'observer query exceeded deadline'
                consecutive = 0; samples += 1
                row = states[uuids[0]] if len(uuids) == 1 else {'gpus': states}
                stream.write(json.dumps({'mono': time.monotonic(), **row})+'\n'); stream.flush()
                if (base/'normal-exit.json').exists() or (base/'ABORT').exists():
                    if any(row != zero for row in states.values()):
                        quiet = None; time.sleep(SAMPLE_INTERVAL_SECONDS); continue
                    quiet = quiet or time.monotonic()
                    if time.monotonic()-quiet >= 60:
                        peak = {'peak_mib': peaks[uuids[0]]} if len(uuids) == 1 else {'peak_mib_by_uuid': peaks}
                        monitor = {'samples': samples, 'memory_samples': memory_samples, 'gaps': gaps,
                                   'max_query_seconds': max_query_seconds,
                                   'max_memory_interval_seconds': max_memory_interval,
                                   'peak_kind': 'SAMPLED_LOWER_BOUND',
                                   'peak_coverage': 'SAMPLED_WITH_QUERY_GAPS' if gaps else 'SAMPLED_NO_QUERY_ERRORS'}
                        save('observer-final.json', {**identity, 'quiet_seconds': time.monotonic()-quiet,
                             **peak, 'monitor': monitor}, base=base)
                        stream.write(json.dumps({'mono': time.monotonic(), 'monitor_summary': monitor})+'\n'); stream.flush()
                        return
                time.sleep(SAMPLE_INTERVAL_SECONDS)
        raise TimeoutError('observer deadline')
    except BaseException:
        (base/'ABORT').touch(exist_ok=True)
        raise



RUN = 's4a-readback-revised-20261104'
IMAGE = 'sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27'
STORE = BASE.parent/'s4a-saveonly-20261103-2103'
RECEIPT = BASE.parent/'s4a-gold-save-only-receipt.json'
# Root must supply the reviewed exact source14 + runner/component + SDK ELF + gold hashes.
PINFILE = ROOT/'sllm_store/tests/s4a-readback-input-pins.json'

def verify_inputs():
    assert call(['hostname']).strip() == 'qjhs2'
    pins = json.loads(PINFILE.read_text())
    assert pins['source_commit'] == 'adaeff41b060227bcdd37b4dd1b0a7961523425e'
    required = [ROOT/name for name in ['sllm_store/README.md', 'sllm_store/sllm_store/server.py', 'sllm_store/sllm_store/torch.py', 'sllm_store/sllm_store/cli.py', 'sllm_store/csrc/sllm_store/pinned_memory_pool.cpp', 'sllm_store/csrc/sllm_store/cuda_memory_pool.cpp', 'sllm_store/csrc/sllm_store/checkpoint_store.cpp', 'sllm_store/csrc/checkpoint/checkpoint.cpp', 'sllm_store/sllm_store/__init__.py', 'sllm_store/sllm_store/utils.py', 'sllm_store/sllm_store/client.py', 'sllm_store/csrc/sllm_store/model.cpp', 'sllm_store/csrc/sllm_store/model.h', 'sllm_store/proto/storage.proto']]
    required += [ROOT/'sllm_store/tests/python/s4a_component_runner.py', ROOT/'sllm_store/tests/python/test_kimi_tensor_component.py', GUARD, pathlib.Path(__file__).resolve(), RECEIPT, STORE/'packed-component/tensor.data_0', STORE/'packed-component/tensor_index.json']
    required += [ROOT/'sllm_store/sllm_store'/name for name in ('_C.cpython-312-x86_64-linux-gnu.so','_checkpoint_store.cpython-312-x86_64-linux-gnu.so','libglog.so.0.6.0')]
    assert len(required) == 24 and set(pins['files']) == {str(path) for path in required}
    for filename, digest in pins['files'].items():
        path = pathlib.Path(filename)
        assert path.is_absolute() and not path.is_symlink() and path.is_file()
        assert path.is_relative_to(ROOT) or path.is_relative_to(STORE) or path == RECEIPT
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    anchors = {'/data/brick1/boyjiang/kimi-k2.6/p0/s4a-saveonly-20261103-2103/packed-component/tensor.data_0': '4920e225718ab1a21e9dd771438c7f1c3c86277b76adfc5c86f91954569a80d8', '/data/brick1/boyjiang/kimi-k2.6/p0/s4a-saveonly-20261103-2103/packed-component/tensor_index.json': '5fbcb6f759d0b473f6ed41ef21a825ca020c44d51c489e8af813c75e3a5b8993', '/data/brick1/boyjiang/kimi-new/ServerlessLLM/sllm_store/sllm_store/_C.cpython-312-x86_64-linux-gnu.so': '41d2409ad03c1587c5fef8312cfd2e6672689f64c1b6c64c38a194755580943e', '/data/brick1/boyjiang/kimi-new/ServerlessLLM/sllm_store/sllm_store/_checkpoint_store.cpython-312-x86_64-linux-gnu.so': 'f757a8eb57d6247b91da1ef3955e996c1c7f2fc998bb6acde15a4e9a69b03ec3', '/data/brick1/boyjiang/kimi-new/ServerlessLLM/sllm_store/sllm_store/libglog.so.0.6.0': 'd76e3fafb8b0b0176a1b5a0ccc9d5db11c5118046b8f31d724731640740da9e6'}
    anchors[str(GUARD)] = '9387374428ae285f9740a4fe6f638aec2ba781384a5a9fdc01b47f6b2249ea3d'
    assert all(pins['files'][path] == digest for path,digest in anchors.items())
    return pins

def prepare():
    preparation_started = time.monotonic()
    assert os.geteuid() == 0 and not BASE.exists()
    verify_inputs()
    BASE.mkdir(mode=0o755); os.chown(BASE,2147,2147)
    args = DOCKER+['create','--name','kimi-'+RUN,'--runtime=nvidia','--gpus','device='+UUID,
        '--restart=no','--network=none','--user','2147:2147','--cpus=1','--memory=2g','--memory-swap=2g',
        '--pids-limit=512','--ulimit','core=0','--read-only','--tmpfs','/tmp:rw,exec,size=64m']
    for value in ['CUDA_VISIBLE_DEVICES='+UUID,'NVIDIA_VISIBLE_DEVICES='+UUID,
                  'NVIDIA_DRIVER_CAPABILITIES=compute,utility','OMP_NUM_THREADS=1','MKL_NUM_THREADS=1','OPENBLAS_NUM_THREADS=1']:
        args += ['--env',value]
    for source,target,readonly in [(ROOT/'sllm_store','/source/sllm_store',True),
            (STORE,'/store',True),(RECEIPT,'/reference/receipt.json',True),(BASE,'/evidence',False)]:
        args += ['--mount','type=bind,src='+str(source)+',dst='+target+(',readonly' if readonly else '')]
    args += ['--entrypoint','/usr/bin/python3',IMAGE,'-I','/source/sllm_store/tests/python/s4a_component_runner.py',
        '--storage','/store','--archive','packed-component','--receipt','/reference/receipt.json',
        '--evidence','/evidence/component','--go-file','/evidence/component-go.json','--run-id',RUN]
    cid = subprocess.check_output(args,text=True,timeout=10).strip()
    assert re.fullmatch('[0-9a-f]{64}',cid)
    row = json.loads(call(DOCKER+['inspect',cid]))[0]
    check_profile(row)
    assert row['State']['Status'] == 'created' and not row['State']['Running']
    save('container-before.json',row)
    save('prepared.json',{'cid':cid,'image':IMAGE,'gpu_uuid':UUID,'pids_limit':512,'controller_sha256':hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),'command':args,'preparation_started':preparation_started})

def check_profile(row):
    c,h = row['Config'],row['HostConfig']
    assert row['Image'] == IMAGE and row['RestartCount'] == 0
    assert h['PidsLimit'] == 512 and h['Memory'] == 2147483648 and h['MemorySwap'] == 2147483648
    assert h['NanoCpus'] == 1000000000 and h['ReadonlyRootfs'] and h['NetworkMode'] == 'none'
    assert h['Runtime'] == 'nvidia' and h['RestartPolicy']['Name'] == 'no' and c['User'] == '2147:2147'
    for name in ('CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES'):
        assert [x for x in c['Env'] if x.startswith(name+'=')] == [name+'='+UUID]
    assert c['Entrypoint'] == ['/usr/bin/python3']
    assert c['Cmd'] == ['-I','/source/sllm_store/tests/python/s4a_component_runner.py','--storage','/store',
        '--archive','packed-component','--receipt','/reference/receipt.json','--evidence','/evidence/component',
        '--go-file','/evidence/component-go.json','--run-id',RUN]
    expected = {('/source/sllm_store',str(ROOT/'sllm_store'),False),('/store',str(STORE),False),
                ('/reference/receipt.json',str(RECEIPT),False),('/evidence',str(BASE),True)}
    assert {(m['Destination'],m['Source'],m['RW']) for m in row['Mounts']} == expected
    assert h['DeviceRequests'][0]['DeviceIDs'] == [UUID] and len(h['DeviceRequests']) == 1
    assert h['Tmpfs'] == {'/tmp':'rw,exec,size=64m'}

def controller(expiry):
    started = time.monotonic()
    assert os.geteuid() == 0 and time.time()+180 < expiry
    pins = verify_inputs()
    receipt = json.loads((BASE/'prepared.json').read_text()); cid = receipt['cid']
    started = receipt['preparation_started']
    assert 0 <= time.monotonic()-started < 30, 'preparation/review consumed the one-shot budget'
    assert re.fullmatch('[0-9a-f]{64}', cid)
    assert receipt['gpu_uuid'] == UUID and receipt['pids_limit'] == 512
    assert hashlib.sha256(GUARD.read_bytes()).hexdigest() == '9387374428ae285f9740a4fe6f638aec2ba781384a5a9fdc01b47f6b2249ea3d'
    assert receipt['controller_sha256'] == pins['files'][str(pathlib.Path(__file__).resolve())]
    def inspect():
        row = json.loads(call(DOCKER+['inspect', cid]))[0]
        check_profile(row)
        assert row['Id'] == cid and row['Image'] == receipt['image']
        assert row['HostConfig']['PidsLimit'] == 512
        return row
    initial = inspect(); assert not initial['State']['Running'] and initial['State']['Status'] == 'created'
    assert gpu() == {'used_mib': 0, 'processes': []}
    guardunit = 'kimi-s4a-readback-revised-20261104-guard.service'
    observerunit = 'kimi-s4a-readback-revised-20261104-observer.service'
    go = None; child = None; observer_started = guard_started = False
    try:
        service(observerunit, ['/usr/bin/python3', str(pathlib.Path(__file__).resolve()), '--observer'], True)
        observer_started = True
        observer_ready_deadline = time.monotonic()+5
        while not (BASE/'observer-ready.json').exists():
            assert time.monotonic() < observer_ready_deadline; time.sleep(.03)
        service(guardunit, ['/usr/bin/python3', str(GUARD), cid, str(BASE)], True)
        guard_started = True
        armed_deadline = time.monotonic()+7
        while not (BASE/'ARMED.json').exists():
            assert time.monotonic() < armed_deadline; time.sleep(.03)
        armed = json.loads((BASE/'ARMED.json').read_text()); assert armed['cid'] == cid
        props = dict(line.split('=', 1) for line in call(['/usr/bin/systemctl', 'show', guardunit, '--property=MainPID,RuntimeMaxUSec,TimeoutStopUSec']).splitlines())
        assert int(props['MainPID']) == armed['guard_pid'] and props['RuntimeMaxUSec'] == '2min 20s' and props['TimeoutStopUSec'] == '3s'
        assert int((pathlib.Path('/proc')/str(armed['guard_pid'])/'stat').read_text().rsplit(')',1)[1].split()[19]) == armed['guard_startticks']
        go = time.monotonic(); save('GO.json', {'cid': cid, 'nonce': armed['nonce'], 'monotonic': go})
        save('component-go.json', {'monotonic': go, 'run_id': RUN}, 0o644)
        while not (BASE/'GO-ACK.json').exists():
            assert time.monotonic()-go < 2; time.sleep(.02)
        assert json.loads((BASE/'GO-ACK.json').read_text()) == {'cid': cid, 'go_monotonic': go}
        healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
        with (BASE/'docker-start.stdout').open('x') as out, (BASE/'docker-start.stderr').open('x') as err:
            child = subprocess.Popen(DOCKER+['start', cid], stdout=out, stderr=err)
        while True:
            assert not (BASE/'ABORT').exists() and time.monotonic()-go < 25
            healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
            row = inspect()
            if row['State']['Status'] == 'exited': break
            time.sleep(.2)
        assert row['State']['ExitCode'] == 0 and row['State']['Pid'] == 0 and row['RestartCount'] == 0
        parent = json.loads((BASE/'component/result.json').read_text())
        assert parent['status'] == 'COMPONENT_ROUNDTRIP_ORDERED_STOP_PASS'
        assert parent['server_returncode'] == 0 and parent['client_returncode'] == 0
        assert parent['server_stop_kind'] == 'SIGINT_GRACEFUL' and parent['forced'] is False
        assert parent['run_id'] == RUN and parent['elapsed_from_go'] <= 21
        records = [json.loads(line) for line in (BASE/'component/client.log').read_text().splitlines() if line.startswith('{')]
        component = records[-1]
        assert component['stage'] == 'OFFICIAL_COMPONENT_ROUNDTRIP' and component['payload_bytes'] == 8257544
        gold = json.loads(RECEIPT.read_text())
        assert component['restored_raw_sha256'] == gold['raw_sha256']
        assert component['tensor_index'] == gold['tensor_index']
        free = component['driver_free_bytes']
        assert free['before_load']-free['last_alias_held'] >= 8257544
        assert free['last_alias_dropped']-free['last_alias_held'] >= 8257544
        verify_inputs()
        assert child.wait(timeout=3) == 0
        save('normal-exit.json', {'state': row['State'], 'parent': parent, 'component': component})
        while not all((BASE/name).exists() for name in ('guard-final.json', 'observer-final.json')):
            assert not (BASE/'ABORT').exists() and time.monotonic()-started < 155
            healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
            time.sleep(.2)
        verify_inputs()
        final = json.loads((BASE/'guard-final.json').read_text())
        assert final['status'] == 'KERNEL_CLEANUP_RELEASE_PASS' and not final['failures'] and final['quiet_seconds'] >= 60
        assert hashlib.sha256((BASE/'oci.raw.json').read_bytes()).hexdigest() == final['raw_oci_sha256']
        assert (BASE/'oci.raw.json').stat().st_mode & 0o777 == 0o600
        observed = json.loads((BASE/'observer-final.json').read_text())
        assert observed['gpu_uuid'] == UUID and observed['quiet_seconds'] >= 60 and observed['peak_mib'] <= 2048
        for unit, name in ((guardunit,'guard-final.json'), (observerunit,'observer-final.json')):
            while int(healthy(unit,name)['MainPID']) != 0:
                assert time.monotonic()-started < 155; time.sleep(.05)
        save('controller-final.json', {'status': 'S4A_COMPONENT_ORDERED_STOP_RELEASE_PASS', 'elapsed': time.monotonic()-started, 'guard': final})
    except BaseException as error:
        (BASE/'ABORT').touch(exist_ok=True)
        save('controller-failed.json', {'error_type': type(error).__name__, 'error': str(error), 'go': go})
        # Keep the independent services alive through cleanup/release; no Docker kill.
        while time.monotonic()-started < 160:
            pending = []
            for unit, launched in ((guardunit,guard_started),(observerunit,observer_started)):
                if launched:
                    try: pending.append(int(unitprops(unit)['MainPID']) != 0)
                    except Exception: pending.append(True)
            if not any(pending): break
            time.sleep(.2)
        raise
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observer', action='store_true')
    parser.add_argument('--create-only', action='store_true')
    parser.add_argument('--run-once', action='store_true')
    parser.add_argument('--booking-expiry-epoch', type=float)
    parser.add_argument('--observer-evidence', type=pathlib.Path)
    parser.add_argument('--observer-uuid', action='append')
    parser.add_argument('--observer-seconds', type=float, default=135)
    parser.add_argument('--observer-limit-mib', type=int, default=2048)
    args = parser.parse_args()
    if args.observer: observer(args.observer_evidence, args.observer_uuid, args.observer_seconds, args.observer_limit_mib)
    elif args.create_only: prepare()
    elif args.run_once:
        assert args.booking_expiry_epoch is not None
        native = dict(line.split('=',1) for line in call(['/usr/bin/systemctl','show',
            'kimi-s4a-readback-revised-20261104-controller.service',
            '--property=RuntimeMaxUSec,TimeoutStopUSec,KillMode,MainPID,SendSIGKILL,Type,Restart']).splitlines())
        assert native['RuntimeMaxUSec'] == '2min 57s' and native['TimeoutStopUSec'] == '1s'
        assert native['KillMode'] == 'control-group' and int(native['MainPID']) == os.getpid()
        assert native['SendSIGKILL'] == 'yes' and native['Type'] == 'exec' and native['Restart'] == 'no'
        prepare(); controller(args.booking_expiry_epoch)
    else:
        parser.error('use --run-once for the guarded execution entry')
