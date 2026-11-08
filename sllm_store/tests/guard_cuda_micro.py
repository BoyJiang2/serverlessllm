"""One CID only: root service must ARM before GO/start; no Docker API or GPU queries."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import select
import signal
import stat
import time

p = argparse.ArgumentParser()
p.add_argument('cid')
p.add_argument('evidence', type=pathlib.Path)
p.add_argument('--oci', type=pathlib.Path)
p.add_argument('--runc-root', default='/var/run/docker/runtime-runc/moby')
p.add_argument('--deadline', type=float, default=25)
p.add_argument('--kill-after', type=float, default=3)
p.add_argument('--overall', type=float, default=65, help='cleanup cutoff; release60 is additional')
p.add_argument('--normal-exit-marker', action='store_true', help='opt in to authenticated normal-exit.json after natural shutdown')
p.add_argument('--head-control-only', action='store_true', help='head control role only: warn on unclassified host discovery PermissionError')
a = p.parse_args()
assert os.geteuid() == 0 and re.fullmatch('[0-9a-f]{64}', a.cid)
assert all(math.isfinite(v) for v in (a.deadline, a.kill_after, a.overall))
assert a.kill_after == 3 and 0 < a.deadline and a.deadline + 3 < a.overall <= a.deadline + 40
base = a.evidence.resolve(strict=True)
assert a.evidence.is_absolute() and not a.evidence.is_symlink() and base.is_dir()
assert not a.head_control_only or (a.normal_exit_marker and base.name == 'guard-head'), 'head-control-only requires normal marker and guard-head role'
runc_root = pathlib.Path(a.runc_root).resolve()
assert a.runc_root == '/var/run/docker/runtime-runc/moby' or runc_root.is_relative_to(base)
oci = a.oci or pathlib.Path('/run/containerd/io.containerd.runtime.v2.task/moby') / a.cid / 'config.json'
assert oci.is_absolute() and oci.parent.name == a.cid
cgroot = pathlib.Path('/sys/fs/cgroup')
scope = cgroot / 'system.slice' / ('docker-' + a.cid + '.scope')
expected_cg = 'system.slice:docker:' + a.cid
log = (base / 'guard-events.jsonl').open('x', encoding='utf-8', buffering=1)
armed_at = time.monotonic()
nonce = os.urandom(16).hex()
pins, last, failures = {}, {}, set()
warnings = {}
scopefd = raw_sha = go_at = quiet = abort_at = normal_at = normal_trigger = None
oci_seen = scope_seen = False
log_bytes = 0
max_cycle = 0.0


def emit(kind, **data):
    global log_bytes
    row = json.dumps({'event': kind, 'mono': time.monotonic(), 'cid': a.cid, **data}) + '\n'
    try:
        size = len(row.encode())
        if log_bytes + size > 8 << 20:
            failures.add('event_log_cap'); return
        log.write(row); log_bytes += size
    except OSError:
        failures.add('event_log_write')


def atomic(name, payload):
    target = base / name
    assert not target.exists(), 'refuse to overwrite evidence'
    temp = base / (name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    os.replace(temp, target)
    fd = os.open(base, os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def read(path, limit=32768):
    with pathlib.Path(path).open('rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit: raise ValueError('bounded metadata read exceeded')
    return raw


def normal_completion(path, cid, arm_nonce, go, now, deadline):
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != 0 or stat.S_IMODE(parent.st_mode) != 0o700:
            raise ValueError('normal marker directory must be root private')
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size > 4096:
                raise ValueError('unsafe normal marker')
            raw = os.read(fd, 4097)
            if len(raw) > 4096: raise ValueError('normal marker too large')
        finally: os.close(fd)
    finally: os.close(directory)
    row = json.loads(raw)
    if not isinstance(row, dict) or set(row) != {'cid','nonce','go_monotonic','completion_monotonic','exit_code'}:
        raise ValueError('normal marker fields')
    if row['cid'] != cid or row['nonce'] != arm_nonce or type(row['exit_code']) is not int or row['exit_code'] != 0:
        raise ValueError('normal marker identity or exit')
    for key in ('go_monotonic','completion_monotonic'):
        if type(row[key]) not in (int,float) or not math.isfinite(row[key]):
            raise ValueError('normal marker timestamp')
    completed = row['completion_monotonic']
    if row['go_monotonic'] != go or not go <= completed <= min(now, go+deadline):
        raise ValueError('normal marker time mismatch')
    return completed


def startticks(pid):
    return int(read(pathlib.Path('/proc') / str(pid) / 'stat').rsplit(b')', 1)[1].split()[19])


def paired(argv, key, value):
    return any(argv[i:i+2] == [key, value] for i in range(len(argv)-1))


def identity(pid, detail=None):
    requested_detail = detail is not None
    if detail is None: detail = {}
    detail.update(pid=pid, startticks=None, uid=None, cgroup=None,
                  read_stage='status' if a.normal_exit_marker else 'exe')
    root = pathlib.Path('/proc') / str(pid)
    try:
        if a.normal_exit_marker:
            status = read(root / 'status').decode()
            uid = next((line.split()[1:] for line in status.splitlines() if line.startswith('Uid:')), None)
            if uid is None or len(uid) != 4 or not all(value.isdecimal() for value in uid): raise ValueError('invalid process UID identity')
            detail['uid'] = uid
            for field, key in (('State:', 'state'), ('PPid:', 'ppid')):
                value = next((line.split()[1:] for line in status.splitlines() if line.startswith(field)), [])
                detail[key] = value[0] if value else None
            if uid != ['0'] * 4:
                if requested_detail:
                    detail['read_stage'] = 'stat'; detail['startticks'] = startticks(pid)
                    detail['read_stage'] = 'cgroup'; detail['cgroup'] = read(root / 'cgroup').decode().strip()
                return None
            detail['read_stage'] = 'stat'; detail['startticks'] = startticks(pid)
            detail['read_stage'] = 'cgroup'; detail['cgroup'] = read(root / 'cgroup').decode().strip()
        detail['read_stage'] = 'exe'
        exe = os.readlink(root / 'exe')
        if exe not in ('/usr/bin/runc', '/usr/bin/containerd-shim-runc-v2'): return None
        detail['read_stage'] = 'cmdline'
        argv = read(root / 'cmdline').rstrip(b'\0').split(b'\0')
        if exe == '/usr/bin/runc':
            owned = argv[-1:] == [a.cid.encode()] and any(x in argv for x in (b'create', b'run', b'start', b'delete'))
            roots = [argv[i+1] for i in range(len(argv)-1) if argv[i] == b'--root']
            owned = owned and len(roots) == 1 and pathlib.Path(os.fsdecode(roots[0])).resolve() == runc_root
        else:
            owned = paired(argv, b'-id', a.cid.encode()) and paired(argv, b'-namespace', b'moby')
        if not owned: return None
        detail['owned_runtime'] = True
        detail['read_stage'] = 'postowned_status'
        status = read(root / 'status').decode()
        uid = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
        if uid != ['0'] * 4: return None
        detail['read_stage'] = 'postowned_stat'; ticks = startticks(pid)
        detail['read_stage'] = 'postowned_cgroup'; cgroup = read(root / 'cgroup').decode().strip()
        return {'pid': pid, 'startticks': ticks, 'exe': exe, 'cgroup': cgroup}
    except (OSError, ValueError) as error:
        error.partial_identity = dict(detail)
        if detail.get('owned_runtime'): error.owned_runtime = True
        raise


def scan():
    retry_used = False
    for entry in pathlib.Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        pid = int(entry.name); stage = 'identity'; row = None
        try:
            try:
                row = identity(pid)
            except PermissionError as first_error:
                partial = getattr(first_error, 'partial_identity', {})
                if (not a.normal_exit_marker or a.head_control_only or
                    partial.get('read_stage') != 'exe' or getattr(first_error, 'owned_runtime', False) or
                    any(key[0] == pid for key in pins)):
                    raise
                emit('proc_discovery_permission_first', pid=pid, stage=stage,
                     errno=first_error.errno, filename=first_error.filename, error_type=type(first_error).__name__,
                     partial_identity=partial)
                try:
                    stage = 'permission_ownership_proof'
                    argv = read(pathlib.Path('/proc') / str(pid) / 'cmdline').rstrip(b'\0').split(b'\0')
                    argv_sha = hashlib.sha256(b'\0'.join(argv)).hexdigest()
                    try: own_pids = {int(value) for value in read(scope / 'cgroup.procs').split()}
                    except FileNotFoundError:
                        if scope.exists(): raise
                        own_pids = set()
                    first_error.confirmation_ownership_proof = {
                        'exact_cid_token': a.cid.encode() in argv, 'own_scope_member': pid in own_pids,
                        'own_cgroup_component': 'docker-' + a.cid + '.scope' in partial.get('cgroup', '').split('/'),
                        'cmdline_sha256': argv_sha}
                    if any(first_error.confirmation_ownership_proof[key] for key in ('exact_cid_token', 'own_scope_member', 'own_cgroup_component')):
                        stage = 'identity'
                        raise first_error
                    if (type(partial.get('startticks')) is not int or partial['startticks'] <= 0 or
                        partial.get('uid') != ['0'] * 4 or not partial.get('cgroup')):
                        raise ValueError('permission recheck lacks initial identity')
                    stage = 'permission_recheck_budget'
                    if go_at is None or retry_used or time.monotonic() + .02 >= go_at + a.deadline:
                        raise first_error
                    retry_used = True
                    time.sleep(.02)
                    if time.monotonic() >= go_at + a.deadline: raise TimeoutError('permission recheck budget expired')
                    stage = 'permission_identity_recheck'; fresh = {}
                    row = identity(pid, fresh)
                    if (row is not None or fresh.get('owned_runtime') or
                        fresh.get('startticks') != partial['startticks'] or fresh.get('cgroup') != partial['cgroup']):
                        raise ValueError('permission recheck identity changed or became owned')
                    fresh_argv = read(pathlib.Path('/proc') / str(pid) / 'cmdline').rstrip(b'\0').split(b'\0')
                    final_status = read(pathlib.Path('/proc') / str(pid) / 'status').decode()
                    final_uid = next((line.split()[1:] for line in final_status.splitlines() if line.startswith('Uid:')), None)
                    final_cgroup = read(pathlib.Path('/proc') / str(pid) / 'cgroup').decode().strip()
                    if (hashlib.sha256(b'\0'.join(fresh_argv)).hexdigest() != argv_sha or
                        final_uid != fresh.get('uid') or final_cgroup != partial['cgroup'] or
                        startticks(pid) != partial['startticks']):
                        raise ValueError('permission recheck command or process identity changed')
                    if time.monotonic() >= go_at + a.deadline: raise TimeoutError('permission recheck budget expired')
                    emit('proc_discovery_permission_rechecked', pid=pid, first_errno=first_error.errno,
                         partial_identity=partial, fresh_identity=fresh, cmdline_sha256=argv_sha,
                         resolution='existing_nonroot_exclusion' if fresh['uid'] != ['0'] * 4 else 'existing_identity_filter_completed')
                except (FileNotFoundError, ProcessLookupError) as error:
                    raise ValueError('permission recheck lost process identity') from error
            if row is None or (row['pid'], row['startticks']) in pins: continue
            stage = 'pidfd_open'; fd = os.pidfd_open(row['pid'])
            try:
                stage = 'identity_recheck'
                if identity(row['pid']) != row: continue
                pins[(row['pid'], row['startticks'])] = {'fd': fd, 'row': row, 'term': False, 'kill': False}
                fd = None; emit('runtime_pinned', identity=row)
            finally:
                if fd is not None: os.close(fd)
        except (FileNotFoundError, ProcessLookupError): pass
        except (OSError, ValueError) as error:
            if a.head_control_only and isinstance(error, PermissionError) and stage == 'identity' and row is None and not getattr(error, 'owned_runtime', False) and not any(key[0] == pid for key in pins):
                proof_stage = 'cmdline_ownership'
                try:
                    try: candidate_argv = read(pathlib.Path('/proc') / str(pid) / 'cmdline').rstrip(b'\0').split(b'\0')
                    except (PermissionError, FileNotFoundError, ProcessLookupError): candidate_argv = []
                    own_command = a.cid.encode() in candidate_argv
                    proof_stage = 'scope_membership'
                    try: own_pids = {int(value) for value in read(scope / 'cgroup.procs').split()}
                    except FileNotFoundError:
                        if scope.exists(): raise
                        own_pids = set()
                    if not own_command and pid not in own_pids:
                        key = (pid, stage, error.errno, str(error.filename))
                        if key not in warnings:
                            warnings[key] = {'pid': pid, 'stage': stage, 'errno': error.errno, 'filename': error.filename, 'error_type': type(error).__name__}
                            emit('unknown_host_discovery_incomplete', **warnings[key])
                        continue
                except (OSError, ValueError) as membership_error:
                    error = membership_error; stage = proof_stage
            failures.add('proc_discovery'); emit('proc_discovery_error', pid=pid, stage=stage, error_type=type(error).__name__, errno=getattr(error, 'errno', None), filename=getattr(error, 'filename', None), partial_identity=getattr(error, 'partial_identity', None), ownership_proof=getattr(error, 'confirmation_ownership_proof', None))
    live = []
    for pin in pins.values():
        if not select.select([pin['fd']], [], [], 0)[0]: live.append(pin); continue
        try:
            if startticks(pin['row']['pid']) == pin['row']['startticks']: live.append(pin)
        except (FileNotFoundError, ProcessLookupError): pass
    return live


def snapshot():
    global scopefd, oci_seen, scope_seen, raw_sha
    fresh_oci = False
    try:
        raw = read(oci, 1 << 20); spec = json.loads(raw)
        if raw_sha is None:
            atomic('oci.raw.json', raw)
            raw_sha = hashlib.sha256(raw).hexdigest()
            emit('raw_oci_saved', sha256=raw_sha, bytes=len(raw), mode='0600')
        linux = spec.get('linux', {})
        if linux.get('cgroupsPath') != expected_cg: raise ValueError('OCI cgroup identity mismatch')
        def debug_env(env): return [x for x in env if x.startswith('NVIDIA_CTK_DEBUG=')]
        hooks = {stage: [{**{k: h[k] for k in ('path','args','timeout') if k in h}, 'env': debug_env(h.get('env', []))} for h in rows] for stage, rows in spec.get('hooks', {}).items()}
        chosen = {'oci_sha256': hashlib.sha256(raw).hexdigest(), 'annotations': spec.get('annotations', {}), 'resources': linux.get('resources', {}), 'cgroupsPath': linux['cgroupsPath'], 'hooks': hooks, 'env': debug_env(spec.get('process', {}).get('env', []))}
        oci_seen = fresh_oci = True
        if last.get('oci') != chosen: last['oci'] = chosen; emit('oci_selected', selected=chosen)
    except FileNotFoundError: pass
    if scopefd is not None:
        try:
            current = os.stat(scope, follow_symlinks=False)
        except FileNotFoundError:
            os.close(scopefd); scopefd = None
        else:
            opened = os.fstat(scopefd)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                if not fresh_oci: raise ValueError('new scope generation needs fresh OCI identity')
                os.close(scopefd); scopefd = None
    if scopefd is None and fresh_oci:
        try:
            scopefd = os.open(scope, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
            scope_seen = True
            emit('scope_pinned', inode=os.fstat(scopefd).st_ino, path=str(scope))
        except FileNotFoundError: pass
    state = {}
    for path in (scope, scope.parent, cgroot):
        row = {}
        names = ('cgroup.events','pids.max','pids.current','pids.events','memory.events')
        if path == scope: names += ('cgroup.procs',)
        for name in names:
            try: row[name] = read(path / name).decode().strip()
            except FileNotFoundError: row[name] = None
        state[str(path)] = row
    signature = {'scope': state[str(scope)], 'ancestor_limits': {
        path: {name: row[name] for name in ('pids.max','pids.events')}
        for path, row in state.items() if path != str(scope)}}
    if last.get('cgroup_signature') != signature or time.monotonic() - last.get('cgroup_log_at', float('-inf')) >= 5:
        last.update(cgroup_signature=signature, cgroup_log_at=time.monotonic())
        emit('cgroups', selected=state)
    events = state[str(scope)]['cgroup.events']
    return events is None or 'populated 0' in events.splitlines()


def kill_scope():
    if scopefd is None: return
    try:
        opened = os.fstat(scopefd); current = os.stat(scope, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino): raise ValueError('scope inode changed')
        fd = os.open('cgroup.kill', os.O_WRONLY | os.O_NOFOLLOW, dir_fd=scopefd)
        try: os.write(fd, b'1\n')
        finally: os.close(fd)
        emit('scope_kill', inode=opened.st_ino)
    except FileNotFoundError:
        if scope.exists(): raise


try:
    assert not scan() and not failures, 'CID already active or pre-ARM discovery failed'
    armed = {'cid': a.cid, 'nonce': nonce, 'guard_pid': os.getpid(), 'guard_startticks': startticks(os.getpid()), 'armed_monotonic': armed_at, 'deadline_seconds': a.deadline, 'kill_after_seconds': a.kill_after, 'cleanup_seconds': a.overall, 'release_seconds': 60}
    atomic('ARMED.json', json.dumps(armed).encode())
    emit('ARMED', **armed)
    while go_at is None:
        if time.monotonic() - armed_at > 10: raise TimeoutError('GO not received within ARM10')
        if os.path.lexists(base / 'ABORT'): raise RuntimeError('ABORT before GO; do not start target')
        try:
            go = json.loads(read(base / 'GO.json'))
            assert go['cid'] == a.cid and go['nonce'] == nonce
            proposed = float(go['monotonic'])
            assert armed_at <= proposed <= time.monotonic() and time.monotonic() - proposed <= 1
            go_at = proposed
        except (FileNotFoundError, json.JSONDecodeError): time.sleep(.03)
    try:
        atomic('GO-ACK.json', json.dumps({'cid': a.cid, 'go_monotonic': go_at}).encode())
    except Exception as error:
        failures.add('GO_ACK_publish'); abort_at = time.monotonic()
        emit('GO_ACK_error', error_type=type(error).__name__)
    emit('GO', go_monotonic=go_at)
    while time.monotonic() < go_at + a.overall + 60 and (normal_trigger is None or time.monotonic() < normal_trigger + a.overall - a.deadline + 60) and (not a.normal_exit_marker or abort_at is None or time.monotonic() < abort_at + a.overall - a.deadline + 60):
        cycle = time.monotonic()
        try: live = scan()
        except Exception as error:
            failures.add('scan'); emit('scan_error', error_type=type(error).__name__); live = list(pins.values())
        try: empty = snapshot()
        except Exception as error:
            failures.add('capture'); emit('capture_error', error_type=type(error).__name__); empty = False
        now = time.monotonic()
        if abort_at is None and os.path.lexists(base / 'ABORT'):
            abort_at = now; failures.add('abort_requested'); emit('ABORT', reason='run_abort_file_present')
        if a.normal_exit_marker and normal_at is None and os.path.lexists(base / 'normal-exit.json'):
            try:
                normal_at = normal_completion(base / 'normal-exit.json', a.cid, nonce, go_at, now, a.deadline)
                emit('normal_completion_authenticated', completion_monotonic=normal_at)
            except Exception as error:
                failures.add('normal_marker_invalid')
                if abort_at is None: abort_at = now
                emit('normal_marker_error', error_type=type(error).__name__)
        if a.normal_exit_marker and failures:
            if abort_at is None:
                abort_at = now; emit('ABORT', reason='guard_failure', failures=sorted(failures))
            if not os.path.lexists(base / 'ABORT'):
                try: atomic('ABORT', b'')
                except Exception as error:
                    failures.add('ABORT_publish'); emit('ABORT_publish_error', error_type=type(error).__name__)
        if a.normal_exit_marker and normal_at is not None and normal_trigger is None and not failures and abort_at is None and now < go_at + a.deadline and not live and empty and oci_seen and scope_seen and raw_sha is not None:
            normal_trigger = now
            emit('normal_cleanup_started', completion_monotonic=normal_at)
        trigger = min(go_at + a.deadline, abort_at if abort_at is not None else float('inf'), normal_trigger if normal_trigger is not None else float('inf'))
        if now >= trigger:
            if not empty:
                try: kill_scope()
                except (OSError, ValueError) as error:
                    failures.add('scope_kill'); emit('scope_kill_error', error_type=type(error).__name__)
            for pin in live:
                for flag, sig, due in (('term', signal.SIGTERM, 0), ('kill', signal.SIGKILL, a.kill_after)):
                    if now >= trigger + due and not pin[flag]:
                        try: signal.pidfd_send_signal(pin['fd'], sig); emit('runtime_signal', signal=sig, identity=pin['row'])
                        except ProcessLookupError: pass
                        except OSError as error:
                            failures.add('pidfd_signal'); emit('pidfd_error', error_type=type(error).__name__); continue
                        pin[flag] = True
            cleanup_cutoff = min(go_at + a.overall, normal_trigger + a.overall - a.deadline if normal_trigger is not None else float('inf'), abort_at + a.overall - a.deadline if a.normal_exit_marker and abort_at is not None else float('inf'))
            if quiet is None and now >= cleanup_cutoff: failures.add('normal_cleanup_exceeded' if normal_trigger is not None else 'abort_cleanup_exceeded' if a.normal_exit_marker and abort_at is not None else f'cleanup_exceeded_GO{a.overall:g}')
            if not live and empty:
                quiet = quiet or now
                if now - quiet >= 60: break
            else: quiet = None
        max_cycle = max(max_cycle, time.monotonic()-cycle)
        time.sleep(max(0, .03-(time.monotonic()-cycle)))
    if not oci_seen or not scope_seen or raw_sha is None: failures.add('required_OCI_or_scope_missing')
    if quiet is None or time.monotonic()-quiet < 60: failures.add('release60_incomplete')
    result = {'status': 'FAILED_RETAINED' if failures else 'KERNEL_CLEANUP_RELEASE_PASS', 'cid': a.cid, 'failures': sorted(failures), 'quiet_seconds': None if quiet is None else time.monotonic()-quiet, 'max_cycle_seconds': max_cycle, 'raw_oci_sha256': raw_sha, 'oci_seen': oci_seen, 'scope_seen': scope_seen, 'abort_monotonic': abort_at, 'pinned_identities': [pin['row'] for pin in pins.values()]}
    if a.normal_exit_marker: result.update(normal_completion_monotonic=normal_at, normal_cleanup_monotonic=normal_trigger)
    if a.head_control_only: result.update(warnings=list(warnings.values()), host_discovery_coverage='Unknown host PermissionError may be omitted; not proven unrelated. Known pins/owned runtime/own scope and all cleanup checks remain strict.')
    emit('final', **result)
    atomic('guard-final.json', json.dumps(result).encode())
    if failures: raise SystemExit(1)
finally:
    for pin in pins.values(): os.close(pin['fd'])
    if scopefd is not None: os.close(scopefd)
    log.close()
