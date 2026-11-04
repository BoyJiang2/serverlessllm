"""One bounded eight-rank CUDA/NCCL micro; no model or Store."""
import argparse, hashlib, json, os, pathlib, re, subprocess, time
import math

ROOT = pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
RUN = 'kimi-tp8-cuda-nccl-micro-cdi2-20261104'
BASE = pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0')/RUN
UUIDS = ['GPU-736d7f37-f647-b82d-ab11-693753ac6822','GPU-c5510474-1f6d-03f1-c57f-476db275c5cb','GPU-d8081222-9816-41e4-68f8-ecbb13de6855','GPU-15b80bc6-fcac-96f8-411d-3fd7ad0ecb8f','GPU-1fcf8aaa-8825-c6d3-fb8d-245377038242','GPU-0df5ba07-c402-f8fd-562d-7fccb4f15d1a','GPU-cca00d09-79a7-49de-2290-9ba1d56f8c6b','GPU-21336138-6e64-ec08-305b-26c99d661ab9']
IMAGE = 'sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27'
OBSERVER = ROOT/'sllm_store/tests/s4a_root_controller.py'
PROBE = ROOT/'sllm_store/tests/cuda_micro_probe.py'
PINS = {ROOT/'sllm_store/tests/guard_cuda_micro.py': '2b5726e497e2f6e8883adb2c676937ce628c03ecde85d62c42eb6750944cec4b', OBSERVER:'f5e0d1ef0dde38a5abd4a3c8b48aebd541ff8ac3a89c110709bbd76b2084baca', PROBE:'07d2c986fef6750083d195ccf2587f02261f359dcdcdff1794c237c73c9569e2'}
GUARD = ROOT/'sllm_store/tests/guard_cuda_micro.py'
DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']
NVIDIA_SMI = '/usr/bin/nvidia-smi'
GPU_QUERY_TIMEOUT = 15

def call(argv, timeout=3):
    return subprocess.check_output(argv, text=True, timeout=timeout)

def save(name, row):
    path = BASE/name
    assert not path.exists()
    temporary = BASE/(name+'.tmp')
    fd = os.open(temporary, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(row, stream); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(BASE, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)

def unitprops(unit):
    return dict(line.split('=',1) for line in call(['/usr/bin/systemctl','show',unit,
        '--property=Type,LoadState,MainPID,ExecMainCode,ExecMainStatus,Result,ActiveState,RuntimeMaxUSec,TimeoutStopUSec,ActiveEnterTimestampMonotonic,Restart,KillMode,SendSIGKILL,FinalKillSignal']).splitlines())

def healthy(unit, finalname):
    row = unitprops(unit)
    if int(row['MainPID']) == 0:
        assert (BASE/finalname).exists() and row['ExecMainCode'] == '1' and row['ExecMainStatus'] == '0' and row['Result'] == 'success'
    else:
        assert row['ActiveState'] == 'active'
    return row

def gpu():
    # Narrow queries avoid collecting unrelated driver fields.
    memory = call([NVIDIA_SMI, '--query-gpu=uuid,memory.used', '--format=csv,noheader,nounits'],
                  timeout=GPU_QUERY_TIMEOUT)
    apps = call([NVIDIA_SMI, '--query-compute-apps=gpu_uuid,pid', '--format=csv,noheader'],
                timeout=GPU_QUERY_TIMEOUT)
    used = {}
    for line in memory.splitlines():
        if line.strip():
            uuid, mib = [field.strip() for field in line.split(',')]
            assert uuid not in used, 'duplicate GPU memory row'
            used[uuid] = int(mib)
            assert used[uuid] >= 0, 'negative GPU memory'
    processes = {}
    for line in apps.splitlines():
        if line.strip():
            uuid, pid = [field.strip() for field in line.split(',')]
            assert pid.isdecimal() and int(pid) > 0, 'invalid GPU process PID'
            processes.setdefault(uuid, []).append(pid)
    assert len(used) == 8 and set(used) == set(UUIDS)
    return {uuid: {'used_mib': used[uuid], 'processes': processes.get(uuid, [])} for uuid in UUIDS}

def validate_messages(messages):
    rows = [m for m in messages if m.get('event') == 'TP8_CUDA_NCCL_MICRO_PASS']
    assert len(rows) == 8 and {r['rank'] for r in rows} == set(range(8))
    assert len([m for m in messages if m.get('event') == 'PYTHON_ENTERED']) == 8
    for row in rows:
        assert row['local_rank'] == row['rank'] and row['world_size'] == row['device_count'] == 8
        assert row['device_uuid'] == UUIDS[row['rank']]
        assert row['cuda_visible_count'] == 8
        # Delivery, reported by the worker itself in the same run that completed the
        # collective: CDI rewrote the NVIDIA hint to "void" and the loopback
        # rendezvous survived the torchrun spawn. Neither is inferred from the
        # create-time request.
        assert row['nvidia_visible'] == 'void'
        assert (row['master_addr'], row['master_port']) == ('127.0.0.1', '29500')
        assert row['payload_bytes'] == 1048576 and row['dtype'] == 'torch.float32'
        assert row['reduced_value'] == 28 and row['raw_sha256'] == '2e042dc2f9f98002bb4fa72e283fcbfe046c03321dfc68f094834ca03891b814'
        assert row['allocator_peak_bytes'] >= 1048576
        assert all(isinstance(row[key],(int,float)) and math.isfinite(row[key]) and row[key] >= 0 for key in ('python_entered_monotonic','rank_ready_monotonic','nccl_init_seconds'))
        assert row['rank_ready_monotonic'] >= row['python_entered_monotonic']
        assert row['nccl_init_seconds'] <= row['rank_ready_monotonic']-row['python_entered_monotonic']
    return rows

def validate_create_request(row):
    """Assert the request stored at CREATE time, before the container ever starts.

    This is not delivery evidence. It runs against `docker inspect` of a created
    but unstarted container, so it only proves what was asked for. The nvidia
    runtime rewrites NVIDIA_VISIBLE_DEVICES to "void" in the OCI spec at start, so
    the equality below holds here and must not be expected to hold in the worker.
    Delivery is asserted by tp8_environment() in the worker and by
    validate_messages() on what each rank reported after the collective.
    """
    host = row['HostConfig']; config = row['Config']
    assert row['Image'] == IMAGE and host['PidsLimit'] == 4096
    assert host['Memory'] == host['MemorySwap'] == 16*(1<<30)
    assert host['NanoCpus'] == 32*10**9 and host['ShmSize'] == 8*(1<<30)
    assert host['ReadonlyRootfs'] and host['NetworkMode'] == 'none' and host['Runtime'] == 'nvidia'
    assert host['RestartPolicy']['Name'] == 'no' and config['User'] == '2147:2147'
    env = dict(value.split('=',1) for value in config['Env'])
    assert env['CUDA_VISIBLE_DEVICES'] == env['NVIDIA_VISIBLE_DEVICES'] == ','.join(UUIDS)
    assert config['Entrypoint'] == ['python3']
    assert config['Cmd'] == ['-I','-m','torch.distributed.run','--rdzv-backend=static','--nnodes=1','--node-rank=0','--master-addr=127.0.0.1','--master-port=29500','--nproc_per_node=8','--max_restarts=0','--no-python','/usr/bin/python3','-I','/source/sllm_store/tests/cuda_micro_probe.py','--tp8']
    assert all(env[name] == '1' for name in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'))
    assert host['Tmpfs'] == {'/tmp':'rw,nosuid,nodev,size=64m'}
    assert any(limit['Name'] == 'core' and limit['Soft'] == limit['Hard'] == 0 for limit in host['Ulimits'])
    assert len(row['Mounts']) == 1
    mount = row['Mounts'][0]
    assert mount['Source'] == str(ROOT/'sllm_store') and mount['Destination'] == '/source/sllm_store' and not mount['RW']

def service(unit, argv, remain=False):
    outer = unitprops(RUN+'-controller.service')
    deadline = int(outer['ActiveEnterTimestampMonotonic'])/1e6+218
    assert int(outer['ActiveEnterTimestampMonotonic']) > 0
    assert time.monotonic()+186 <= deadline, 'independent service exceeds outer budget'
    call(['/usr/bin/systemd-run', '--unit='+unit, '--property=Type=exec',
          '--property=RuntimeMaxSec=180', '--property=TimeoutStopSec=3',
          '--property=Restart=no', '--property=KillMode=control-group',
          '--property=SendSIGKILL=yes', '--property=FinalKillSignal=SIGKILL',
          '--property=MemoryMax=256M', '--property=TasksMax=64',
          *(['--property=RemainAfterExit=yes'] if remain else []),
          '--property=StandardOutput=append:'+str(BASE/(unit+'.stdout')),
          '--property=StandardError=append:'+str(BASE/(unit+'.stderr')), *argv])
    actual = unitprops(unit)
    assert actual['RuntimeMaxUSec'] == '3min' and actual['TimeoutStopUSec'] == '3s'
    assert int(actual['ActiveEnterTimestampMonotonic']) > 0
    assert int(actual['ActiveEnterTimestampMonotonic'])/1e6+183 <= deadline

def monitor_status(observed):
    monitor = observed['monitor']
    assert isinstance(monitor['gaps'], int) and monitor['gaps'] >= 0
    assert monitor['samples'] > 0 and monitor['memory_samples'] >= monitor['samples']
    assert all(math.isfinite(monitor[k]) and monitor[k] >= 0 for k in ('max_query_seconds', 'max_memory_interval_seconds'))
    assert monitor['peak_kind'] == 'SAMPLED_LOWER_BOUND'
    expected = 'SAMPLED_WITH_QUERY_GAPS' if monitor['gaps'] else 'SAMPLED_NO_QUERY_ERRORS'
    assert monitor['peak_coverage'] == expected
    return 'TP8_MICRO_COMPUTE_RELEASE_PASS_TELEMETRY_GAPS' if monitor['gaps'] else 'TP8_MICRO_NORMAL_EXIT_RELEASE_PASS'

def controller(expiry, expected_sha):
    started = time.monotonic(); start_epoch = time.time()
    assert os.geteuid() == 0 and math.isfinite(expiry) and time.time()+220 < expiry
    assert hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest() == expected_sha
    for path, digest in PINS.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    outer = unitprops(RUN+'-controller.service')
    assert outer['Type'] == 'exec' and int(outer['MainPID']) == os.getpid()
    assert outer['RuntimeMaxUSec'] == '3min 37s' and outer['TimeoutStopUSec'] == '1s'
    assert outer['Restart'] == 'no' and outer['KillMode'] == 'control-group' and outer['SendSIGKILL'] == 'yes' and outer['FinalKillSignal'] == '9'
    assert int(outer['ActiveEnterTimestampMonotonic']) > 0
    started = int(outer['ActiveEnterTimestampMonotonic'])/1e6
    assert started <= time.monotonic()
    assert not BASE.exists()
    assert all(r == {'used_mib':0, 'processes':[]} for r in gpu().values())
    BASE.mkdir(mode=0o700)
    cid = None; go = None; child = None; observer_started = guard_started = False
    guardunit = RUN+'-guard.service'; observerunit = RUN+'-observer.service'
    try:
        env = ['CUDA_VISIBLE_DEVICES='+','.join(UUIDS), 'NVIDIA_VISIBLE_DEVICES='+','.join(UUIDS), 'NVIDIA_DRIVER_CAPABILITIES=compute,utility', 'OMP_NUM_THREADS=1','MKL_NUM_THREADS=1','OPENBLAS_NUM_THREADS=1']
        argv = DOCKER+['create','--name',RUN,'--runtime=nvidia','--network=none','--read-only','--user=2147:2147','--cpus=32','--memory=16g','--memory-swap=16g','--pids-limit=4096','--shm-size=8g','--ulimit=core=0','--tmpfs=/tmp:rw,nosuid,nodev,size=64m','--mount=type=bind,src='+str(ROOT/'sllm_store')+',dst=/source/sllm_store,readonly']
        for value in env: argv += ['--env',value]
        argv += ['--entrypoint=python3',IMAGE,'-I','-m','torch.distributed.run','--rdzv-backend=static','--nnodes=1','--node-rank=0','--master-addr=127.0.0.1','--master-port=29500','--nproc_per_node=8','--max_restarts=0','--no-python','/usr/bin/python3','-I','/source/sllm_store/tests/cuda_micro_probe.py','--tp8']
        cid = subprocess.check_output(argv, text=True, timeout=10).strip()
        assert re.fullmatch('[0-9a-f]{64}', cid)
        receipt = {'cid':cid,'image':IMAGE,'gpu_uuids':UUIDS,'pids_limit':4096,'argv':argv,'started':started,'epoch':start_epoch,'budget_seconds':220,'outer':outer,'source_sha256':{str(p):d for p,d in PINS.items()},'controller_sha256':expected_sha}
        save('prepared.json',receipt)
        kernel = subprocess.run(['/usr/bin/journalctl','-k','--since=@'+str(receipt['epoch']-2),'--no-pager','-n','200','-o','short-iso'],capture_output=True,text=True,timeout=3)
        save('kernel-before.json',{'returncode':kernel.returncode,'stdout':kernel.stdout,'stderr':kernel.stderr})
        def inspect():
            row = json.loads(call(DOCKER+['inspect', cid]))[0]
            assert row['Id'] == cid and row['Image'] == receipt['image']
            assert row['HostConfig']['PidsLimit'] == 4096
            return row
        initial = inspect(); validate_create_request(initial)
        save('docker-inspect-created.json',initial)
        assert not initial['State']['Running'] and initial['State']['Status'] == 'created'
        assert all(r == {'used_mib': 0, 'processes': []} for r in gpu().values())
        observer_argv = ['/usr/bin/python3', str(OBSERVER), '--observer', '--observer-evidence', str(BASE), '--observer-seconds','175','--observer-limit-mib','2048']
        for uuid in UUIDS: observer_argv += ['--observer-uuid',uuid]
        service(observerunit, observer_argv, True)
        observer_started = True
        while not (BASE/'observer-ready.json').exists():
            assert time.monotonic()-started < 20; time.sleep(.03)
        service(guardunit, ['/usr/bin/python3', str(GUARD), cid, str(BASE),'--deadline','60','--kill-after','3','--overall','100'], True)
        guard_started = True
        armed_deadline = time.monotonic()+7
        while not (BASE/'ARMED.json').exists():
            assert time.monotonic() < armed_deadline; time.sleep(.03)
        armed = json.loads((BASE/'ARMED.json').read_text()); assert armed['cid'] == cid
        props = unitprops(guardunit)
        assert int(props['MainPID']) == armed['guard_pid']
        assert (armed['deadline_seconds'],armed['kill_after_seconds'],armed['cleanup_seconds'],armed['release_seconds']) == (60,3,100,60)
        for unit in (guardunit,observerunit):
            actual = unitprops(unit)
            assert actual['RuntimeMaxUSec'] == '3min' and actual['TimeoutStopUSec'] == '3s'
            assert int(actual['ActiveEnterTimestampMonotonic'])/1e6+183 <= started+218
        assert time.monotonic()-started < 32
        assert int((pathlib.Path('/proc')/str(armed['guard_pid'])/'stat').read_text().rsplit(')',1)[1].split()[19]) == armed['guard_startticks']
        go = time.monotonic(); save('GO.json', {'cid': cid, 'nonce': armed['nonce'], 'monotonic': go})
        while not (BASE/'GO-ACK.json').exists():
            assert time.monotonic()-go < 2; time.sleep(.02)
        assert json.loads((BASE/'GO-ACK.json').read_text()) == {'cid': cid, 'go_monotonic': go}
        healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
        with (BASE/'docker-start.stdout').open('x') as out, (BASE/'docker-start.stderr').open('x') as err:
            child = subprocess.Popen(DOCKER+['start', cid], stdout=out, stderr=err)
        while True:
            assert not (BASE/'ABORT').exists() and time.monotonic()-go < 60
            healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
            row = inspect()
            if row['State']['Status'] == 'exited': break
            time.sleep(.2)
        assert row['State']['ExitCode'] == 0 and row['State']['Pid'] == 0 and not row['State']['OOMKilled'] and row['RestartCount'] == 0
        save('docker-inspect-exited.json',row)
        logs = call(DOCKER+['logs', '--tail', '200', cid])
        save('docker-logs.json',{'text':logs})
        messages = [json.loads(line) for line in logs.splitlines() if line.startswith('{')]
        rank_results = validate_messages(messages)
        ready = [m for m in messages if m.get('event') == 'TP8_RANK_READY']
        assert len(ready) == 8 and {r['rank'] for r in ready} == set(range(8))
        for r in ready:
            final = next(m for m in rank_results if m['rank'] == r['rank'])
            assert all(r[key] == final[key] for key in ('python_entered_monotonic','rank_ready_monotonic','nccl_init_seconds'))
        all_ready = max(r['rank_ready_monotonic'] for r in rank_results)
        assert min(r['python_entered_monotonic'] for r in rank_results) >= go
        save('rank-timing.json',{'all_eight_ready_seconds_from_go':all_ready-go,'all_eight_ready_seconds_from_first_python':all_ready-min(r['python_entered_monotonic'] for r in rank_results),'per_rank':rank_results})
        assert child.wait(timeout=3) == 0
        save('normal-exit.json', {'state': row['State'], 'messages': messages})
        while not all((BASE/name).exists() for name in ('guard-final.json', 'observer-final.json')):
            assert not (BASE/'ABORT').exists() and time.monotonic()-started < 200
            healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
            time.sleep(.2)
        final = json.loads((BASE/'guard-final.json').read_text())
        assert final['status'] == 'KERNEL_CLEANUP_RELEASE_PASS' and not final['failures'] and final['quiet_seconds'] >= 60
        assert hashlib.sha256((BASE/'oci.raw.json').read_bytes()).hexdigest() == final['raw_oci_sha256']
        assert (BASE/'oci.raw.json').stat().st_mode & 0o777 == 0o600
        observed = json.loads((BASE/'observer-final.json').read_text())
        assert observed['gpu_uuids'] == UUIDS and observed['quiet_seconds'] >= 60
        status = monitor_status(observed)
        assert set(observed['peak_mib_by_uuid']) == set(UUIDS) and max(observed['peak_mib_by_uuid'].values()) <= 2048
        for unit, name in ((guardunit,'guard-final.json'), (observerunit,'observer-final.json')):
            while int(healthy(unit,name)['MainPID']) != 0:
                assert time.monotonic()-started < 200; time.sleep(.05)
        assert time.monotonic()+2*GPU_QUERY_TIMEOUT+6 <= started+218, 'final queries exceed outer budget'
        assert all(r == {'used_mib':0,'processes':[]} for r in gpu().values())
        assert not pathlib.Path('/sys/fs/cgroup/system.slice/docker-'+cid+'.scope').exists()
        for path, digest in PINS.items(): assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        assert hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest() == expected_sha
        save('controller-final.json', {'status':status,'cid':cid,'elapsed':time.monotonic()-started,'guard':final,'observer':observed,'guard_exit':unitprops(guardunit),'observer_exit':unitprops(observerunit)})
    except BaseException as error:
        (BASE/'ABORT').touch(exist_ok=True)
        save('controller-failed.json', {'status':'FAILED_RETAINED','cid':cid,'error_type': type(error).__name__, 'error': str(error), 'go': go,'elapsed':time.monotonic()-started})
        if cid is not None:
            try: save('docker-inspect-failed.json',json.loads(call(DOCKER+['inspect',cid]))[0])
            except Exception: pass
        # Keep the independent services alive through cleanup/release; no Docker kill.
        while time.monotonic()-started < 205:
            pending = []
            for unit, launched in ((guardunit,guard_started),(observerunit,observer_started)):
                if launched:
                    try: pending.append(int(unitprops(unit)['MainPID']) != 0)
                    except Exception: pending.append(True)
            if not any(pending): break
            time.sleep(.2)
        raise
    finally:
        try:
            kernel = subprocess.run(['/usr/bin/journalctl','-k','--since=@'+str(start_epoch-2),'--no-pager','-n','200','-o','short-iso'],capture_output=True,text=True,timeout=3)
            save('kernel-after.json',{'returncode':kernel.returncode,'stdout':kernel.stdout,'stderr':kernel.stderr})
        except Exception as error:
            try: save('kernel-after-error.json',{'error_type':type(error).__name__,'error':str(error)})
            except Exception: pass
        if child is not None and child.poll() is None:
            child.terminate()
            try: child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait(timeout=2)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--booking-expiry-epoch', type=float, required=True)
    parser.add_argument('--expected-controller-sha', required=True)
    args = parser.parse_args()
    controller(args.booking_expiry_epoch, args.expected_controller_sha)
