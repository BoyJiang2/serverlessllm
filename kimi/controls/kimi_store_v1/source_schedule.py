"""Source-worker deadline arithmetic; never grants resource authorization."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import subprocess
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
    main()
