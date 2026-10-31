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


def _utc(value):
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
    required = {'schema', 'run_id', 'holder', 'code_commit', 'spec_sha256', 'source_manifest_sha256',
                'resource_id', 'booking_id', 'window_start', 'window_end', 'cleanup_complete_by', 'seconds'}
    if (not isinstance(plan, dict) or set(plan) != required
            or plan['schema'] != 'kimi-source-window-v1' or plan['resource_id'] != 'h20/qjhs003'
            or not isinstance(plan['booking_id'], str) or not plan['booking_id'].startswith('bk-')):
        raise ValueError('exact source-window schema and node required')
    for name, receipt_name in (('run_id', 'run_id'), ('holder', 'holder'), ('code_commit', 'code_commit'),
            ('spec_sha256', 'spec_sha256'), ('source_manifest_sha256', 'manifest_sha256')):
        if plan[name] != source_receipt[receipt_name]:
            raise ValueError('source window identity differs: ' + name)
    seconds = plan['seconds']
    if (not isinstance(seconds, dict) or set(seconds) != set(PHASES)
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
    for name in PHASES:
        cursor += timedelta(seconds=seconds[name])
        stages[name] = cursor.isoformat()
    return dict(total_max_seconds=total, compute_max_seconds=total-sum(seconds[n] for n in TAIL),
                guard_fire_by_utc=guard_fire.isoformat(), cleanup_complete_by_utc=run_cleanup.isoformat(),
                window_cleanup_deadline_utc=cleanup.isoformat(),
                phase_deadlines_utc=stages, minimum_release_observe_seconds=60, timing_fits=True,
                booking_verified=False, guard_verified=False, runtime_admitted=False)


def phase_remaining_seconds(schedule, phase, *, entered, elapsed, now):
    """Use the original batch anchor, never a new per-phase start time."""
    if phase not in PHASES:
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
    keys = {'schema', 'identity', 'boot_id', 'uid', 'container_id', 'image_id', 'container_created'}
    if (not isinstance(registration, dict) or set(registration) != keys
            or registration['schema'] != 'kimi-source-guard-target-v1'):
        raise ValueError('exact source guard target schema required')
    identity = _source_identity(registration['identity'])
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


def guard_target(registration, info, *, boot_id, uid):
    """Bind cleanup to an immutable Docker ID, creation event and owned labels."""
    identity = _guard_identity(registration, boot_id=boot_id, uid=uid)
    cid, image = registration['container_id'], registration['image_id']
    labels = info['Config']['Labels']
    expected = {'sllm.kimi.' + key: value for key, value in identity.items()}
    expected['sllm.purpose'] = GUARD_PURPOSE
    if (info['Id'] != cid or info['Image'] != image or info['Created'] != registration['container_created']
            or info['Config']['User'] not in ('2147', '2147:2147')
            or not isinstance(labels, dict) or any(labels.get(k) != v for k, v in expected.items())
            or info['HostConfig']['RestartPolicy']['Name'] != 'no'
            or type(info['State']['Running']) is not bool
            or type(info['State']['Pid']) is not int
            or (info['State']['Running'] and info['State']['Pid'] <= 0)
            or (not info['State']['Running'] and info['State']['Pid'] != 0)):
        raise ValueError('guard container identity, owner or restart policy differs')
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
    deadline = time.monotonic() + timeout
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=environment)
    buffers = [bytearray(), bytearray()]
    selector, failure = None, None
    try:
        selector = selectors.DefaultSelector()
        for index, stream in enumerate((process.stdout, process.stderr)):
            selector.register(stream, selectors.EVENT_READ, index)
        while selector.get_map():
            remaining = deadline - time.monotonic()
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
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        code = process.wait(timeout=remaining)
        return subprocess.CompletedProcess(command, code, *(bytes(b).decode('utf-8', errors='strict') for b in buffers))
    except BaseException as error:
        failure = error
        try:
            process.kill()  # The guard's Docker CLI child only; no model/container inference.
            process.wait(timeout=max(0, min(1, deadline-time.monotonic())))
        except BaseException as cleanup_error:
            error.__notes__ = list(getattr(error, '__notes__', ())) + [
                'Docker CLI child cleanup also failed: ' + repr(cleanup_error)]
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


def watchdog_main(argv, *, probe_only=False, service_plan_only=False, service_probe_only=False):
    """Host-only entry; source verification and target probe precede readiness."""
    import argparse
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('watchdog requires unoptimized python -B')
    from source_bundle import verify_source_run
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registration', required=True)
    parser.add_argument('--registration-sha', required=True)
    parser.add_argument('--service-plan-sha')
    args = parser.parse_args(argv)
    path = Path(args.registration)
    if not path.is_absolute():
        raise ValueError('absolute watchdog registration required')
    root = path.resolve(strict=True).parent
    if Path(__file__).resolve().parent != root/'code':
        raise ValueError('watchdog must execute from this run source bundle')
    registration = load_source_plan(path, args.registration_sha)
    if (not isinstance(registration, dict) or set(registration) != {'schema', 'target', 'plan_sha256',
            'batch_entered_utc', 'host_pid_namespace', 'nonce'}
            or registration['schema'] != 'kimi-source-watchdog-registration-v1'):
        raise ValueError('exact watchdog registration required')
    plan = load_source_plan(root/'source-plan.json', registration['plan_sha256'])
    receipt = verify_source_run(root/'code', root/'source-code-manifest.json', plan['source_manifest_sha256'],
                                root/'source-spec.json', plan['spec_sha256'])
    schedule = source_schedule(plan, receipt, now=_utc(registration['batch_entered_utc']))
    expected = dict(run_id=receipt['run_id'], holder=receipt['holder'], code_commit=receipt['code_commit'],
        spec_sha256=receipt['spec_sha256'], source_manifest_sha256=receipt['manifest_sha256'])
    if registration['target']['identity'] != expected:
        raise ValueError('watchdog target and verified source identity differ')
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
    if sys.argv[1:2] in (['guard'], ['probe'], ['service-plan'], ['service-probe']):
        watchdog_main(sys.argv[2:], probe_only=sys.argv[1] == 'probe', service_plan_only=sys.argv[1] == 'service-plan',
                      service_probe_only=sys.argv[1] == 'service-probe')
    else:
        main()
