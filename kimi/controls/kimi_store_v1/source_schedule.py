"""Source budgets and owned-target watchdog candidate; no compute authorization."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import selectors
import socket
import struct
import subprocess
import sys
import time

PHASES = ('preflight', 'image_restore', 'engine_initialize_and_export', 'fixed_token_business',
          'natural_eos_business', 'worker_shutdown', 'release_observe', 'cleanup_evidence', 'safety_margin')
TAIL = ('worker_shutdown', 'release_observe', 'cleanup_evidence', 'safety_margin')
GUARD_PURPOSE = 'KIMI-SOURCE-UNREVIEWED'
P0_PURPOSE = 'KIMI-CUDA-IPC-P0A-UNREVIEWED'
P0_PHASES = ('preflight', 'guard_setup', 'component', *TAIL)


def _utc(value):
    # Docker RFC3339Nano must also parse on the host's Python 3.10.
    if not isinstance(value,str):
        raise ValueError('timezone-qualified time required')
    match = re.fullmatch(r'([0-9]{4}-[0-9]{2}-[0-9]{2}[Tt ][0-9]{2}:[0-9]{2}:[0-9]{2})(?:[.]([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})',value)
    if match:
        base,fraction,offset = match.groups()
        value = base + ('.'+fraction[:6].ljust(6,'0') if fraction else '') + ('+00:00' if offset=='Z' else offset)
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone-qualified time required')
    return result.astimezone(timezone.utc)


def load_source_plan(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('external source plan SHA required')
    with open(path, 'rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('source plan length or SHA differs')
    return json.loads(raw)


def source_schedule(plan, source_receipt, *, now):
    return _window_schedule(plan, source_receipt, now=now, phases=PHASES,
                            schema='kimi-source-window-v1')


def p0_schedule(plan, receipt, *, now):
    result = _window_schedule(plan, receipt, now=now, phases=P0_PHASES, schema='kimi-p0-window-v1')
    if (result['total_max_seconds'] > 1800
            or plan['seconds']['component'] != receipt['component_max_seconds']):
        raise ValueError('P0 full batch must fit 1800 seconds and exact component budget')
    return result


def _window_schedule(plan, source_receipt, *, now, phases, schema):
    required = {'schema', 'run_id', 'holder', 'code_commit', 'spec_sha256', 'source_manifest_sha256',
                'resource_id', 'booking_id', 'window_start', 'window_end', 'cleanup_complete_by', 'seconds'}
    if (not isinstance(plan, dict) or set(plan) != required
            or plan['schema'] != schema or plan['resource_id'] != 'h20/qjhs003'
            or not isinstance(plan['booking_id'], str) or not plan['booking_id'].startswith('bk-')):
        raise ValueError('exact source-window schema and node required')
    for name, receipt_name in (('run_id', 'run_id'), ('holder', 'holder'), ('code_commit', 'code_commit'),
            ('spec_sha256', 'spec_sha256'), ('source_manifest_sha256', 'manifest_sha256')):
        if plan[name] != source_receipt[receipt_name]:
            raise ValueError('source window identity differs: ' + name)
    seconds = plan['seconds']
    if (not isinstance(seconds, dict) or set(seconds) != set(phases)
            or any(type(v) is not int or not 0 < v <= 86400 for v in seconds.values())
            or seconds['release_observe'] < 60):
        raise ValueError('all finite phase maxima and at least 60-second observation budget required')
    start, end, cleanup = (_utc(plan[k]) for k in ('window_start', 'window_end', 'cleanup_complete_by'))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone-qualified current time required')
    current = now.astimezone(timezone.utc)
    total = sum(seconds.values())
    if not start < cleanup <= end - timedelta(minutes=10):
        raise ValueError('cleanup must finish at least ten minutes before booking end')
    if current < start:
        raise ValueError('source window has not started')
    if current + timedelta(seconds=total) > cleanup:
        raise TimeoutError('remaining window cannot cover full source budget and cleanup')
    run_cleanup = current + timedelta(seconds=total)
    guard_fire = run_cleanup - timedelta(seconds=sum(seconds[n] for n in TAIL))
    cursor, stages = current, {}
    for name in phases:
        cursor += timedelta(seconds=seconds[name])
        stages[name] = cursor.isoformat()
    return dict(total_max_seconds=total, compute_max_seconds=total-sum(seconds[n] for n in TAIL),
                guard_fire_by_utc=guard_fire.isoformat(), cleanup_complete_by_utc=run_cleanup.isoformat(),
                window_cleanup_deadline_utc=cleanup.isoformat(),
                phase_deadlines_utc=stages, minimum_release_observe_seconds=60, timing_fits=True,
                booking_verified=False, guard_verified=False, runtime_admitted=False)


def phase_remaining_seconds(schedule, phase, *, entered, elapsed, now):
    """Use the original batch anchor, never a new per-phase start time."""
    if phase not in PHASES + P0_PHASES or phase not in schedule['phase_deadlines_utc']:
        raise ValueError('unknown source phase')
    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
            or entered.tzinfo is None or entered.utcoffset() is None
            or now.tzinfo is None or now.utcoffset() is None):
        raise ValueError('finite elapsed time and timezone-qualified clocks required')
    deadline = _utc(schedule['phase_deadlines_utc'][phase])
    if now < entered:
        raise TimeoutError('source wall clock regressed')
    remaining = min((deadline - entered).total_seconds() - elapsed,
                    (deadline - now).total_seconds())
    if remaining <= 0:
        raise TimeoutError('source phase deadline exhausted: ' + phase)
    return remaining


def _guard_identity(registration, *, boot_id, uid):
    from vllm_export_hook import _source_identity
    from source_bundle import _p0_identity
    p0 = isinstance(registration, dict) and registration.get('schema') == 'kimi-p0-guard-target-v2'
    keys = {'schema', 'identity', 'boot_id', 'uid', 'container_id', 'image_id', 'container_created'}
    if p0: keys = keys | {'gpu_uuid', 'run_root', 'worker_input_sha256', 'environment_sha256'}
    if (not isinstance(registration, dict) or set(registration) != keys
            or registration['schema'] not in ('kimi-source-guard-target-v1', 'kimi-p0-guard-target-v2')):
        raise ValueError('exact source guard target schema required')
    identity = (_p0_identity if p0 else _source_identity)(registration['identity'])
    if p0 and (not isinstance(registration['gpu_uuid'], str) or not re.fullmatch(
            'GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', registration['gpu_uuid'])):
        raise ValueError('one complete P0 GPU UUID required')
    if (type(uid) is not int or uid != 2147 or registration['uid'] != uid
            or type(registration['uid']) is not int
            or not isinstance(boot_id, str) or not re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id)
            or registration['boot_id'] != boot_id):
        raise ValueError('fresh authorized UID and host boot identity required')
    cid, image = registration['container_id'], registration['image_id']
    if (not isinstance(cid, str) or not re.fullmatch('[0-9a-f]{64}', cid)
            or not isinstance(image, str) or not re.fullmatch('sha256:[0-9a-f]{64}', image)):
        raise ValueError('complete immutable container and image IDs required')
    _utc(registration['container_created'])
    return identity


def _p0_mounts(run_root):
    if not isinstance(run_root, str):
        raise ValueError('P0 run root must be text')
    root = PurePosixPath(run_root)
    if (not isinstance(run_root, str) or not root.is_absolute() or str(root) != run_root
            or '..' in root.parts or root == PurePosixPath('/')
            or not re.fullmatch(r'/[A-Za-z0-9_./-]+', run_root) or run_root.startswith('//')):
        raise ValueError('canonical safe absolute P0 run root required')
    return [(run_root, True)] + [(str(root/name), False) for name in
        ('code','p0-spec.json','p0-plan.json','p0-worker-input.json','source-code-manifest.json')]


def _p0_environment(gpu_uuid):
    return dict(NVIDIA_VISIBLE_DEVICES=gpu_uuid,CUDA_VISIBLE_DEVICES='0',
        NVIDIA_DRIVER_CAPABILITIES='compute,utility',PYTHONDONTWRITEBYTECODE='1',
        OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1',NUMEXPR_NUM_THREADS='1',
        HOME='/tmp',XDG_CACHE_HOME='/tmp',TORCHINDUCTOR_CACHE_DIR='/tmp/torchinductor')


def _environment_digest(rows, overrides):
    if not isinstance(rows,list) or len(rows)>256:
        raise ValueError('bounded complete environment list required')
    env = {}
    for row in rows:
        if not isinstance(row,str) or len(row)>8192 or '=' not in row:
            raise ValueError('bounded environment assignment required')
        key,value = row.split('=',1)
        if not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',key) or key in env:
            raise ValueError('unique environment keys required')
        env[key] = value
    env.update(overrides)
    return hashlib.sha256(json.dumps(env,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def p0_image_environment_sha256(image_env, *, gpu_uuid):
    """Derive from the pinned image Config.Env, not a running container's env."""
    if not isinstance(gpu_uuid,str) or not re.fullmatch('GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',gpu_uuid):
        raise ValueError('one complete P0 GPU UUID required')
    return _environment_digest(image_env,_p0_environment(gpu_uuid))


def p0_container_command(identity, *, run_root, image_id, gpu_uuid, worker_input_sha256, environment_sha256):
    """Pure Docker-create argv. No registration/container-ID circular dependency."""
    from source_bundle import _p0_identity
    identity = _p0_identity(identity)
    if (not isinstance(image_id, str) or not re.fullmatch('sha256:[0-9a-f]{64}',image_id)
            or not isinstance(gpu_uuid, str) or not re.fullmatch('GPU-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',gpu_uuid)
            or not isinstance(worker_input_sha256, str) or not re.fullmatch('[0-9a-f]{64}',worker_input_sha256)
            or not isinstance(environment_sha256, str) or not re.fullmatch('[0-9a-f]{64}',environment_sha256)):
        raise ValueError('immutable P0 image, GPU and worker input digest required')
    mounts = _p0_mounts(run_root)
    root = PurePosixPath(run_root)
    command = ['create','--name',identity['run_id']+'-'+identity['holder'],'--pull=never',
        '--runtime=nvidia','--gpus','driver=nvidia,device='+gpu_uuid,'--user=2147:2147',
        '--restart=no','--network=none','--ipc=private','--memory=4g','--memory-swap=4g',
        '--cpus=2','--pids-limit=128','--shm-size=64m','--tmpfs','/tmp:rw,nosuid,nodev,size=16m','--cap-drop=ALL',
        '--security-opt=no-new-privileges=true','--workdir',run_root,'--entrypoint=python3']
    for key,value in dict(**{'sllm.kimi.'+key:value for key,value in identity.items()},
                           **{'sllm.purpose':P0_PURPOSE,'sllm.kimi.environment_sha256':environment_sha256}).items():
        command.extend(['--label',key+'='+value])
    for key,value in _p0_environment(gpu_uuid).items():
        command.extend(['--env',key+'='+value])
    for source,writable in mounts:
        command.extend(['--mount','type=bind,src='+source+',dst='+source+('' if writable else ',readonly')])
    command.extend([image_id,'-B','-E','-s','-X','utf8',str(root/'code/native_record_call.py'),'p0a',
        '--worker-input',str(root/'p0-worker-input.json'),'--worker-input-sha',worker_input_sha256])
    return command


def guard_target(registration, info, *, boot_id, uid):
    """Bind cleanup to an immutable Docker ID, creation event and owned labels."""
    identity = _guard_identity(registration, boot_id=boot_id, uid=uid)
    cid, image = registration['container_id'], registration['image_id']
    labels = info['Config']['Labels']
    expected = {'sllm.kimi.' + key: value for key, value in identity.items()}
    p0 = registration['schema'] == 'kimi-p0-guard-target-v2'
    expected['sllm.purpose'] = P0_PURPOSE if p0 else GUARD_PURPOSE
    if (info['Id'] != cid or info['Image'] != image or info['Created'] != registration['container_created']
            or info['Config']['User'] not in ('2147', '2147:2147')
            or not isinstance(labels, dict) or any(labels.get(k) != v for k, v in expected.items())
            or info['HostConfig']['RestartPolicy']['Name'] != 'no'
            or type(info['State']['Running']) is not bool
            or type(info['State']['Pid']) is not int
            or (info['State']['Running'] and info['State']['Pid'] <= 0)
            or (not info['State']['Running'] and info['State']['Pid'] != 0)):
        raise ValueError('guard container identity, owner or restart policy differs')
    if p0:
        host = info['HostConfig']
        requests = host.get('DeviceRequests')
        if (not isinstance(requests, list) or len(requests) != 1
                or requests[0].get('Driver') != 'nvidia' or requests[0].get('Count') != 0
                or requests[0].get('DeviceIDs') != [registration['gpu_uuid']]
                or requests[0].get('Capabilities') != [['gpu']]
                or requests[0].get('Options') != {}
                or type(host.get('Memory')) is not int or host['Memory'] != 4*1024**3
                or type(host.get('NanoCpus')) is not int or host['NanoCpus'] != 2*10**9
                or type(host.get('PidsLimit')) is not int or host['PidsLimit'] != 128
                or type(host.get('ShmSize')) is not int or host['ShmSize'] != 64*1024**2
                or host.get('Tmpfs') != {'/tmp':'rw,nosuid,nodev,size=16m'}
                or host.get('NetworkMode') != 'none' or host.get('Privileged') is not False
                or host.get('IpcMode') != 'private' or host.get('PidMode') != ''
                or host.get('Devices') not in ([], None) or host.get('CapAdd') not in ([], None)):
            raise ValueError('P0 one-GPU isolation or resource limits differ')
        command = p0_container_command(identity, run_root=registration['run_root'], image_id=image,
            gpu_uuid=registration['gpu_uuid'], worker_input_sha256=registration['worker_input_sha256'],
            environment_sha256=registration['environment_sha256'])
        if (info['Config'].get('Entrypoint') != ['python3']
                or info['Config'].get('Cmd') != command[command.index(image)+1:]
                or info['Config'].get('WorkingDir') != registration['run_root']
                or host.get('Runtime') != 'nvidia' or host.get('MemorySwap') != 4*1024**3
                or host.get('CapDrop') != ['ALL'] or host.get('SecurityOpt') != ['no-new-privileges=true']):
            raise ValueError('P0 immutable worker command or privilege policy differs')
        mounts = info.get('Mounts')
        expected_mounts = {(source,source,writable) for source,writable in _p0_mounts(registration['run_root'])}
        if (not isinstance(mounts, list) or len(mounts) != len(expected_mounts)
                or any(row.get('Type') != 'bind' or type(row.get('RW')) is not bool for row in mounts)
                or {(row.get('Source'),row.get('Destination'),row.get('RW')) for row in mounts} != expected_mounts):
            raise ValueError('P0 exact run/code/input mounts required')
        if (labels.get('sllm.kimi.environment_sha256') != registration['environment_sha256']
                or _environment_digest(info['Config'].get('Env'),{}) != registration['environment_sha256']):
            raise ValueError('P0 complete frozen container environment differs')
        env = dict(row.split('=',1) for row in info['Config']['Env'])
        if any(env.get(key)!=value for key,value in _p0_environment(registration['gpu_uuid']).items()):
            raise ValueError('P0 fixed GPU and thread environment differs')
    return identity


def guard_process(info):
    """Read this container's PID twice; never signal a PID or a process name."""
    if not info['State']['Running']:
        return None
    pid = info['State']['Pid']
    root = Path('/proc') / str(pid)
    def start_time():
        # comm may include spaces or parentheses; field 22 follows its last ')'.
        return int((root / 'stat').read_text().rsplit(')', 1)[1].split()[19])
    started = start_time()
    owner = root.stat().st_uid
    cgroup = (root / 'cgroup').read_text()
    cid = info['Id']
    if (owner != 2147 or started != start_time()
            or not any(re.search(r'/(?:docker/|docker-)' + cid + r'(?:\.scope)?(?:/|$)', line)
                       for line in cgroup.splitlines())):
        raise ValueError('container PID ownership, lifetime or cgroup differs')
    return dict(pid=pid, start_ticks=started, uid=owner, cgroup=cgroup)


def _guard_capture(command, timeout, environment):
    """POSIX host-only pipe reader: cap each output before retaining more bytes."""
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('finite positive CLI capture budget required')
    deadline = time.monotonic() + timeout
    work_deadline = deadline-min(0.25,timeout/4)
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=environment)
    buffers = [bytearray(), bytearray()]
    selector, failure, code = None, None, None
    def preserve_capture(error):
        error.guard_capture_stdout = bytes(buffers[0])
        error.guard_capture_stderr = bytes(buffers[1])
        error.guard_capture_child_pid = getattr(process,'pid',None)
        observed = getattr(process,'returncode',None)
        if observed is None:observed=code
        error.guard_capture_returncode = observed
        error.guard_capture_child_exit_verified = observed is not None
    try:
        selector = selectors.DefaultSelector()
        for index, stream in enumerate((process.stdout, process.stderr)):
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = work_deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in selector.select(remaining):
                block = os.read(key.fd, 4096)
                if not block:
                    selector.unregister(key.fileobj)
                elif len(buffers[key.data]) + len(block) > 65536:
                    raise ValueError('source guard Docker pipe exceeds 64KiB')
                else:
                    buffers[key.data].extend(block)
        remaining = work_deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        code = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(command, code, *(bytes(b).decode('utf-8', errors='strict') for b in buffers))
    except BaseException as error:
        failure = error
        try:
            process.kill()  # The guard's Docker CLI child only; no model/container inference.
            code=process.wait(timeout=max(0, min(1, deadline-time.monotonic())))
        except BaseException as cleanup_error:
            error.__notes__ = list(getattr(error, '__notes__', ())) + [
                'Docker CLI child cleanup also failed: ' + repr(cleanup_error)]
        preserve_capture(error)
        raise
    finally:
        closing_errors = []
        for owned in (selector, process.stdout, process.stderr):
            if owned is not None:
                try:
                    owned.close()
                except BaseException as close_error:
                    closing_errors.append(close_error)
        if closing_errors:
            if failure is None:
                preserve_capture(closing_errors[0])
                raise closing_errors[0]
            failure.__notes__ = list(getattr(failure, '__notes__', ())) + [
                'Docker CLI pipe cleanup failed: ' + repr(error) for error in closing_errors]


def _guard_docker(args, timeout):
    environment = dict(os.environ)
    for name in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        environment.pop(name, None)
    return _guard_capture(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', *args], timeout, environment)


def cleanup_guard_target(registration, *, boot_id, uid, timeout_seconds,
                         docker=_guard_docker, process_probe=guard_process):
    """Bounded cleanup kernel only; an independent host watchdog must call it."""
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError('finite remaining cleanup budget required')
    _guard_identity(registration, boot_id=boot_id, uid=uid)
    entered = time.monotonic()
    def command(*args):
        remaining = timeout_seconds - (time.monotonic() - entered)
        if remaining <= 0:
            raise TimeoutError('source guard cleanup budget exhausted')
        response = docker(list(args), remaining)
        if response.returncode:
            raise RuntimeError('source guard Docker command failed: ' + str(args[0]))
        if len(response.stdout) > 65536 or len(response.stderr) > 65536:
            raise ValueError('source guard Docker response exceeds metadata bound')
        return response.stdout
    cid = registration['container_id']
    rows = json.loads(command('inspect', cid))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError('exactly one guard container required')
    info = rows[0]
    guard_target(registration, info, boot_id=boot_id, uid=uid)
    process = process_probe(info)
    if info['State']['Running']:
        remaining = timeout_seconds - (time.monotonic() - entered)
        command('stop', '--time', str(max(0, int(remaining) - 5)), cid)
        rows = json.loads(command('inspect', cid))
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('stopped guard container evidence missing')
        guard_target(registration, rows[0], boot_id=boot_id, uid=uid)
        if rows[0]['State']['Running']:
            raise RuntimeError('guard container still running after stop')
    command('rm', cid)  # No force, wildcard, PID signal, volume or image deletion.
    remaining = command('ps', '-aq', '--no-trunc', '--filter', 'id=' + cid)
    if remaining.strip():
        raise RuntimeError('guard container remains after removal')
    if time.monotonic() - entered > timeout_seconds:
        raise TimeoutError('source guard cleanup returned past its budget')
    return dict(container_id=cid, process=process, container_absence_verified=True,
                resources_released=False, release_observation_verified=False,
                guard_deployed=False, runtime_admitted=False)


def _watchdog_pid(pid):
    if os.name != 'posix' or os.getuid() != 2147:
        raise ValueError('watchdog requires authorized UID2147 on the Linux host')
    if type(pid) is not int or pid <= 0:
        raise ValueError('positive host PID required')
    root = Path('/proc')/str(pid)
    def start():return int((root/'stat').read_text().rsplit(')', 1)[1].split()[19])
    started = start()
    result = dict(uid=root.stat().st_uid, boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        pid=pid, start_ticks=started, pid_namespace=(root / 'ns/pid').stat().st_ino,
        cgroup=(root / 'cgroup').read_text())
    if started != start():
        raise ValueError('watchdog PID was reused during probe')
    return result


def _watchdog_host():
    return _watchdog_pid(os.getpid())


def _probe_peer(connection):
    if not sys.platform.startswith('linux'):
        raise ValueError('watchdog peer credentials require Linux')
    pid, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    return pid, uid


def _probe_listener(nonce):
    if not sys.platform.startswith('linux') or not re.fullmatch('[0-9a-f]{32}', nonce):
        raise ValueError('Linux abstract socket and fresh nonce required')
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        listener.bind('\0sllm-kimi-'+nonce)
        listener.listen(4)
        return listener
    except BaseException as error:
        try:
            listener.close()
        except BaseException as close_error:
            error.__notes__ = list(getattr(error, '__notes__', ())) + ['probe listener cleanup failed: '+repr(close_error)]
        raise


def _probe_receive(connection, deadline, limit):
    raw = bytearray()
    while True:
        remaining = deadline-time.monotonic()
        if remaining <= 0:
            raise TimeoutError('watchdog probe deadline exhausted')
        connection.settimeout(remaining)
        block = connection.recv(min(1024, limit+1-len(raw)))
        if not block:
            raise ValueError('watchdog probe closed before complete message')
        raw.extend(block)
        if len(raw) > limit:
            raise ValueError('watchdog probe message exceeds bound')
        if b'\n' in raw:
            if not raw.endswith(b'\n') or raw.count(b'\n') != 1:
                raise ValueError('exactly one watchdog probe message required')
            return json.loads(raw)


def _serve_guard_probe(listener, target, host, nonce, fire, remaining, valid, *, peer_probe=_probe_peer):
    deadline = time.monotonic()+min(0.25, remaining)
    connection = None
    try:
        listener.settimeout(max(0, deadline-time.monotonic()))
        connection, _ = listener.accept()
        if peer_probe(connection)[1] != 2147:
            return False
        request = _probe_receive(connection, deadline, 4096)
        if (not isinstance(request, dict) or set(request) != {'schema','nonce','challenge'}
                or request['schema'] != 'kimi-watchdog-probe-request-v1' or request['nonce'] != nonce
                or not isinstance(request['challenge'], str) or not re.fullmatch('[0-9a-f]{32}', request['challenge'])
                or not valid()):
            return False
        response = dict(schema='kimi-watchdog-probe-response-v1', identity=target['identity'], nonce=nonce,
            challenge=request['challenge'], host=host, container_id=target['container_id'],
            image_id=target['image_id'], container_created=target['container_created'],
            guard_fire_by_utc=fire.isoformat(), runtime_admitted=False)
        raw = (json.dumps(response, allow_nan=False)+'\n').encode()
        if len(raw) > 8192 or not valid() or time.monotonic() >= deadline:
            return False
        connection.settimeout(deadline-time.monotonic())
        connection.sendall(raw)
        return True
    except (OSError, ValueError, struct.error):
        return False  # Invalid/slow probe clients do not cancel or extend the guard.
    finally:
        if connection is not None:connection.close()


def _exchange_guard_probe(connection, target, nonce, expected_host, fire, deadline, *,
                          peer_probe=_probe_peer, pid_probe=_watchdog_pid):
    challenge = secrets.token_hex(16)
    peer_pid, peer_uid = peer_probe(connection)
    if peer_uid != 2147 or peer_pid != expected_host['pid']:
        raise ValueError('watchdog socket peer differs from registered host process')
    remaining = deadline-time.monotonic()
    if remaining <= 0:raise TimeoutError('watchdog probe deadline exhausted')
    connection.settimeout(remaining)
    request = dict(schema='kimi-watchdog-probe-request-v1', nonce=nonce, challenge=challenge)
    connection.sendall((json.dumps(request)+'\n').encode())
    response = _probe_receive(connection, deadline, 8192)
    expected = dict(schema='kimi-watchdog-probe-response-v1', identity=target['identity'], nonce=nonce,
        challenge=challenge, host=expected_host, container_id=target['container_id'], image_id=target['image_id'],
        container_created=target['container_created'], guard_fire_by_utc=fire.isoformat(), runtime_admitted=False)
    if response != expected or pid_probe(peer_pid) != expected_host:
        raise ValueError('watchdog challenge, source/target or live PID lifetime differs')
    if time.monotonic() >= deadline or datetime.now(timezone.utc) >= fire:
        raise TimeoutError('watchdog probe or guard fire deadline exhausted')
    return dict(response, probe_exchange_verified=True, independent_service_verified=False,
                guard_deployed=False, resources_released=False)


def probe_watchdog(target, *, nonce, expected_host, host_pid_namespace, guard_fire_by_utc, timeout_seconds=2):
    if not sys.platform.startswith('linux'):
        raise ValueError('watchdog active probe requires the Linux host')
    _guard_identity(target, boot_id=expected_host['boot_id'], uid=expected_host['uid'])
    if (not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce)
            or type(host_pid_namespace) is not int or host_pid_namespace <= 0
            or any(type(expected_host[k]) is not int or expected_host[k] <= 0 for k in ('pid', 'start_ticks'))
            or expected_host['pid_namespace'] != host_pid_namespace or target['container_id'] in expected_host['cgroup']
            or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 2):
        raise ValueError('bounded fresh watchdog probe identity required')
    fire = _utc(guard_fire_by_utc)
    budget = min(timeout_seconds, (fire-datetime.now(timezone.utc)).total_seconds())
    if budget <= 0:raise TimeoutError('watchdog guard already due')
    deadline = time.monotonic()+budget
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(budget)
        connection.connect('\0sllm-kimi-'+nonce)
        return _exchange_guard_probe(connection, target, nonce, expected_host, fire, deadline)


def read_watchdog_ready(path, target, *, nonce, registration_sha256, plan_sha256, guard_fire_by_utc):
    from vllm_export_hook import _metadata_stream
    with _metadata_stream(Path(path)) as stream:
        raw = stream.read(16385)
    if len(raw) > 16384 or not raw.endswith(b'\n'):
        raise ValueError('watchdog readiness is oversized or partial')
    lines = raw.splitlines()
    if len(lines) != 2 or any(len(line)+1 > 8192 for line in lines):
        raise ValueError('watchdog is not exclusively in the ready state')
    rows = [json.loads(line) for line in lines]
    for row, event in zip(rows, ('inputs_verified', 'ready')):
        if (not isinstance(row, dict) or row.get('schema') != 'kimi-source-watchdog-events-v1'
                or row.get('nonce') != nonce or row.get('event') != event
                or any(row.get(k) != v for k,v in target['identity'].items())):
            raise ValueError('watchdog readiness identity differs')
        _utc(row['utc'])
    if rows[0]['payload'] != dict(registration_sha256=registration_sha256, plan_sha256=plan_sha256,
                                  runtime_admitted=False):
        raise ValueError('watchdog verified inputs differ')
    ready = rows[1]['payload']
    if (ready.get('container_id') != target['container_id'] or ready.get('image_id') != target['image_id']
            or ready.get('container_created') != target['container_created']
            or _utc(ready['guard_fire_by_utc']) != _utc(guard_fire_by_utc)
            or ready.get('probe_endpoint') != '@sllm-kimi-'+nonce or ready.get('runtime_admitted') is not False):
        raise ValueError('watchdog ready target or live probe differs')
    return ready


def _watchdog_event(stream, identity, nonce, event, payload):
    raw = (json.dumps(dict(identity, schema='kimi-source-watchdog-events-v1', nonce=nonce,
        event=event, utc=datetime.now(timezone.utc).isoformat(), payload=payload), allow_nan=False)+'\n').encode()
    if len(raw) > 8192:
        raise ValueError('watchdog event exceeds 8KiB')
    if stream.write(raw) != len(raw):
        raise OSError('short watchdog event write')
    stream.flush()
    os.fsync(stream.fileno())


def source_watchdog(target, schedule, *, nonce, host_pid_namespace, events,
                    docker=_guard_docker, process_probe=guard_process, host_probe=_watchdog_host,
                    probe_factory=None):
    """Independent loop candidate; caller binds source/plan and reserves its log."""
    host = host_probe()
    identity = _guard_identity(target, boot_id=host['boot_id'], uid=host['uid'])
    if (not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce)
            or type(host_pid_namespace) is not int or host_pid_namespace <= 0
            or host['pid_namespace'] != host_pid_namespace or target['container_id'] in host['cgroup']):
        raise ValueError('fresh nonce and host PID namespace outside target required')
    anchored_utc, anchored_mono = datetime.now(timezone.utc), time.monotonic()
    fire = _utc(schedule['guard_fire_by_utc'])
    shutdown_end = _utc(schedule['phase_deadlines_utc']['worker_shutdown'])
    original_wait = (fire-anchored_utc).total_seconds()
    if anchored_utc < fire-timedelta(seconds=schedule['compute_max_seconds']):
        raise ValueError('watchdog batch anchor is in the future')
    shutdown_span = (shutdown_end-fire).total_seconds()
    if not 0 < shutdown_span <= 86400:
        raise ValueError('finite reviewed shutdown span required')
    def query(*args):
        response = docker(list(args), min(5, shutdown_span))
        if response.returncode or len(response.stdout) > 65536 or len(response.stderr) > 65536:
            raise RuntimeError('watchdog Docker query failed: '+args[0])
        return response.stdout
    verified, cleanup_attempted, late_start, cleanup_result = False, False, False, None
    listener, failure = None, None
    def cleanup_owned(check_absence):
        nonlocal cleanup_attempted
        present = query('ps', '-aq', '--no-trunc', '--filter', 'id='+target['container_id']) if check_absence else target['container_id']
        budget = min(shutdown_span, (shutdown_end-datetime.now(timezone.utc)).total_seconds(),
                     (shutdown_end-anchored_utc).total_seconds()-(time.monotonic()-anchored_mono))
        missed = budget <= 0
        cleanup_attempted = True
        if present.strip():
            # A missed deadline permits only one bounded emergency cleanup, never more computation.
            result = cleanup_guard_target(target, boot_id=host['boot_id'], uid=host['uid'],
                timeout_seconds=min(5, shutdown_span) if missed else budget,
                docker=docker, process_probe=process_probe)
        else:
            result = dict(container_id=target['container_id'], container_absence_verified=True,
                          resources_released=False, release_observation_verified=False,
                          guard_deployed=False, runtime_admitted=False)
        result['deadline_missed'] = (missed or datetime.now(timezone.utc) > shutdown_end
            or time.monotonic()-anchored_mono > (shutdown_end-anchored_utc).total_seconds())
        result['late_start'] = late_start
        result['emergency_cleanup_budget_seconds'] = min(5, shutdown_span) if missed else 0
        return result
    try:
        rows = json.loads(query('inspect', target['container_id']))
        if not isinstance(rows, list) or len(rows) != 1:
            raise ValueError('watchdog initial container identity missing')
        guard_target(target, rows[0], boot_id=host['boot_id'], uid=host['uid'])
        verified = True
        process = process_probe(rows[0])
        event = 'ready' if (original_wait > time.monotonic()-anchored_mono
                            and datetime.now(timezone.utc) < fire) else 'late_start'
        late_start = event == 'late_start'
        if not late_start and probe_factory is not None:
            listener = probe_factory(nonce)
            if time.monotonic()-anchored_mono >= original_wait or datetime.now(timezone.utc) >= fire:
                event, late_start = 'late_start', True
        _watchdog_event(events, identity, nonce, event, dict(host=host, target_process=process,
            container_id=target['container_id'], image_id=target['image_id'], container_created=target['container_created'],
            probe_endpoint='@sllm-kimi-'+nonce if listener is not None else None,
            guard_fire_by_utc=fire.isoformat(), runtime_admitted=False))
        reason = 'deadline'
        while True:
            now, elapsed = datetime.now(timezone.utc), time.monotonic()-anchored_mono
            if now < anchored_utc or elapsed < 0:
                reason = 'clock_regressed'
                break
            remaining = min((fire-now).total_seconds(), original_wait-elapsed)
            if remaining <= 0:
                break
            if listener is None:
                time.sleep(min(1, remaining))
            else:
                def valid():
                    wall, mono = datetime.now(timezone.utc), time.monotonic()-anchored_mono
                    return anchored_utc <= wall < fire and 0 <= mono < original_wait
                _serve_guard_probe(listener, target, host, nonce, fire, remaining, valid)
        if listener is not None:
            listener.close()
            listener = None
        _watchdog_event(events, identity, nonce, 'cleanup_started', dict(reason=reason))
        result = cleanup_result = cleanup_owned(True)
        if result['deadline_missed'] or late_start or reason != 'deadline':
            error = TimeoutError('watchdog deadline or clock failed; cleanup evidence retained')
            error.guard_cleanup_receipt = result
            raise error
        _watchdog_event(events, identity, nonce, 'cleanup_returned', result)
        return result
    except BaseException as error:
        failure = error
        if listener is not None:
            try:
                listener.close()
                listener = None
            except BaseException as close_error:
                error.__notes__ = list(getattr(error, '__notes__', ())) + ['watchdog probe close before cleanup failed: '+repr(close_error)]
        if verified and not cleanup_attempted:
            try:
                cleanup_result = cleanup_owned(False)
            except BaseException as cleanup_error:
                error.__notes__ = list(getattr(error, '__notes__', ())) + ['watchdog cleanup also failed: '+repr(cleanup_error)]
        if cleanup_result is not None:
            error.guard_cleanup_receipt = cleanup_result
        try:
            description = repr(error)
            notes = '\n'.join(getattr(error, '__notes__', ()))
            _watchdog_event(events, identity, nonce, 'failed', dict(error=description[:2048],
                error_truncated=len(description) > 2048, notes=notes[:2048], notes_truncated=len(notes) > 2048,
                cleanup=getattr(error, 'guard_cleanup_receipt', None)))
        except BaseException as log_error:
            error.__notes__ = list(getattr(error, '__notes__', ())) + ['watchdog evidence failed: '+repr(log_error)]
        raise
    finally:
        if listener is not None:
            try:
                listener.close()
            except BaseException as close_error:
                if failure is None:raise
                failure.__notes__ = list(getattr(failure, '__notes__', ())) + ['watchdog probe close failed: '+repr(close_error)]


def watchdog_service_plan(target, registration_path, registration_sha256, schedule, *, now):
    """Render only; this is neither service installation nor runtime admission."""
    identity = _guard_identity(target, boot_id=target['boot_id'], uid=target['uid'])
    if not isinstance(registration_path, str):
        raise ValueError('canonical safe absolute Linux registration path and SHA required')
    path = PurePosixPath(registration_path)
    if (not path.is_absolute() or registration_path.startswith('//')
            or '..' in path.parts or not re.fullmatch(r'/[A-Za-z0-9_./-]+', registration_path)
            or str(path) != registration_path or path.parent == PurePosixPath('/')
            or not isinstance(registration_sha256, str) or not re.fullmatch('[0-9a-f]{64}', registration_sha256)):
        raise ValueError('canonical safe absolute Linux registration path and SHA required')
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone-qualified service plan time required')
    now = now.astimezone(timezone.utc)
    fire = _utc(schedule['guard_fire_by_utc'])
    cleanup = _utc(schedule['cleanup_complete_by_utc'])
    if not fire-timedelta(seconds=schedule['compute_max_seconds']) <= now < fire:
        raise TimeoutError('service plan outside original batch compute window')
    # Reserve a finite installation interval and systemd stop grace inside this batch.
    latest_start = min(now+timedelta(seconds=10), fire)
    runtime = math.floor((cleanup-latest_start).total_seconds())-10-5
    if runtime <= 0:
        raise TimeoutError('service plan lacks original cleanup budget')
    root = path.parent
    unit = identity['run_id']+'-guard-'+identity['holder']+'.service'
    command = ['/usr/bin/systemd-run', '--system', '--unit='+unit, '--uid=2147',
        '--property=Type=exec', '--property=Slice=system.slice', '--property=Restart=no',
        '--property=KillMode=control-group', '--property=UMask=0077',
        '--property=MemoryMax=256M', '--property=CPUQuota=100%', '--property=TasksMax=64',
        '--property=TimeoutStartSec=10', '--property=TimeoutStopSec=5',
        '--property=RuntimeMaxSec='+str(runtime), '--working-directory='+str(root),
        '/usr/bin/python3', '-B', '-E', '-s', str(root/'code/source_schedule.py'), 'guard',
        '--registration', str(path), '--registration-sha', registration_sha256]
    if target['schema'] == 'kimi-p0-guard-target-v2':
        command.insert(2, '--no-ask-password')
    return dict(schema='kimi-source-watchdog-service-plan-v1', identity=identity, unit=unit,
        command=command, generated_utc=now.isoformat(), launch_before_utc=latest_start.isoformat(),
        cleanup_complete_by_utc=cleanup.isoformat(), max_runtime_seconds=runtime,
        startup_grace_seconds=10, stop_grace_seconds=5,
        system_manager_authorization_required=True, deployment_performed=False,
        independent_service_verified=False, guard_deployed=False, runtime_admitted=False)


SERVICE_PROPERTIES = ('Id', 'LoadState', 'ActiveState', 'SubState', 'MainPID', 'ControlGroup', 'Transient',
    'User', 'Type', 'Restart', 'Slice', 'WorkingDirectory', 'KillMode', 'UMask', 'MemoryMax',
    'CPUQuotaPerSecUSec', 'TasksMax', 'TimeoutStartUSec', 'TimeoutStopUSec', 'RuntimeMaxUSec',
    'ExecMainStartTimestamp', 'ActiveEnterTimestamp', 'InvocationID')


def _guard_systemctl(unit, timeout):
    return _guard_capture(['/usr/bin/systemctl', '--system', '--no-pager', '--timestamp=us+utc',
        'show', unit, '--property='+','.join(SERVICE_PROPERTIES)], timeout,
        {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'TZ': 'UTC', 'SYSTEMD_COLORS': '0'})


def _watchdog_command(pid):
    from vllm_export_hook import _metadata_stream
    root = Path('/proc')/str(pid)
    with _metadata_stream(root/'cmdline') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096 or not raw.endswith(b'\0'):
        raise ValueError('bounded watchdog command line required')
    executable, expected = (root/'exe').stat(), Path('/usr/bin/python3').stat()
    if (executable.st_dev, executable.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError('watchdog executable differs from host Python')
    return dict(argv=[arg.decode('utf-8') for arg in raw[:-1].split(b'\0')],
                executable_device=executable.st_dev, executable_inode=executable.st_ino)


def _service_seconds(value):
    compact = value.replace(' ', '')
    parts = re.findall(r'([0-9]+)(min|d|h|s)', compact)
    if not parts or ''.join(number+unit for number, unit in parts) != compact:
        raise ValueError('unsupported finite integer-second systemd duration')
    units = {'d': 86400, 'h': 3600, 'min': 60, 's': 1}
    return sum(int(number)*units[unit] for number, unit in parts)


def probe_watchdog_service(service_plan, target, ready, schedule, *, nonce, host_pid_namespace,
                           guard_fire_by_utc, timeout_seconds=5, query=_guard_systemctl,
                           pid_probe=_watchdog_pid, command_probe=_watchdog_command,
                           active_probe=probe_watchdog):
    """Read-only live service/PID/probe cross-check, never authorizes computation."""
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 5:
        raise ValueError('finite service probe budget at most five seconds required')
    deadline = time.monotonic()+timeout_seconds
    derived = watchdog_service_plan(target, service_plan['command'][-3], service_plan['command'][-1],
                                    schedule, now=_utc(service_plan['generated_utc']))
    if (json.dumps(service_plan, sort_keys=True, allow_nan=False) != json.dumps(derived, sort_keys=True, allow_nan=False)
            or _utc(guard_fire_by_utc) != _utc(schedule['guard_fire_by_utc'])):
        raise ValueError('derived service plan or original guard deadline differs')
    def remaining():
        value = min(deadline-time.monotonic(), (_utc(guard_fire_by_utc)-datetime.now(timezone.utc)).total_seconds())
        if value <= 0:raise TimeoutError('service probe or original guard deadline exhausted')
        return value
    host, unit = ready['host'], service_plan['unit']
    identity = _guard_identity(target, boot_id=host['boot_id'], uid=host['uid'])
    if (service_plan['identity'] != identity
            or unit != identity['run_id']+'-guard-'+identity['holder']+'.service'):
        raise ValueError('source service plan identity differs')
    root = str(PurePosixPath(service_plan['command'][-3]).parent)
    expected = dict(Id=unit, LoadState='loaded', ActiveState='active', SubState='running',
        User='2147', Type='exec', Restart='no', Slice='system.slice', WorkingDirectory=root,
        KillMode='control-group', UMask='0077', MemoryMax=str(256*1024**2), TasksMax='64',
        MainPID=str(host['pid']), ControlGroup='/system.slice/'+unit, Transient='yes')
    def inspect_service():
        response = query(unit, remaining())
        remaining()
        if response.returncode or len(response.stdout) > 65536 or len(response.stderr) > 65536:
            raise RuntimeError('bounded system service query failed')
        properties = {}
        for line in response.stdout.splitlines():
            name, separator, value = line.partition('=')
            if not separator or name in properties:raise ValueError('duplicate or malformed service properties')
            properties[name] = value
        if (set(properties) != set(SERVICE_PROPERTIES)
                or any(properties.get(k) != v for k, v in expected.items())
                or not re.fullmatch('[0-9a-f]{32}', properties['InvocationID'])):
            raise ValueError('actual system service identity, state or resources differ')
        for field, seconds in (('CPUQuotaPerSecUSec', 1), ('TimeoutStartUSec', 10),
                               ('TimeoutStopUSec', 5), ('RuntimeMaxUSec', service_plan['max_runtime_seconds'])):
            if _service_seconds(properties[field]) != seconds:
                raise ValueError('actual system service time or CPU limit differs')
        def stamp(name):
            return datetime.strptime(properties[name][4:], '%Y-%m-%d %H:%M:%S.%f UTC').replace(tzinfo=timezone.utc)
        started, active = stamp('ExecMainStartTimestamp'), stamp('ActiveEnterTimestamp')
        if (not _utc(service_plan['generated_utc']) <= started <= active <= datetime.now(timezone.utc)
                or active > _utc(service_plan['launch_before_utc'])+timedelta(seconds=10)
                or active+timedelta(seconds=service_plan['max_runtime_seconds']+5) > _utc(service_plan['cleanup_complete_by_utc'])):
            raise ValueError('actual service activation exceeds original cleanup budget')
        return properties
    before = inspect_service()
    process = pid_probe(host['pid'])
    remaining()
    command = command_probe(host['pid'])
    remaining()
    if (process != host or not any(line.split(':', 2)[-1] == expected['ControlGroup']
            for line in process['cgroup'].splitlines()) or command['argv'] != service_plan['command'][-10:]):
        raise ValueError('actual watchdog process, command or unit cgroup differs')
    proof = active_probe(target, nonce=nonce, expected_host=host, host_pid_namespace=host_pid_namespace,
        guard_fire_by_utc=guard_fire_by_utc, timeout_seconds=min(2, remaining()))
    if (not isinstance(proof, dict) or proof.get('probe_exchange_verified') is not True
            or proof.get('host') != host or proof.get('identity') != identity
            or proof.get('nonce') != nonce or proof.get('container_id') != target['container_id']
            or proof.get('image_id') != target['image_id'] or proof.get('container_created') != target['container_created']
            or proof.get('guard_fire_by_utc') != guard_fire_by_utc or proof.get('runtime_admitted') is not False):
        raise ValueError('active service probe proof differs')
    after = inspect_service()
    after_process = pid_probe(host['pid'])
    remaining()
    after_command = command_probe(host['pid'])
    if after != before or after_process != process or after_command != command:
        raise ValueError('system service or process changed during active probe')
    remaining()
    return dict(proof, service_unit=unit, service_invocation_id=before['InvocationID'],
        independent_service_verified=True, service_submission_deadline_verified=False,
        guard_deployed=False, runtime_admitted=False, resources_released=False)


def _await_watchdog_ready(path, target, *, remaining, **options):
    from vllm_export_hook import _metadata_stream
    while True:
        remaining()
        try:
            with _metadata_stream(path) as stream:
                raw = stream.read(16385)
        except FileNotFoundError:
            raw = b''
        remaining()
        if len(raw) > 16384:
            raise ValueError('watchdog readiness exceeds 16KiB')
        if raw.endswith(b'\n') and len(raw.splitlines()) >= 2:
            return read_watchdog_ready(path, target, **options)
        time.sleep(min(0.05, remaining()))


def create_p0_target(worker_input_path, worker_input_sha256, *, events,
                     docker=_guard_docker, host_probe=_watchdog_host):
    """Create metadata only, never start. Timeout leaves an explicitly owed cleanup."""
    from source_bundle import verify_p0_worker_input
    entered,started = datetime.now(timezone.utc),time.monotonic()
    state = dict(create_attempted=False,create_outcome='not_attempted',container_id=None,
        stopped_target_verified=False,image_environment_verified=False,runtime_abi_verified=False,cleanup_owed=False,
        runtime_admitted=False,resources_released=False)
    identity = None
    try:
        _,root,inputs,receipt,schedule,identity = verify_p0_worker_input(worker_input_path,worker_input_sha256)
        if entered<_utc(inputs['batch_entered_utc']):
            raise TimeoutError('P0 create batch anchor is still in the future')
        if Path(__file__).resolve(strict=True) != root/'code/source_schedule.py':
            raise ValueError('create must execute this run verified source bundle')
        deadline = started + min(30,(_utc(schedule['phase_deadlines_utc']['preflight'])-entered).total_seconds())
        def remaining():
            now = datetime.now(timezone.utc)
            if now<entered:
                raise TimeoutError('P0 create wall clock regressed')
            value = min(deadline-time.monotonic(),(_utc(schedule['phase_deadlines_utc']['preflight'])-now).total_seconds())
            if value<=0:
                raise TimeoutError('P0 create or original preflight deadline exhausted')
            return value
        remaining()
        host = host_probe()
        remaining()
        if (type(host['uid']) is not int or host['uid']!=2147
                or not re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',host['boot_id'])
                or type(host['pid_namespace']) is not int or host['pid_namespace']<=0
                or re.search(r'/(docker/|docker-|kubepods)',host['cgroup'])
                or socket.gethostname() not in ('qjhs3','qjhs003')):
            raise ValueError('P0 create requires this authorized Linux host identity')
        command = p0_container_command(identity,run_root=str(root),image_id=receipt['image_id'],
            gpu_uuid=receipt['gpu_uuid'],worker_input_sha256=worker_input_sha256,
            environment_sha256=receipt['environment_sha256'])
        name = identity['run_id']+'-'+identity['holder']
        state.update(container_name=name,image_id=receipt['image_id'],worker_input_sha256=worker_input_sha256,
                     environment_sha256=receipt['environment_sha256'])
        def inspect(args):
            response = docker(args,min(10,remaining()))
            remaining()
            if response.returncode or len(response.stdout)>65536 or len(response.stderr)>65536:
                raise RuntimeError('P0 metadata query failed')
            return response
        response = inspect(['ps','--all','--no-trunc','--filter','name=^/'+name+'$','--format','{{.ID}}'])
        if response.stdout.strip():
            raise ValueError('P0 container name already exists; no replacement or deletion')
        rows = json.loads(inspect(['image','inspect',receipt['image_id']]).stdout)
        if not isinstance(rows,list) or len(rows)!=1 or rows[0].get('Id')!=receipt['image_id']:
            raise ValueError('pinned P0 image identity differs')
        if rows[0]['Config'].get('Volumes'):
            raise ValueError('P0 image declares unregistered volumes; no implicit copies or volume allocation allowed')
        actual_env = p0_image_environment_sha256(rows[0]['Config'].get('Env'),gpu_uuid=receipt['gpu_uuid'])
        if actual_env!=receipt['environment_sha256']:
            raise ValueError('actual pinned image environment differs from frozen worker input')
        state['image_environment_verified'] = True
        _watchdog_event(events,identity,identity['holder'],'create_prepared',dict(state,boot_id=host['boot_id'],
            host_pid_namespace=host['pid_namespace'],original_preflight_deadline_utc=schedule['phase_deadlines_utc']['preflight']))
        timeout = min(10,remaining())
        state.update(create_attempted=True,create_outcome='unknown',cleanup_owed=True,create_requested_utc=datetime.now(timezone.utc).isoformat())
        response = docker(command,timeout)
        # Preserve a returned ID even when its client response exhausted preflight.
        if response.returncode==0 and len(response.stdout)<=65536:
            cid = response.stdout.strip()
            if re.fullmatch('[0-9a-f]{64}',cid):
                state.update(container_id=cid,create_outcome='id_returned')
        remaining()
        if response.returncode or len(response.stdout)>65536 or len(response.stderr)>65536:
            raise RuntimeError('P0 create failed; exact name outcome requires inspection, never automatic retry')
        cid = response.stdout.strip()
        if not re.fullmatch('[0-9a-f]{64}',cid):
            raise ValueError('P0 create did not return a complete container ID')
        state.update(container_id=cid,create_outcome='id_returned')
        rows = json.loads(inspect(['inspect',cid]).stdout)
        if not isinstance(rows,list) or len(rows)!=1:
            raise ValueError('exact created P0 container required')
        info = rows[0]
        target = dict(schema='kimi-p0-guard-target-v2',identity=identity,boot_id=host['boot_id'],uid=2147,
            container_id=cid,image_id=receipt['image_id'],container_created=info['Created'],
            gpu_uuid=receipt['gpu_uuid'],run_root=str(root),worker_input_sha256=worker_input_sha256,
            environment_sha256=receipt['environment_sha256'])
        guard_target(target,info,boot_id=host['boot_id'],uid=2147)
        if (info['State']['Running'] or info.get('Name')!='/'+name
                or not _utc(state['create_requested_utc'])<=_utc(info['Created'])<=datetime.now(timezone.utc)):
            raise ValueError('created P0 target ran unexpectedly or has stale creation identity')
        state.update(stopped_target_verified=True,create_outcome='stopped_verified')
        result = dict(state,target=target,host_pid_namespace=host['pid_namespace'])
        _watchdog_event(events,identity,identity['holder'],'create_verified',result)
        remaining()
        return result
    except BaseException as error:
        error.p0_create_receipt = state
        if identity is not None:
            try:_watchdog_event(events,identity,identity['holder'],'create_failed',dict(state,error_type=type(error).__name__,error=str(error)[:1024]))
            except BaseException as evidence_error:
                error.p0_create_evidence_error = dict(error_type=type(evidence_error).__name__,error=str(evidence_error)[:1024])
        raise


def materialize_p0_guard_inputs(worker_input_path, worker_input_sha256, create_events_sha256, *, events,
                                docker=_guard_docker, host_probe=_watchdog_host):
    """Publish guard inputs only after rechecking the stopped owned target."""
    from source_bundle import verify_p0_worker_input
    from vllm_export_hook import _metadata_stream
    from native_index import _publish
    entered,started = datetime.now(timezone.utc),time.monotonic()
    state = dict(container_id=None,target_reverified=False,artifacts={},pending_partial_cleanup=[],
        guard_deployed=False,creation_cli_exit_verified=False,runtime_abi_verified=False,
        runtime_admitted=False,resources_released=False,cleanup_owed=True)
    identity,nonce = None,None
    try:
        _,root,inputs,receipt,schedule,identity = verify_p0_worker_input(worker_input_path,worker_input_sha256)
        if Path(__file__).resolve(strict=True) != root/'code/source_schedule.py':
            raise ValueError('guard inputs must be prepared from this run verified source bundle')
        if entered<_utc(inputs['batch_entered_utc']):
            raise TimeoutError('P0 guard-input batch anchor is still in the future')
        deadline = started + min(30,(_utc(schedule['phase_deadlines_utc']['guard_setup'])-entered).total_seconds())
        def remaining():
            now = datetime.now(timezone.utc)
            if now<entered:
                raise TimeoutError('P0 guard-input wall clock regressed')
            value = min(deadline-time.monotonic(),(_utc(schedule['phase_deadlines_utc']['guard_setup'])-now).total_seconds())
            if value<=0:
                raise TimeoutError('P0 guard-input or original guard-setup deadline exhausted')
            return value
        remaining()
        if not isinstance(create_events_sha256,str) or not re.fullmatch('[0-9a-f]{64}',create_events_sha256):
            raise ValueError('external P0 creation-journal SHA required')
        with _metadata_stream(root/'p0-create-events.jsonl') as stream:
            raw = stream.read(16385)
        remaining()
        if len(raw)>16384 or not raw.endswith(b'\n') or hashlib.sha256(raw).hexdigest()!=create_events_sha256:
            raise ValueError('P0 creation-journal length, completeness or SHA differs')
        rows = [json.loads(line) for line in raw.splitlines()]
        if len(rows)!=2:
            raise ValueError('only the successful two-event creation path can prepare guard inputs')
        for row,stage in zip(rows,('create_prepared','create_verified')):
            if (row.get('schema')!='kimi-source-watchdog-events-v1' or row.get('event')!=stage
                    or row.get('nonce')!=identity['holder'] or any(row.get(k)!=v for k,v in identity.items())):
                raise ValueError('P0 creation-journal source identity or stages differ')
        before,created = (row['payload'] for row in rows)
        target = created['target']
        state['container_id'] = target['container_id']
        name = identity['run_id']+'-'+identity['holder']
        if (before.get('create_attempted') is not False or created.get('create_attempted') is not True
                or created.get('create_outcome')!='stopped_verified' or created.get('stopped_target_verified') is not True
                or created.get('image_environment_verified') is not True or created.get('cleanup_owed') is not True
                or created.get('runtime_admitted') is not False or created.get('resources_released') is not False
                or target.get('identity')!=identity or target.get('image_id')!=receipt['image_id']
                or target.get('gpu_uuid')!=receipt['gpu_uuid'] or target.get('run_root')!=str(root)
                or target.get('worker_input_sha256')!=worker_input_sha256
                or target.get('environment_sha256')!=receipt['environment_sha256']
                or created.get('container_id')!=target['container_id'] or created.get('container_name')!=name
                or not _utc(inputs['batch_entered_utc'])<=_utc(rows[0]['utc'])<=_utc(created['create_requested_utc'])
                    <=_utc(target['container_created'])<=_utc(rows[1]['utc'])<=entered):
            raise ValueError('P0 creation provenance, stopped target or original times differ')
        outputs = ('registration.json','source-watchdog-service-plan.json')
        if any((root/name).exists() or (root/name).is_symlink() for name in (*outputs,*(name+'.partial' for name in outputs))):
            raise FileExistsError('guard inputs or retained partials already exist; no overwrite or retry')
        host = host_probe()
        remaining()
        _guard_identity(target,boot_id=host['boot_id'],uid=host['uid'])
        if (host['pid_namespace']!=created['host_pid_namespace'] or host['pid_namespace']!=before['host_pid_namespace']
                or host['boot_id']!=before['boot_id'] or re.search(r'/(docker/|docker-|kubepods)',host['cgroup'])
                or socket.gethostname() not in ('qjhs3','qjhs003')):
            raise ValueError('P0 creation host boot or PID namespace no longer matches')
        def recheck():
            state['target_reverified'] = False
            response = docker(['inspect',target['container_id']],min(5,remaining()))
            remaining()
            if response.returncode or len(response.stdout)>65536 or len(response.stderr)>65536:
                raise RuntimeError('P0 guard-input target inspection failed')
            found = json.loads(response.stdout)
            if not isinstance(found,list) or len(found)!=1:
                raise ValueError('exact P0 guard-input target required')
            guard_target(target,found[0],boot_id=host['boot_id'],uid=host['uid'])
            if found[0]['State']['Running'] or found[0].get('Name')!='/'+name:
                raise ValueError('P0 guard inputs require the same stopped named target')
            state['target_reverified'] = True
        recheck()
        nonce = secrets.token_hex(16)
        registration = dict(schema='kimi-p0-watchdog-registration-v1',target=target,
            plan_sha256=inputs['plan_sha256'],batch_entered_utc=inputs['batch_entered_utc'],
            host_pid_namespace=host['pid_namespace'],nonce=nonce)
        raw_registration = (json.dumps(registration,sort_keys=True,allow_nan=False)+'\n').encode()
        registration_sha = hashlib.sha256(raw_registration).hexdigest()
        plan = watchdog_service_plan(target,str(root/'registration.json'),registration_sha,schedule,now=datetime.now(timezone.utc))
        raw_plan = (json.dumps(plan,sort_keys=True,allow_nan=False)+'\n').encode()
        if len(raw_registration)>65536 or len(raw_plan)>65536:
            raise ValueError('guard registration and service plan must each fit 64KiB')
        _watchdog_event(events,identity,nonce,'guard_inputs_prepared',dict(state,create_events_sha256=create_events_sha256))
        for filename,data in zip(outputs,(raw_registration,raw_plan)):
            remaining()
            cleaned = _publish(root,filename,data)
            state['artifacts'][filename] = hashlib.sha256(data).hexdigest()
            if not cleaned:state['pending_partial_cleanup'].append(filename+'.partial')
            remaining()
        fd = os.open(root,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
        remaining()
        _,_,verified,_,verified_schedule,_ = load_watchdog_inputs(root/'registration.json',registration_sha)
        verified_plan = load_source_plan(root/'source-watchdog-service-plan.json',state['artifacts'][outputs[1]])
        if (verified!=registration or verified_schedule!=schedule
                or json.dumps(verified_plan,sort_keys=True,allow_nan=False)!=json.dumps(plan,sort_keys=True,allow_nan=False)):
            raise ValueError('published guard input readback differs')
        remaining()
        recheck()
        if datetime.now(timezone.utc)>=_utc(plan['launch_before_utc']):
            raise TimeoutError('guard-input publication consumed the service submission window')
        result = dict(state,registration_sha256=registration_sha,service_plan_sha256=state['artifacts'][outputs[1]],
            nonce=nonce,registration_materialized=True,service_launch_before_utc=plan['launch_before_utc'])
        _watchdog_event(events,identity,nonce,'guard_inputs_materialized',result)
        remaining()
        return result
    except BaseException as error:
        error.p0_registration_receipt = state
        if identity is not None:
            try:_watchdog_event(events,identity,nonce or identity['holder'],'guard_inputs_failed',
                dict(state,error_type=type(error).__name__,error=str(error)[:1024]))
            except BaseException as evidence_error:
                error.p0_registration_evidence_error = dict(error_type=type(evidence_error).__name__,error=str(evidence_error)[:1024])
        raise


def run_p0_setup(worker_input_path, worker_input_sha256, *, events,
                 runner=_guard_capture, host_probe=_watchdog_host):
    """Run metadata CLIs and collect real exit status; never start the GPU target."""
    from source_bundle import verify_p0_worker_input
    from vllm_export_hook import _metadata_stream
    from native_index import _publish
    entered,started = datetime.now(timezone.utc),time.monotonic()
    state = dict(phase_exitcodes={},capture_cleanup={},artifacts={},pending_partial_cleanup=[],container_id=None,reported_container_id=None,
        creation_cli_exit_verified=False,registration_cli_exit_verified=False,
        service_submit_cli_exit_verified=False,service_probe_cli_exit_verified=False,
        guard_deployed=False,resource_window_verified=False,runtime_abi_verified=False,
        runtime_admitted=False,resources_released=False,cleanup_owed=False)
    identity,phase = None,None
    try:
        _,root,inputs,receipt,schedule,identity = verify_p0_worker_input(worker_input_path,worker_input_sha256)
        if Path(__file__).resolve(strict=True)!=root/'code/source_schedule.py':
            raise ValueError('P0 setup must execute this run verified source bundle')
        if entered<_utc(inputs['batch_entered_utc']):
            raise TimeoutError('P0 setup batch anchor is still in the future')
        def remaining(which):
            now = datetime.now(timezone.utc)
            if now<entered:raise TimeoutError('P0 setup wall clock regressed')
            limit = _utc(schedule['phase_deadlines_utc']['preflight' if which=='create' else 'guard_setup'])
            value = min((limit-entered).total_seconds()-(time.monotonic()-started),(limit-now).total_seconds())
            if value<=0:raise TimeoutError('P0 setup original phase budget exhausted')
            return value
        remaining('create')
        host = host_probe()
        if (host['uid']!=2147 or type(host['uid']) is not int
                or not re.fullmatch('[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',host['boot_id'])
                or type(host['pid_namespace']) is not int or host['pid_namespace']<=0
                or re.search(r'/(docker/|docker-|kubepods)',host['cgroup'])
                or socket.gethostname() not in ('qjhs3','qjhs003')):
            raise ValueError('P0 setup requires the authorized Linux host')
        stages = ('create','register','submit','probe')
        names = [f'p0-setup-{stage}.{stream}.log' for stage in stages for stream in ('stdout','stderr')]
        names += ['p0-create-events.jsonl','p0-registration-events.jsonl','registration.json',
            'source-watchdog-service-plan.json','source-watchdog-submit-events.jsonl','source-watchdog-events.jsonl']
        if any((root/name).exists() or (root/name).is_symlink() for name in (*names,*(name+'.partial' for name in names))):
            raise FileExistsError('P0 setup has existing stage evidence; no automatic replay')
        def record_streams(label,stdout,stderr):
            for stream,data in (('stdout',stdout),('stderr',stderr)):
                if data is None:data=b''
                if isinstance(data,str):data=data.encode('utf-8')
                if not isinstance(data,bytes) or len(data)>65536:
                    raise ValueError('phase raw stream must fit 64KiB')
                name=f'p0-setup-{label}.{stream}.log'
                cleaned=_publish(root,name,data)
                state['artifacts'][name]=hashlib.sha256(data).hexdigest()
                if not cleaned:state['pending_partial_cleanup'].append(name+'.partial')
        def recover_stdout(label,stdout):
            if stdout is None:return None
            raw=stdout.encode('utf-8') if isinstance(stdout,str) else stdout
            if not isinstance(raw,bytes) or len(raw)>65536:
                raise ValueError('phase stdout must fit 64KiB')
            try:document=json.loads(raw)
            except (TypeError,ValueError):document=None
            if label=='create' and isinstance(document,dict):
                reported=document.get('container_id')
                if isinstance(reported,str) and re.fullmatch('[0-9a-f]{64}',reported):
                    state['reported_container_id']=reported
            return document
        def run(label,subcommand,args):
            nonlocal phase
            phase=label
            budget=min(30,remaining(label))
            command=['/usr/bin/python3','-B','-E','-s','-X','utf8',str(root/'code/source_schedule.py'),subcommand,*args]
            _watchdog_event(events,identity,identity['holder'],'phase_started',dict(phase=label,
                command=command,timeout_seconds=budget,runtime_admitted=False))
            budget=min(budget,remaining(label))
            if label=='create':state['cleanup_owed']=True
            try:
                response=runner(command,budget,{'PATH':'/usr/bin:/bin','LC_ALL':'C','TZ':'UTC','SYSTEMD_COLORS':'0'})
            except BaseException as error:
                state['phase_exitcodes'][label]=getattr(error,'guard_capture_returncode',None)
                state['capture_cleanup'][label]=dict(pid=getattr(error,'guard_capture_child_pid',None),
                    direct_child_exit_verified=getattr(error,'guard_capture_child_exit_verified',False))
                try:
                    recover_stdout(label,getattr(error,'guard_capture_stdout',b''))
                    record_streams(label,getattr(error,'guard_capture_stdout',b''),getattr(error,'guard_capture_stderr',b''))
                except BaseException as capture_error:
                    error.p0_setup_capture_error=dict(error_type=type(capture_error).__name__,error=str(capture_error)[:1024])
                raise
            state['phase_exitcodes'][label]=response.returncode
            document=recover_stdout(label,response.stdout)
            record_streams(label,response.stdout,response.stderr)
            _watchdog_event(events,identity,identity['holder'],'phase_returned',dict(phase=label,
                returncode=response.returncode,elapsed_seconds=time.monotonic()-started,
                artifacts=dict(state['artifacts']),runtime_admitted=False))
            remaining(label)
            if type(response.returncode) is not int or response.returncode!=0:
                raise RuntimeError('P0 phase returned nonzero: '+label)
            if not isinstance(document,dict) or document.get('runtime_admitted') is not False:
                raise ValueError('P0 phase output or runtime boundary differs')
            return document
        worker_args=['--worker-input',str(worker_input_path),'--worker-input-sha',worker_input_sha256]
        created=run('create','p0-create',worker_args)
        with _metadata_stream(root/'p0-create-events.jsonl') as stream:raw=stream.read(16385)
        if len(raw)>16384 or not raw.endswith(b'\n'):
            raise ValueError('P0 creation evidence is oversized or partial')
        rows=[json.loads(line) for line in raw.splitlines()]
        if (len(rows)!=2 or rows[-1].get('event')!='create_verified'
                or json.dumps(rows[-1]['payload'],sort_keys=True,allow_nan=False)!=json.dumps(created,sort_keys=True,allow_nan=False)):
            raise ValueError('create CLI output and retained creation event differ')
        state['container_id']=state['reported_container_id']
        if state['container_id'] is None:raise ValueError('create result lacks a complete container ID')
        state['creation_cli_exit_verified']=True
        registered=run('register','p0-register',worker_args+['--create-events-sha',hashlib.sha256(raw).hexdigest()])
        if registered.get('registration_materialized') is not True or registered.get('pending_partial_cleanup')!=[]:
            raise ValueError('P0 registration is incomplete or retains pending partials')
        _,_,registration,_,verified_schedule,_=load_watchdog_inputs(root/'registration.json',registered['registration_sha256'])
        service_plan=load_source_plan(root/'source-watchdog-service-plan.json',registered['service_plan_sha256'])
        if (registration['target']['container_id']!=state['container_id'] or verified_schedule!=schedule
                or registration['host_pid_namespace']!=host['pid_namespace'] or registration['target']['boot_id']!=host['boot_id']):
            raise ValueError('P0 setup registration changed target, host or original schedule')
        state['registration_cli_exit_verified']=True
        guard_args=['--registration',str(root/'registration.json'),'--registration-sha',registered['registration_sha256'],
                    '--service-plan-sha',registered['service_plan_sha256']]
        submitted=run('submit','service-submit',guard_args)
        if (submitted.get('guard_deployed') is not True or submitted.get('service_submission_deadline_verified') is not True
                or submitted.get('container_id')!=state['container_id'] or submitted.get('identity')!=identity
                or submitted.get('independent_service_verified') is not True
                or submitted.get('nonce')!=registration['nonce'] or submitted.get('service_unit')!=service_plan['unit']
                or not isinstance(submitted.get('service_invocation_id'),str)
                or not re.fullmatch('[0-9a-f]{32}',submitted['service_invocation_id'])):
            raise ValueError('P0 guard submission proof differs')
        state['service_submit_cli_exit_verified']=True
        probed=run('probe','service-probe',guard_args)
        if (probed.get('independent_service_verified') is not True or probed.get('identity')!=identity
                or probed.get('container_id')!=state['container_id'] or probed.get('nonce')!=registration['nonce']
                or probed.get('service_invocation_id')!=submitted.get('service_invocation_id')
                or probed.get('service_unit')!=service_plan['unit']
                or probed.get('image_id')!=receipt['image_id']
                or probed.get('container_created')!=registration['target']['container_created']
                or probed.get('guard_fire_by_utc')!=schedule['guard_fire_by_utc']
                or probed.get('host',{}).get('boot_id')!=host['boot_id']
                or probed.get('host',{}).get('uid')!=2147
                or probed.get('host',{}).get('pid_namespace')!=host['pid_namespace']):
            raise ValueError('P0 fresh guard probe changed service identity')
        state.update(service_probe_cli_exit_verified=True,guard_deployed=True,setup_cli_exit_verified=True)
        result=dict(state,registration_sha256=registered['registration_sha256'],service_plan_sha256=registered['service_plan_sha256'])
        _watchdog_event(events,identity,identity['holder'],'setup_returned',result)
        remaining('probe')
        return result
    except BaseException as error:
        error.p0_setup_receipt=state
        if identity is not None:
            try:_watchdog_event(events,identity,identity['holder'],'setup_failed',dict(state,phase=phase,error_type=type(error).__name__,error=str(error)[:1024]))
            except BaseException as evidence_error:
                error.p0_setup_evidence_error=dict(error_type=type(evidence_error).__name__,error=str(evidence_error)[:1024])
        raise


def p0_setup_main(argv):
    import argparse
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('P0 setup requires unoptimized python -B')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-input',required=True)
    parser.add_argument('--worker-input-sha',required=True)
    args=parser.parse_args(argv)
    path=Path(args.worker_input)
    if not path.is_absolute():raise ValueError('absolute P0 worker input required')
    root=path.resolve(strict=True).parent
    if Path(__file__).resolve(strict=True)!=root/'code/source_schedule.py':
        raise ValueError('P0 setup must execute this run verified source bundle')
    with (root/'p0-setup-events.jsonl').open('xb',buffering=0) as events:
        result=run_p0_setup(args.worker_input,args.worker_input_sha,events=events)
    print(json.dumps(result,sort_keys=True))


def p0_register_main(argv):
    """Materialize metadata; does not install a service or start a container."""
    import argparse
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('P0 registration requires unoptimized python -B')
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('worker-input','worker-input-sha','create-events-sha'):
        parser.add_argument('--'+name,required=True)
    args = parser.parse_args(argv)
    path = Path(args.worker_input)
    if not path.is_absolute():raise ValueError('absolute P0 worker input required')
    root = path.resolve(strict=True).parent
    if Path(__file__).resolve(strict=True)!=root/'code/source_schedule.py':
        raise ValueError('guard inputs must be prepared from this run verified source bundle')
    with (root/'p0-registration-events.jsonl').open('xb',buffering=0) as events:
        result = materialize_p0_guard_inputs(args.worker_input,args.worker_input_sha,args.create_events_sha,events=events)
    print(json.dumps(result,sort_keys=True))


def p0_create_main(argv):
    """Explicit metadata-create entry, not a GPU/model execution gate."""
    import argparse
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('P0 create requires unoptimized python -B')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker-input',required=True)
    parser.add_argument('--worker-input-sha',required=True)
    args = parser.parse_args(argv)
    # Establish only the exclusive destination; the core budgets all verification.
    path = Path(args.worker_input)
    if not path.is_absolute():
        raise ValueError('absolute P0 worker input required')
    root = path.resolve(strict=True).parent
    if Path(__file__).resolve(strict=True) != root/'code/source_schedule.py':
        raise ValueError('create must execute this run verified source bundle')
    with (root/'p0-create-events.jsonl').open('xb',buffering=0) as events:
        result = create_p0_target(args.worker_input,args.worker_input_sha,events=events)
    print(json.dumps(result,sort_keys=True))


def submit_p0_watchdog(service_plan, target, schedule, *, nonce, host_pid_namespace,
                       plan_sha256, events, runner=_guard_capture, docker=_guard_docker,
                       host_probe=_watchdog_host, ready_waiter=_await_watchdog_ready,
                       service_probe=probe_watchdog_service):
    """One owned P0 guard submission; no Docker start or compute authorization."""
    entered, started = datetime.now(timezone.utc), time.monotonic()
    state = dict(unit=service_plan['unit'], container_id=target['container_id'],
        submission_attempted=False, submission_outcome='not_attempted', guard_deployed=False,
        runtime_admitted=False, resources_released=False)
    identity = target['identity']
    try:
        if target['schema'] != 'kimi-p0-guard-target-v2':
            raise ValueError('guard submission only accepts P0 scope')
        derived = watchdog_service_plan(target, service_plan['command'][-3], service_plan['command'][-1],
                                        schedule, now=_utc(service_plan['generated_utc']))
        if json.dumps(service_plan, sort_keys=True, allow_nan=False) != json.dumps(derived, sort_keys=True, allow_nan=False):
            raise ValueError('exact re-derived P0 service plan required')
        if (not isinstance(plan_sha256, str) or not re.fullmatch('[0-9a-f]{64}', plan_sha256)
                or not isinstance(nonce, str) or not re.fullmatch('[0-9a-f]{32}', nonce)
                or type(host_pid_namespace) is not int or host_pid_namespace <= 0):
            raise ValueError('P0 plan SHA, nonce and host namespace required')
        if entered < _utc(service_plan['generated_utc']):
            raise TimeoutError('P0 service plan is from the future')
        deadline = started + min(30, (_utc(schedule['phase_deadlines_utc']['guard_setup'])-entered).total_seconds())
        def remaining(*, submitting=False):
            now = datetime.now(timezone.utc)
            if now < entered:
                raise TimeoutError('P0 submit wall clock regressed')
            limit = service_plan['launch_before_utc'] if submitting else schedule['phase_deadlines_utc']['guard_setup']
            value = min(deadline-time.monotonic(), (_utc(limit)-now).total_seconds(),
                        (_utc(schedule['guard_fire_by_utc'])-now).total_seconds())
            if value <= 0:
                raise TimeoutError('P0 submit or original guard-setup deadline exhausted')
            return value
        remaining(submitting=True)
        host = host_probe()
        remaining(submitting=True)
        _guard_identity(target, boot_id=host['boot_id'], uid=host['uid'])
        if (host['pid_namespace'] != host_pid_namespace
                or re.search(r'/(docker/|docker-|kubepods)', host['cgroup'])
                or socket.gethostname() not in ('qjhs3', 'qjhs003')):
            raise ValueError('P0 submission must run on the registered qjhs003 host')
        def stopped_target():
            response = docker(['inspect', target['container_id']], min(5, remaining()))
            remaining()
            if response.returncode or len(response.stdout) > 65536 or len(response.stderr) > 65536:
                raise RuntimeError('P0 target inspection failed')
            rows = json.loads(response.stdout)
            if not isinstance(rows, list) or len(rows) != 1:
                raise ValueError('exact P0 target inspection required')
            guard_target(target, rows[0], boot_id=host['boot_id'], uid=host['uid'])
            if rows[0]['State']['Running']:
                raise ValueError('P0 guard must be installed before starting the target')
        stopped_target()
        _watchdog_event(events, identity, nonce, 'submission_prepared', dict(state,
            registration_sha256=service_plan['command'][-1], plan_sha256=plan_sha256))
        timeout = min(10, remaining(submitting=True))
        state.update(submission_attempted=True, submission_outcome='unknown',
                     submitted_utc=datetime.now(timezone.utc).isoformat())
        response = runner(service_plan['command'], timeout,
            {'PATH':'/usr/bin:/bin', 'LC_ALL':'C', 'TZ':'UTC', 'SYSTEMD_COLORS':'0'})
        remaining(submitting=True)
        if response.returncode or len(response.stdout) > 65536 or len(response.stderr) > 65536:
            raise RuntimeError('P0 system service submission failed; unit state must be inspected')
        state.update(submission_outcome='client_returned_zero', submission_returned_utc=datetime.now(timezone.utc).isoformat())
        remaining(submitting=True)
        root = Path(service_plan['command'][-3]).parent
        ready = ready_waiter(root/'source-watchdog-events.jsonl', target, remaining=remaining, nonce=nonce,
            registration_sha256=service_plan['command'][-1], plan_sha256=plan_sha256,
            guard_fire_by_utc=schedule['guard_fire_by_utc'])
        proof = service_probe(service_plan, target, ready, schedule, nonce=nonce,
            host_pid_namespace=host_pid_namespace, guard_fire_by_utc=schedule['guard_fire_by_utc'],
            timeout_seconds=min(5, remaining()))
        remaining()
        if (proof.get('independent_service_verified') is not True or proof.get('runtime_admitted') is not False
                or proof.get('identity') != identity or proof.get('container_id') != target['container_id']):
            raise ValueError('P0 independent service proof differs')
        stopped_target()
        state.update(guard_deployed=True, service_submission_deadline_verified=True)
        _watchdog_event(events, identity, nonce, 'submission_verified', dict(state,
            service_invocation_id=proof['service_invocation_id']))
        remaining()
        return dict(proof, **state)
    except BaseException as error:
        # A timed-out CLI does not cancel the system manager's service creation.
        error.guard_submission_receipt = state
        try:
            _watchdog_event(events, identity, nonce, 'submission_failed', dict(state,
                error_type=type(error).__name__, error=str(error)[:1024]))
        except BaseException as evidence_error:
            error.guard_submission_evidence_error = dict(error_type=type(evidence_error).__name__, error=str(evidence_error)[:1024])
        raise


def load_watchdog_inputs(registration_path, registration_sha256):
    """Verify exact source or P0 scope, retaining the original batch anchor."""
    from source_bundle import verify_source_run, verify_p0_run
    path = Path(registration_path)
    if not path.is_absolute():
        raise ValueError('absolute watchdog registration required')
    root = path.resolve(strict=True).parent
    if Path(__file__).resolve().parent != root/'code':
        raise ValueError('watchdog must execute from this run source bundle')
    registration = load_source_plan(path, registration_sha256)
    source = isinstance(registration, dict) and registration.get('schema') == 'kimi-source-watchdog-registration-v2'
    keys = {'schema', 'target', 'plan_sha256', 'batch_entered_utc', 'host_pid_namespace', 'nonce'}
    if source:
        keys.add('worker_input_sha256')
    if (not isinstance(registration, dict) or set(registration) != keys
            or registration['schema'] not in ('kimi-source-watchdog-registration-v2', 'kimi-p0-watchdog-registration-v1')):
        raise ValueError('exact watchdog registration required')
    p0 = registration['schema'] == 'kimi-p0-watchdog-registration-v1'
    prefix = 'p0' if p0 else 'source'
    target_schema = 'kimi-p0-guard-target-v2' if p0 else 'kimi-source-guard-target-v1'
    if registration['target'].get('schema') != target_schema:
        raise ValueError('watchdog and target scopes differ')
    plan = load_source_plan(root/(prefix+'-plan.json'), registration['plan_sha256'])
    verify = verify_p0_run if p0 else verify_source_run
    receipt = verify(root/'code', root/'source-code-manifest.json', plan['source_manifest_sha256'],
                     root/(prefix+'-spec.json'), plan['spec_sha256'])
    schedule = (p0_schedule if p0 else source_schedule)(plan, receipt, now=_utc(registration['batch_entered_utc']))
    if p0:
        from source_bundle import verify_p0_worker_input
        _, worker_root, worker_input, worker_receipt, worker_schedule, _ = verify_p0_worker_input(
            root/'p0-worker-input.json', registration['target']['worker_input_sha256'])
        if (registration['target']['image_id'] != receipt['image_id']
                or registration['target']['gpu_uuid'] != receipt['gpu_uuid']):
            raise ValueError('P0 target image or GPU differs from spec')
        if (worker_root != root or registration['target']['run_root'] != str(root)
                or worker_input['plan_sha256'] != registration['plan_sha256']
                or worker_input['batch_entered_utc'] != registration['batch_entered_utc']
                or worker_schedule != schedule or worker_receipt['spec_sha256'] != receipt['spec_sha256']
                or worker_receipt['environment_sha256'] != registration['target']['environment_sha256']):
            raise ValueError('P0 host registration differs from frozen worker input')
    else:
        from source_bundle import verify_source_worker_input
        worker_root, worker_input, _, worker_receipt, worker_schedule, worker_identity = verify_source_worker_input(
            root/'source-worker-input.json', registration['worker_input_sha256'])
        if (worker_root != root or worker_input['plan_sha256'] != registration['plan_sha256']
                or worker_input['batch_entered_utc'] != registration['batch_entered_utc']
                or worker_schedule != schedule
                or any(worker_receipt[k] != receipt[k] for k in
                       ('run_id', 'holder', 'code_commit', 'spec_sha256', 'manifest_sha256'))
                or worker_identity != registration['target']['identity']):
            raise ValueError('source host registration differs from frozen worker input')
        receipt['worker_input_sha256'] = registration['worker_input_sha256']
    expected = dict(run_id=receipt['run_id'], holder=receipt['holder'], code_commit=receipt['code_commit'],
        spec_sha256=receipt['spec_sha256'], source_manifest_sha256=receipt['manifest_sha256'])
    if registration['target']['identity'] != expected:
        raise ValueError('watchdog target and verified source identity differ')
    return path, root, registration, receipt, schedule, expected


def watchdog_main(argv, *, probe_only=False, service_plan_only=False, service_probe_only=False,
                  service_submit_only=False):
    """Host-only entry; source verification and target probe precede readiness."""
    import argparse
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('watchdog requires unoptimized python -B')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--registration-sha', required=True)
    parser.add_argument('--service-plan-sha')
    args = parser.parse_args(argv)
    path, root, registration, receipt, schedule, expected = load_watchdog_inputs(
        args.registration, args.registration_sha)
    if service_submit_only:
        service_plan = load_source_plan(root/'source-watchdog-service-plan.json', args.service_plan_sha)
        if (service_plan['command'][-3] != str(path.resolve()) or service_plan['command'][-1] != args.registration_sha):
            raise ValueError('service plan belongs to another registration')
        with (root/'source-watchdog-submit-events.jsonl').open('xb', buffering=0) as events:
            result = submit_p0_watchdog(service_plan, registration['target'], schedule,
                nonce=registration['nonce'], host_pid_namespace=registration['host_pid_namespace'],
                plan_sha256=registration['plan_sha256'], events=events)
        print(json.dumps(result, sort_keys=True))
        return
    if service_plan_only:
        result = watchdog_service_plan(registration['target'], str(path.resolve()), args.registration_sha,
                                       schedule, now=datetime.now(timezone.utc))
        print(json.dumps(result, sort_keys=True))
        return
    if probe_only or service_probe_only:
        ready = read_watchdog_ready(root/'source-watchdog-events.jsonl', registration['target'],
            nonce=registration['nonce'], registration_sha256=args.registration_sha,
            plan_sha256=registration['plan_sha256'], guard_fire_by_utc=schedule['guard_fire_by_utc'])
        if service_probe_only:
            service_plan = load_source_plan(root/'source-watchdog-service-plan.json', args.service_plan_sha)
            if (service_plan['command'][-3] != str(path.resolve())
                    or service_plan['command'][-1] != args.registration_sha):
                raise ValueError('service plan belongs to another registration')
            result = probe_watchdog_service(service_plan, registration['target'], ready, schedule,
                nonce=registration['nonce'], host_pid_namespace=registration['host_pid_namespace'],
                guard_fire_by_utc=schedule['guard_fire_by_utc'])
        else:
            result = probe_watchdog(registration['target'], nonce=registration['nonce'], expected_host=ready['host'],
                host_pid_namespace=registration['host_pid_namespace'], guard_fire_by_utc=schedule['guard_fire_by_utc'])
        print(json.dumps(result, sort_keys=True))
        return
    with (root/'source-watchdog-events.jsonl').open('xb', buffering=0) as events:
        _watchdog_event(events, expected, registration['nonce'], 'inputs_verified', dict(
            registration_sha256=args.registration_sha, plan_sha256=registration['plan_sha256'],
            runtime_admitted=False))
        result = source_watchdog(registration['target'], schedule, nonce=registration['nonce'],
            host_pid_namespace=registration['host_pid_namespace'], events=events, probe_factory=_probe_listener)
    print(json.dumps(result, sort_keys=True))


def main():
    import argparse
    from source_bundle import verify_source_run
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('code', 'manifest', 'manifest-sha', 'spec', 'spec-sha', 'plan', 'plan-sha'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    entered = datetime.now(timezone.utc)
    started = time.monotonic()
    receipt = verify_source_run(args.code, args.manifest, args.manifest_sha, args.spec, args.spec_sha)
    plan = load_source_plan(args.plan, args.plan_sha)
    result = source_schedule(plan, receipt, now=entered)
    elapsed = time.monotonic() - started
    finished = datetime.now(timezone.utc)
    try:
        phase_remaining_seconds(result, 'preflight', entered=entered, elapsed=elapsed, now=finished)
    except TimeoutError as error:
        raise TimeoutError('source verification consumed preflight budget or clock regressed') from error
    result['validation_elapsed_seconds'] = elapsed
    print(json.dumps(dict(result, run_id=receipt['run_id'], holder=receipt['holder']), sort_keys=True))


if __name__ == '__main__':
    if sys.argv[1:2] == ['p0-create']:
        p0_create_main(sys.argv[2:])
    elif sys.argv[1:2] == ['p0-setup']:
        p0_setup_main(sys.argv[2:])
    elif sys.argv[1:2] == ['p0-register']:
        p0_register_main(sys.argv[2:])
    elif sys.argv[1:2] in (['guard'], ['probe'], ['service-plan'], ['service-probe'], ['service-submit']):
        watchdog_main(sys.argv[2:], probe_only=sys.argv[1] == 'probe', service_plan_only=sys.argv[1] == 'service-plan',
                      service_probe_only=sys.argv[1] == 'service-probe', service_submit_only=sys.argv[1] == 'service-submit')
    else:
        main()
