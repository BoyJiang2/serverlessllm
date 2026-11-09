import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Debug-only GPU stack lifecycle on qjhs003: up/status/down. Never formal; refuses next to any formal run."""
import argparse
import datetime
import hashlib
import ipaddress
import json
import pathlib
import re
import shutil
import subprocess
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
TREE = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(TREE/'tools'))
import debug_plan as plan

HOST = pathlib.Path(str(plan.HOST))
SOURCE_TREE = re.compile(r'source-debug-([0-9a-f]{12})-([0-9a-f]{12})')
SOURCE_RECEIPT = 'deploy-tree.json'
GUARD_SCRIPT = pathlib.Path('/usr/local/lib/sllm-debug-guard/debug_guard.py')
GUARD_TIMER = 'sllm-debug-guard.timer'
GUARD_SERVICE = 'sllm-debug-guard.service'
GUARD_HEARTBEAT = pathlib.Path('/var/lib/sllm-debug-guard/heartbeat.json')
GUARD_FRESH_SECONDS = 180  # the timer sweeps every 60 s
UID = 2147
BOOKING_MARGIN_SECONDS = 600
BORROW_MAX_SECONDS = 2400
HEAD_READY_SECONDS = 120
HEAD_READY_MARK = 'Ray runtime started.'
STOP_SECONDS = dict(head=70, worker=320)  # TERM reaches the entry through timeout; worker needs the Store grace
TOOLS = (HERE/'debug_plan.py', HERE/'debug_stack.py', HERE/'debug_service.py', HERE/'debug_guard.py',
         TREE/'controls/service_smoke_v1/docker_args.py', TREE/'tools/restore_original_image.py', TREE/'tools/docker_plan.py')


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def docker(*args, timeout=60):
    return subprocess.check_output(['docker', *args], text=True, timeout=timeout).strip()


def write(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2))
    temporary.replace(path)


def refuse_shared_state(gpu_required):
    if os.getuid() != UID:
        raise RuntimeError('authorized UID 2147 required')
    for row in docker('ps', '--format', '{{.Label "sllm.run_id"}}|{{.Label "sllm.purpose"}}').splitlines():
        run_id, _, purpose = row.partition('|')
        if run_id.startswith('sllm-glm51fp8-'):
            raise RuntimeError('formal run container present: '+run_id)
        if purpose == plan.PURPOSE:
            raise RuntimeError('another debug stack is running: '+run_id)
    if gpu_required and subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'],
                                                text=True, timeout=15).strip():
        raise RuntimeError('GPU occupied')


def booking_window(booking_id, booking_end, seconds, now=None):
    if not booking_id or not booking_end:
        raise ValueError('GPU debug needs --booking-id and --booking-end of the active qjhs003 booking')
    end = datetime.datetime.fromisoformat(booking_end)
    if end.tzinfo is None:
        raise ValueError('booking end must carry a timezone')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if (end-now).total_seconds() < seconds+BOOKING_MARGIN_SECONDS:
        raise ValueError('lifetime plus 10 min margin does not fit the booking')
    return dict(booking_id=booking_id, booking_end=end.isoformat(), checked_utc=now.isoformat(),
                scope='user-supplied booking; not verified against the booking API')


def gpu_authority(borrow, booking_id, booking_end, seconds, now=None):
    """A booking, or an explicit owner instruction to borrow idle GPUs for a short capped window."""
    if not borrow:
        return booking_window(booking_id, booking_end, seconds, now)
    if booking_id or booking_end:
        raise ValueError('--borrow and booking arguments are mutually exclusive')
    if seconds > BORROW_MAX_SECONDS:
        raise ValueError('borrowed GPU time is capped at %d s' % BORROW_MAX_SECONDS)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return dict(mode='borrowed without booking', authorized_by='owner instruction in chat, 2026-10-28',
                checked_utc=now.isoformat(), max_lifetime_seconds=BORROW_MAX_SECONDS,
                scope='GPUs idle and no formal or debug containers when up ran; not a reservation')


def guard_problems(timer_state, exec_start, script_stat, parent_stat, script_sha256, expected_sha256, heartbeat_stat, heartbeat_age):
    problems = []
    if timer_state != 'active':
        problems.append('timer %s is %s' % (GUARD_TIMER, timer_state or 'absent'))
    if str(GUARD_SCRIPT) not in exec_start:
        problems.append('%s does not run %s' % (GUARD_SERVICE, GUARD_SCRIPT))
    for name, stat in (('script', script_stat), ('directory', parent_stat), ('heartbeat', heartbeat_stat)):
        if stat is None or stat.st_uid != 0 or stat.st_mode & 0o022:
            problems.append('guard %s must exist, be root-owned and not group/world writable' % name)
    if script_sha256 != expected_sha256:
        problems.append('installed guard differs from this tool version')
    if heartbeat_age is None or not 0 <= heartbeat_age <= GUARD_FRESH_SECONDS:
        problems.append('guard has not completed a sweep in the last %d s (%s)' % (GUARD_FRESH_SECONDS, GUARD_HEARTBEAT))
    return problems


def guard_state():
    """The independent host guard: a root timer that removes expired debug containers even if timeout, the entry
    or the SSH session fail. Model runs refuse to start without it; head-only runs only record its state."""
    def stat(path):
        try:
            return None if path.is_symlink() else path.stat()
        except OSError:
            return None
    show = lambda *args: subprocess.run(['systemctl', *args], capture_output=True, text=True, timeout=15).stdout.strip()
    script, heartbeat = stat(GUARD_SCRIPT), stat(GUARD_HEARTBEAT)
    try:
        age = time.time()-json.loads(GUARD_HEARTBEAT.read_text())['unix'] if heartbeat else None
    except (OSError, ValueError, KeyError, TypeError):
        age = None
    problems = guard_problems(show('is-active', GUARD_TIMER), show('show', GUARD_SERVICE, '-p', 'ExecStart', '--value'),
                              script, stat(GUARD_SCRIPT.parent), sha256(GUARD_SCRIPT) if script else None, sha256(HERE/'debug_guard.py'),
                              heartbeat, age)
    return dict(timer=GUARD_TIMER, script=str(GUARD_SCRIPT), active=not problems, problems=problems,
                heartbeat_age_seconds=None if age is None else round(age, 1),
                checked_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())


def configuration(weight_format, query, gpu):
    """What this run is, stated against C23: a debug configuration, never a formal-equivalent measurement."""
    import debug_service
    restored = json.loads((HOST/'validation/store-restore-validation.json').read_text())
    pool, chunk = debug_service.pool_for(weight_format, restored)
    differences = ['identity and directories: sllm-debug run id, DEBUG-UNREVIEWED labels, debug/<run>/ code tree and /r',
                   'worker entry: debug_service.py instead of supervise_control13 + bootstrap_control13 '
                   '(no monitor heartbeat, admission gates, budget phases or preregistered trial protocol)',
                   'lifetime: timeout in argv plus the debug host guard instead of the formal root deadline guard',
                   'switches: the 7 formal13-dropped switches removed, SLLM_DEBUG_WEIGHT_FORMAT/SLLM_DEBUG_QUERY added']
    if gpu and weight_format == 'safetensors':
        differences.append('Store pool: %d bytes instead of the %d-byte pool C23 kept resident through every trial' % (pool, restored['pool_bytes']))
    elif gpu:
        differences.append('Store pool: created by this run, not the one shared pool C23 kept across all trials')
    differences.append('requests: one gold request' if gpu and query else 'requests: none')
    return dict(weight_format=weight_format if gpu else None, pool_bytes=pool if gpu else None, chunk_bytes=chunk if gpu else None,
                c23_pool_bytes=restored['pool_bytes'], differs_from_formal=differences, comparable_to_formal_measurements=False)


def tree_files(root, skip=()):
    files = {}
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ValueError('symlink in source tree: '+relative)
        if path.is_file() and relative not in skip:
            data = path.read_bytes()
            files[relative] = (len(data), hashlib.sha256(data).hexdigest())
    return files


def verified_source(tree):
    """A debug tree pushed by tools/deploy_tree.py: exactly its receipt's files, from one commit, no formal identity."""
    tree = pathlib.Path(tree)
    match = SOURCE_TREE.fullmatch(tree.name)
    if match is None or tree.parent != HOST/'code' or tree.is_symlink() or not tree.is_dir():
        raise ValueError('--source-tree must be a deploy_tree push under %s' % (HOST/'code'))
    receipt = json.loads((tree/SOURCE_RECEIPT).read_text())
    rows = receipt['files']
    if (receipt.get('kind') != 'debug' or receipt['code_commit'][:12] != match[1] or receipt['tree_sha256'][:12] != match[2]
            or hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() != receipt['tree_sha256']):
        raise ValueError('source tree name, kind or file list does not match its receipt')
    expected = {r['path']: (r['bytes'], r['sha256']) for r in rows}
    if tree_files(tree, skip=(SOURCE_RECEIPT,)) != expected:
        raise ValueError('source tree differs from its receipt')
    if 'run-manifest.json' in expected or 'debug_service.py' not in expected:
        raise ValueError('debug source must carry the debug entry and no formal run identity')
    return receipt


def copy_source(tree, run_root, receipt):
    """The run keeps its own verified copy, so later changes to the pushed tree cannot alter what it ran."""
    source = run_root/'source'
    source.mkdir(mode=0o700)
    for row in receipt['files']:
        (source/row['path']).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pathlib.Path(tree)/row['path'], source/row['path'])
    if tree_files(source) != {r['path']: (r['bytes'], r['sha256']) for r in receipt['files']}:
        raise RuntimeError('copied source differs from the verified tree')
    return dict(tree=str(tree), code_commit=receipt['code_commit'], tree_sha256=receipt['tree_sha256'], files=len(receipt['files']))


def owned(info, state, role):
    labels = info['Config']['Labels'] or {}
    name = plan.head_spec()['name'] if role == 'head' else plan.worker_spec('x')['name']
    return (labels.get('sllm.run_id') == state['run_id'] and labels.get('sllm.purpose') == plan.PURPOSE
            and labels.get('sllm.holder') == state['holder'] and info['Image'] == plan.IMAGE
            and info['Name'] == '/'+state['run_id']+'-'+name)


def inspect(cid):
    result = subprocess.run(['docker', 'inspect', cid], capture_output=True, text=True, timeout=30)
    if result.returncode:
        if 'No such' in result.stderr:
            return None
        raise RuntimeError('docker inspect failed without confirmed absence')
    return json.loads(result.stdout)[0]


def create_and_start(argv, role, state, receipt):
    cid = docker(*argv[1:])
    if not re.fullmatch('[0-9a-f]{64}', cid):
        raise RuntimeError('docker create did not return a full container ID')
    state[role+'_container_id'] = cid
    write(receipt, state)
    docker('start', cid)
    info = inspect(cid)
    if info is None or not owned(info, state, role) or not info['State']['Running']:
        raise RuntimeError(role+' container not running under this holder')
    return info


def wait_head_ready(cid, seconds=HEAD_READY_SECONDS, sleep=time.sleep, clock=time.monotonic):
    """Non-head Ray nodes take temp_dir from GCS, so the worker shares the head's session directory through /r.
    Joining before the head's raylet has created its sockets races on the plasma socket name (seen 2026-10-28:
    bind EADDRINUSE, worker raylet abort). The formal flow only avoids it by starting the worker much later."""
    begin = clock()
    while clock()-begin < seconds:
        info = inspect(cid)
        if info is None or not info['State']['Running']:
            raise RuntimeError('head container stopped before Ray was ready')
        logs = subprocess.run(['docker', 'logs', cid], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30).stdout
        if HEAD_READY_MARK in logs:
            return round(clock()-begin, 1)
        sleep(1)
    raise TimeoutError('head Ray runtime not ready within %d s' % seconds)


def remove_owned(state, role):
    cid = state.get(role+'_container_id')
    if not cid:
        return 'never created'
    info = inspect(cid)
    if info is None:
        return 'absent'
    if not owned(info, state, role):
        raise RuntimeError(role+' container identity differs; not touching it')
    subprocess.run(['docker', 'stop', '-t', str(STOP_SECONDS[role]), cid], capture_output=True, timeout=STOP_SECONDS[role]+60)
    docker('rm', cid)
    if docker('ps', '-aq', '--no-trunc', '--filter', 'id='+cid):
        raise RuntimeError(role+' container remains after removal')
    return 'removed'


def up(args):
    controller_begin_ns = time.monotonic_ns()
    controller_boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    gpu = not args.head_only
    refuse_shared_state(gpu)
    booking = gpu_authority(args.borrow, args.booking_id, args.booking_end, args.seconds) if gpu else None
    source_receipt = verified_source(args.source_tree)
    guard = guard_state()
    if gpu and not guard['active']:
        raise RuntimeError('independent host guard not ready (%s); install once: sudo bash install_debug_guard.sh'
                           % '; '.join(guard['problems']))
    config = configuration(args.weight_format, not args.no_query, gpu)
    deadline = int(time.time())+args.seconds+plan.DEADLINE_GRACE
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = 'sllm-debug-'+stamp+'-'+uuid.uuid4().hex[:8]
    holder = uuid.uuid4().hex
    debug_root = HOST/'debug'
    debug_root.mkdir(mode=0o700, exist_ok=True)
    if debug_root.stat().st_uid != UID or debug_root.is_symlink():
        raise RuntimeError('debug root owner or type differs')
    run_root = debug_root/run_id
    run_root.mkdir(mode=0o700)
    state = dict(run_id=run_id, holder=holder, purpose=plan.PURPOSE, image_id=plan.IMAGE, mode='head-only' if args.head_only else 'gpu',
                 controller_begin_ns=controller_begin_ns, controller_boot_id=controller_boot_id,
                 controller_clock='time.monotonic_ns on qjhs003 host',
                 lifetime_seconds=args.seconds, deadline_unix=deadline, weight_format=args.weight_format, query=not args.no_query,
                 booking=booking, guard=guard, configuration=config,
                 tool_sha256={str(p.relative_to(TREE)): sha256(p) for p in TOOLS}, status='deploying source')
    receipt = run_root/'stack.json'
    with receipt.open('x') as stream:
        json.dump(state, stream, indent=2)
    try:
        state['source'] = copy_source(args.source_tree, run_root, source_receipt)
        active = run_root/'source'  # templates come from the same commit as the code the containers run
        from restore_original_image import restore_with_receipt
        # The image is verified, and restored from the archive if absent, right before each create; nothing here
        # relies on a running container keeping it or on when the node removes images.
        state['image'] = dict(head=restore_with_receipt(run_root/'image-init-head.json'))
        head_plan = plan.plan(run_id, holder, args.seconds, 'PENDING_OBSERVED_HEAD_IP', active=active, deadline_unix=deadline)['head']
        head = create_and_start(head_plan['docker_argv'], 'head', state, receipt)
        ip = head['NetworkSettings']['Networks']['bridge']['IPAddress']
        if not ipaddress.ip_address(ip).is_private:
            raise RuntimeError('head address is not private')
        state.update(head_ip=ip, status='head running', head_ready_seconds=wait_head_ready(head['Id']))
        if gpu:
            state['image']['worker'] = restore_with_receipt(run_root/'image-init-worker.json')
            worker = plan.plan(run_id, holder, args.seconds, ip, args.weight_format, not args.no_query,
                               active=active, deadline_unix=deadline)['worker']
            create_and_start(worker['docker_argv'], 'worker', state, receipt)
            state['status'] = 'worker starting; see status for readiness'
        state['up_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write(receipt, state)
    except BaseException as error:
        state.update(status='failed during up', error=repr(error))
        cleanup = {}
        for role in ('worker', 'head'):
            try:
                cleanup[role] = remove_owned(state, role)
            except Exception as problem:
                cleanup[role] = 'cleanup unverified: '+repr(problem)
        state['cleanup'] = cleanup
        write(receipt, state)
        raise
    return state


def load_state(run_id):
    if not re.fullmatch(plan.RUN_PATTERN, run_id):
        raise ValueError('debug run identity required')
    return json.loads((HOST/'debug'/run_id/'stack.json').read_text())


def status(args):
    state = load_state(args.run_id)
    report = dict(run_id=state['run_id'], status=state['status'], containers={})
    for role in ('head', 'worker'):
        cid = state.get(role+'_container_id')
        info = inspect(cid) if cid else None
        report['containers'][role] = 'never created' if not cid else 'absent' if info is None else dict(
            owned=owned(info, state, role), state=info['State']['Status'], exit_code=info['State']['ExitCode'])
    service = HOST/'debug'/state['run_id']/'debug-service'
    for name in ('ready.json', 'result.json'):
        if (service/name).exists():
            report[name] = json.loads((service/name).read_text())
    report['gpu_compute_pids'] = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'],
                                                         text=True, timeout=15).split()
    return report


def down(args):
    state = load_state(args.run_id)
    state['down'] = {role: remove_owned(state, role) for role in ('worker', 'head')}
    if state.get('worker_container_id'):
        deadline = time.monotonic()+30
        while subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'], text=True, timeout=15).strip():
            if time.monotonic() > deadline:
                raise RuntimeError('GPU processes remain after the worker was removed')
            time.sleep(2)
    end_ns = time.monotonic_ns()
    boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    # Missing or cross-boot timing must never prevent cleanup, nor become a duration.
    if 'controller_end_ns' not in state:
        begin_ns = state.get('controller_begin_ns')
        valid = (type(begin_ns) is int and begin_ns < end_ns and bool(boot_id)
                 and state.get('controller_boot_id') == boot_id)
        state.update(controller_end_ns=end_ns, controller_end_boot_id=boot_id,
                     controller_elapsed_s=(end_ns-begin_ns)/1e9 if valid else None,
                     controller_timing_valid=valid,
                     controller_scope='up entry through owned container removal and GPU-process check; '
                                      'excludes any later resource observation; debug evidence, not C23')
    state.update(status='released', released_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(HOST/'debug'/state['run_id']/'stack.json', state)
    return state


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Debug-only GPU stack lifecycle on qjhs003; never formal.')
    sub = parser.add_subparsers(dest='action', required=True)
    start = sub.add_parser('up')
    start.add_argument('--source-tree', required=True, help='a debug tree pushed by tools/deploy_tree.py push')
    start.add_argument('--seconds', type=int, default=3600)
    start.add_argument('--head-only', action='store_true', help='CPU-only Ray head; no GPU, no booking needed')
    start.add_argument('--weight-format', choices=plan.WEIGHT_FORMATS, default='safetensors')
    start.add_argument('--no-query', action='store_true', help='start the services but skip the gold request')
    start.add_argument('--booking-id')
    start.add_argument('--booking-end', help='ISO time with timezone, e.g. 2026-10-29T14:00:00+08:00')
    start.add_argument('--borrow', action='store_true',
                       help='owner-approved use of idle GPUs without a booking; lifetime capped at %d s' % BORROW_MAX_SECONDS)
    for name in ('status', 'down'):
        sub.add_parser(name).add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(dict(up=up, status=status, down=down)[args.action](args), indent=2))
