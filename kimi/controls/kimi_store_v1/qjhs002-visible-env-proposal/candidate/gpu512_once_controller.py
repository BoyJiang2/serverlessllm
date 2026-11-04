"""Review draft: one prepared container; no booking or container creation here."""
import argparse, hashlib, json, os, pathlib, re, subprocess, time
import xml.etree.ElementTree as ET

ROOT = pathlib.Path('/data/brick1/boyjiang/kimi-new/ServerlessLLM')
BASE = pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/cuda-micro-pids512-visible-20261104')
UUID = 'GPU-736d7f37-f647-b82d-ab11-693753ac6822'
GUARD = ROOT/'sllm_store/tests/guard_cuda_micro.py'
DOCKER = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock']

def call(argv):
    return subprocess.check_output(argv, text=True, timeout=3)

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
        '--property=MainPID,ExecMainCode,ExecMainStatus,Result,ActiveState']).splitlines())

def healthy(unit, finalname):
    row = unitprops(unit)
    if int(row['MainPID']) == 0:
        assert (BASE/finalname).exists() and row['ExecMainCode'] == '1' and row['ExecMainStatus'] == '0' and row['Result'] == 'success'
    else:
        assert row['ActiveState'] == 'active'
    return row

def gpu():
    raw = call(['/usr/bin/nvidia-smi', '--id='+UUID, '-q', '-x'])
    row = ET.fromstring(raw).find('gpu')
    assert row.findtext('uuid') == UUID
    return {'used_mib': int(row.findtext('fb_memory_usage/used').split()[0]),
            'processes': [p.findtext('pid') for p in row.findall('processes/process_info')]}

def service(unit, argv, remain=False):
    call(['/usr/bin/systemd-run', '--unit='+unit, '--property=Type=exec',
          '--property=RuntimeMaxSec=140', '--property=TimeoutStopSec=3',
          '--property=Restart=no', '--property=KillMode=control-group',
          '--property=SendSIGKILL=yes', '--property=FinalKillSignal=SIGKILL',
          '--property=MemoryMax=256M', '--property=TasksMax=64',
          *(['--property=RemainAfterExit=yes'] if remain else []),
          '--property=StandardOutput=append:'+str(BASE/(unit+'.stdout')),
          '--property=StandardError=append:'+str(BASE/(unit+'.stderr')), *argv])

def observer():
    start = time.monotonic(); quiet = None; peak = 0
    try:
        assert gpu() == {'used_mib': 0, 'processes': []}
        save('observer-ready.json', {'pid': os.getpid(), 'gpu_uuid': UUID})
        with (BASE/'gpu-observer.jsonl').open('x') as stream:
            while time.monotonic()-start < 135:
                row = gpu(); peak = max(peak, row['used_mib'])
                stream.write(json.dumps({'mono': time.monotonic(), **row})+'\n'); stream.flush()
                assert row['used_mib'] <= 2048
                if (BASE/'normal-exit.json').exists() or (BASE/'ABORT').exists():
                    if row != {'used_mib': 0, 'processes': []}:
                        quiet = None; time.sleep(.5); continue
                    quiet = quiet or time.monotonic()
                    if time.monotonic()-quiet >= 60:
                        save('observer-final.json', {'gpu_uuid': UUID, 'quiet_seconds': time.monotonic()-quiet, 'peak_mib': peak})
                        return
                time.sleep(.5)
        raise TimeoutError('observer deadline')
    except BaseException:
        (BASE/'ABORT').touch(exist_ok=True)
        raise

def controller(expiry):
    started = time.monotonic()
    assert os.geteuid() == 0 and time.time()+180 < expiry
    receipt = json.loads((BASE/'prepared.json').read_text()); cid = receipt['cid']
    assert re.fullmatch('[0-9a-f]{64}', cid)
    assert receipt['gpu_uuid'] == UUID and receipt['pids_limit'] == 512
    assert hashlib.sha256(GUARD.read_bytes()).hexdigest() == '9387374428ae285f9740a4fe6f638aec2ba781384a5a9fdc01b47f6b2249ea3d'
    for name, digest in receipt['script_sha256'].items():
        assert pathlib.Path(name).name == name
        assert hashlib.sha256((BASE.parent/name).read_bytes()).hexdigest() == digest
    def inspect():
        row = json.loads(call(DOCKER+['inspect', cid]))[0]
        assert row['Id'] == cid and row['Image'] == receipt['image']
        assert row['HostConfig']['PidsLimit'] == 512
        return row
    initial = inspect(); assert not initial['State']['Running'] and initial['State']['Status'] == 'created'
    assert gpu() == {'used_mib': 0, 'processes': []}
    guardunit = 'kimi-cuda-pids512-visible-20261104-guard.service'
    observerunit = 'kimi-cuda-pids512-visible-20261104-observer.service'
    go = None; child = None; observer_started = guard_started = False
    try:
        service(observerunit, ['/usr/bin/python3', str(pathlib.Path(__file__).resolve()), '--observer'], True)
        observer_started = True
        while not (BASE/'observer-ready.json').exists():
            assert time.monotonic()-started < 5; time.sleep(.03)
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
        assert row['State']['ExitCode'] == 0 and row['State']['Pid'] == 0
        logs = call(DOCKER+['logs', '--tail', '20', cid])
        messages = [json.loads(line) for line in logs.splitlines() if line.startswith('{')]
        assert any(m.get('event') == 'PYTHON_ENTERED' for m in messages)
        compute = next(m for m in messages if m.get('event') == 'CUDA_MICRO_COMPUTE_PASS')
        assert compute['payload_bytes'] == 1048576 and compute['allocator_peak_bytes'] >= 1048576
        assert child.wait(timeout=3) == 0
        save('normal-exit.json', {'state': row['State'], 'messages': messages})
        while not all((BASE/name).exists() for name in ('guard-final.json', 'observer-final.json')):
            assert not (BASE/'ABORT').exists() and time.monotonic()-started < 165
            healthy(observerunit, 'observer-final.json'); healthy(guardunit, 'guard-final.json')
            time.sleep(.2)
        final = json.loads((BASE/'guard-final.json').read_text())
        assert final['status'] == 'KERNEL_CLEANUP_RELEASE_PASS' and not final['failures'] and final['quiet_seconds'] >= 60
        assert hashlib.sha256((BASE/'oci.raw.json').read_bytes()).hexdigest() == final['raw_oci_sha256']
        assert (BASE/'oci.raw.json').stat().st_mode & 0o777 == 0o600
        observed = json.loads((BASE/'observer-final.json').read_text())
        assert observed['gpu_uuid'] == UUID and observed['quiet_seconds'] >= 60 and observed['peak_mib'] <= 2048
        for unit, name in ((guardunit,'guard-final.json'), (observerunit,'observer-final.json')):
            while int(healthy(unit,name)['MainPID']) != 0:
                assert time.monotonic()-started < 165; time.sleep(.05)
        save('controller-final.json', {'status': 'GPU512_MICRO_NORMAL_EXIT_RELEASE_PASS', 'elapsed': time.monotonic()-started, 'guard': final})
    except BaseException as error:
        (BASE/'ABORT').touch(exist_ok=True)
        save('controller-failed.json', {'error_type': type(error).__name__, 'error': str(error), 'go': go})
        # Keep the independent services alive through cleanup/release; no Docker kill.
        while time.monotonic()-started < 170:
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
    parser.add_argument('--booking-expiry-epoch', type=float)
    args = parser.parse_args()
    if args.observer: observer()
    else:
        assert args.booking_expiry_epoch is not None
        controller(args.booking_expiry_epoch)
