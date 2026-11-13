import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Debug-only GPU stack lifecycle on qjhs003: up/status/down. Never formal; refuses next to any formal run."""
import argparse
import datetime
import errno
import hashlib
import http.client
import ipaddress
import json
import pathlib
import re
import shutil
import subprocess
import signal
import socket
import threading
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
         HERE/'install_debug_guard.sh',
         TREE/'controls/service_smoke_v1/docker_args.py', TREE/'tools/restore_original_image.py', TREE/'tools/docker_plan.py')
SUPPLEMENT_WORK_SECONDS = 1800
SUPPLEMENT_TOTAL_SECONDS = 3600
SUPPLEMENT_START_BUDGET_SECONDS = SUPPLEMENT_TOTAL_SECONDS + 300
SUPPLEMENT_CONDITIONS = ('safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit')


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
    return dict(mode='borrowed without booking', authorized_by='owner instruction in this chat; latest short-borrow confirmation 2026-11-09',
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


def register_sleep_exit_wait(cid):
    if not re.fullmatch('[0-9a-f]{64}', cid):
        raise ValueError('sleep exit wait requires a full container ID')
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(5)
        sock.connect('/var/run/docker.sock')
        sock.sendall((f'POST /v1.41/containers/{cid}/wait?condition=next-exit HTTP/1.1\r\n'
                      'Host: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n').encode('ascii'))
        response = http.client.HTTPResponse(sock)
        response.begin()
        if response.status != 200:
            response.close()
            raise RuntimeError('Docker did not acknowledge sleep exit wait')
        return sock, response
    except BaseException:
        sock.close()
        raise


def read_sleep_exit_wait(response):
    raw = response.read(4097)
    if len(raw) > 4096:
        raise RuntimeError('oversized Docker sleep exit receipt')
    result = json.loads(raw)
    if type(result.get('StatusCode')) is not int or result['StatusCode'] != 0 or result.get('Error') is not None:
        raise RuntimeError('Docker sleep exit wait did not report a clean exit')
    return result


def sleep_exit_events(cid, state, since, evidence_path, wait_result):
    raw = subprocess.check_output(
        ['docker', 'events', '--since', since, '--until', str(time.time()),
         '--filter', 'type=container', '--filter', 'container='+cid, '--format', '{{json .}}'],
        timeout=5)
    evidence_path.write_bytes(raw)
    events = [json.loads(line) for line in raw.splitlines()]
    expected = {'name': state['run_id']+'-'+plan.worker_spec('x')['name'],
                'image': plan.IMAGE, 'sllm.run_id': state['run_id'], 'sllm.holder': state['holder'],
                'sllm.purpose': plan.PURPOSE, 'sllm.uid': str(UID)}
    for event in events:
        actor = event['Actor']
        if (event['Type'] != 'container' or actor['ID'] != cid
                or any(actor['Attributes'].get(key) != value for key, value in expected.items())):
            raise RuntimeError('sleep exit event identity differs')
    if [event['Action'] for event in events] != ['create', 'start', 'die', 'destroy']:
        raise RuntimeError('sleep exit event sequence differs')
    times = [event['timeNano'] for event in events]
    if not all(a < b for a, b in zip(times, times[1:])) or events[2]['Actor']['Attributes'].get('exitCode') != '0':
        raise RuntimeError('sleep exit event status or order differs')
    return dict(container_id=cid, wait=wait_result, events_sha256=sha256(evidence_path),
                source='pre-registered Docker next-exit and exact lifecycle events')


def create_and_start(argv, role, state, receipt, *, before_start=None):
    cid = docker(*argv[1:])
    if not re.fullmatch('[0-9a-f]{64}', cid):
        raise RuntimeError('docker create did not return a full container ID')
    guarded_worker = role == 'worker' and state.get('condition') and state.get('verified_slice_cgroup')
    if guarded_worker:
        try:
            created = inspect(cid)
            if (created is None or created.get('Id') != cid or not owned(created, state, role)
                    or created['State']['Status'] != 'created' or created['State']['Pid'] != 0
                    or created['HostConfig']['CgroupParent'] != state['run_id']+'.slice'):
                raise RuntimeError('worker create identity differs before start')
            state['starting_worker_id'] = cid
        finally:
            state[role+'_container_id'] = cid
    else:
        state[role+'_container_id'] = cid
    try:
        write(receipt, state)
        if before_start is not None:
            before_start(cid)
        docker('start', cid)
    finally:
        if guarded_worker:
            state.pop('starting_worker_id', None)
    info = inspect(cid)
    if info is None or not owned(info, state, role) or not info['State']['Running']:
        raise RuntimeError(role+' container not running under this holder')
    if state.get('condition'):
        env = dict(value.split('=', 1) for value in info['Config']['Env'])
        if env.get('SLLM_EVENT_DIR') != plan.worker_spec('x')['env']['SLLM_EVENT_DIR']:
            raise RuntimeError('supplement head/worker event directory differs from the shared C23 boundary')
        cg, = [line[3:] for line in (pathlib.Path('/proc')/str(info['State']['Pid'])/'cgroup').read_text().splitlines()
                if line.startswith('0::')]
        if not cg.startswith(state['verified_slice_cgroup']+'/'):
            raise RuntimeError('supplement container escaped independently guarded private slice')
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


def remove_owned(state, role, *, on_absent=None):
    cid = state.get(role+'_container_id')
    if not cid:
        return 'never created'
    info = inspect(cid)
    if info is None:
        state.setdefault('exit_evidence', {})[role] = dict(container_id=cid, normal_exit_verified=False,
                                                        reason='container absent before exit inspection')
        if on_absent is not None:
            on_absent(cid)
        return 'absent'
    if not owned(info, state, role):
        raise RuntimeError(role+' container identity differs; not touching it')
    before = info['State']
    sleep_contradiction = on_absent is not None and (
        before['Running'] or before['OOMKilled'] or before['ExitCode'] != 0 or bool(before.get('Error')))
    if sleep_contradiction:
        on_absent = None
    evidence = dict(container_id=cid, before=info['State'], stop_returncode=None, normal_exit_verified=False)
    state.setdefault('exit_evidence', {})[role] = evidence
    if info['State']['Running']:
        stopped = subprocess.run(['docker', 'stop', '-t', str(STOP_SECONDS[role]), cid], capture_output=True,
                                 timeout=STOP_SECONDS[role]+60)
        evidence.update(stop_returncode=stopped.returncode, stop_stderr=stopped.stderr.decode(errors='replace'))
    final = inspect(cid)
    if final is None and on_absent is not None:
        on_absent(cid)
        return 'absent'
    if final is None or not owned(final, state, role):
        raise RuntimeError('final container exit identity missing')
    evidence['final'] = final['State']
    code = final['State']['ExitCode']
    evidence['normal_exit_verified'] = (not sleep_contradiction and not final['State']['Running'] and not final['State']['OOMKilled']
        and not final['State'].get('Error') and (code == 0 if role == 'worker' else
        (code == 0 or (info['State']['Running'] and evidence['stop_returncode'] == 0 and code in (1, 143, -15)))))
    try:
        docker('rm', cid)
    except subprocess.CalledProcessError:
        if on_absent is None or not evidence['normal_exit_verified'] or inspect(cid) is not None:
            raise
        on_absent(cid)
        return 'absent'
    if docker('ps', '-aq', '--no-trunc', '--filter', 'id='+cid):
        raise RuntimeError(role+' container remains after removal')
    return 'removed'


def up(args, state=None, *, before_worker_start=None):
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
    condition = getattr(args, 'condition', None)
    if condition:
        config.update(pool_bytes=config['c23_pool_bytes'], measurement_class='independent supplement candidate',
                      request_order=['arithmetic', 'translation', 'fixed32'], condition=condition)
        config['differs_from_formal'] = ['fresh public stack for each condition instead of C23 shared stack',
                                        'independently reviewed supplementary entry, not the original formal gate',
                                        'fresh native-home compilation cache; compilation costs remain charged',
                                        'DEBUG-UNREVIEWED guard namespace retained; no historical formal IDs']
    if state and state.get('startup_only'):
        config.update(pool_bytes=0, chunk_bytes=None, measurement_class='startup monitor control; no worker application')
    deadline = (args.guard_deadline_unix if condition else int(time.time())+args.seconds+plan.DEADLINE_GRACE)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run_id = state['run_id'] if state else 'sllm-debug-'+stamp+'-'+uuid.uuid4().hex[:8]
    holder = state['holder'] if state else uuid.uuid4().hex
    debug_root = HOST/'debug'
    debug_root.mkdir(mode=0o700, exist_ok=True)
    if debug_root.stat().st_uid != UID or debug_root.is_symlink():
        raise RuntimeError('debug root owner or type differs')
    run_root = debug_root/run_id
    if condition and state:
        registered = json.loads((run_root/'controller-start.json').read_text())
        if registered['holder'] != holder or registered['run_id'] != run_id or (run_root/'stack.json').exists():
            raise RuntimeError('supplement preallocated identity differs')
    else:
        run_root.mkdir(mode=0o700)
    state = {} if state is None else state
    state.update(run_id=run_id, holder=holder, purpose=plan.PURPOSE, image_id=plan.IMAGE, mode='head-only' if args.head_only else 'gpu',
                 controller_begin_ns=controller_begin_ns, controller_boot_id=controller_boot_id,
                 controller_clock='time.monotonic_ns on qjhs003 host',
                 lifetime_seconds=args.seconds, deadline_unix=deadline, weight_format=args.weight_format, query=not args.no_query,
                 booking=booking, guard=guard, configuration=config,
                 tool_sha256={p.relative_to(TREE).as_posix(): sha256(p) for p in TOOLS}, status='deploying source')
    if condition:
        state['condition'] = condition
    receipt = run_root/'stack.json'
    with receipt.open('x') as stream:
        json.dump(state, stream, indent=2)
    try:
        state['source'] = copy_source(args.source_tree, run_root, source_receipt)
        active = run_root/'source'  # templates come from the same commit as the code the containers run
        from restore_original_image import restore_with_receipt
        # The image is verified, and restored from the archive if absent, right before each create; nothing here
        # relies on a running container keeping it or on when the node removes images.
        if getattr(args, 'condition', None):
            # A timed supplement does not initiate asynchronous Docker image import.
            # If cleanup removes the image, retain failure and restore outside this batch.
            if docker('image', 'inspect', plan.IMAGE, '--format', '{{.Id}}') != plan.IMAGE:
                raise RuntimeError('supplement image missing')
            state['image'] = dict(head=dict(image_id=plan.IMAGE, restored=False))
        else:
            state['image'] = dict(head=restore_with_receipt(run_root/'image-init-head.json'))
        head_plan = plan.plan(run_id, holder, args.seconds, 'PENDING_OBSERVED_HEAD_IP', args.weight_format, not args.no_query,
                              active=active, deadline_unix=deadline, supplement_condition=condition,
                              supplement_start_sha=sha256(run_root/'controller-start.json') if condition else None,
                              startup_only=state.get('startup_only', False), startup_stage=state.get('startup_stage', 'sleep'))['head']
        head = create_and_start(head_plan['docker_argv'], 'head', state, receipt)
        ip = head['NetworkSettings']['Networks']['bridge']['IPAddress']
        if not ipaddress.ip_address(ip).is_private:
            raise RuntimeError('head address is not private')
        state.update(head_ip=ip, status='head running', head_ready_seconds=wait_head_ready(head['Id']))
        if gpu:
            if getattr(args, 'condition', None):
                if docker('image', 'inspect', plan.IMAGE, '--format', '{{.Id}}') != plan.IMAGE:
                    raise RuntimeError('supplement image missing before worker create')
                state['image']['worker'] = dict(image_id=plan.IMAGE, restored=False)
            else:
                state['image']['worker'] = restore_with_receipt(run_root/'image-init-worker.json')
            worker = plan.plan(run_id, holder, args.seconds, ip, args.weight_format, not args.no_query,
                               active=active, deadline_unix=deadline, supplement_condition=getattr(args, 'condition', None),
                               supplement_start_sha=sha256(run_root/'controller-start.json') if condition else None,
                               startup_only=state.get('startup_only', False), startup_stage=state.get('startup_stage', 'sleep'))['worker']
            if before_worker_start is None:
                create_and_start(worker['docker_argv'], 'worker', state, receipt)
            else:
                create_and_start(worker['docker_argv'], 'worker', state, receipt, before_start=before_worker_start)
            state['status'] = 'worker starting; see status for readiness'
        state['up_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write(receipt, state)
    except BaseException as error:
        state.update(status='failed during up', error=repr(error))
        if condition:
            # measure owns the single cleanup pass and its absolute root deadline.
            write(receipt, state)
            raise
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


def down(args, *, sleep_exit=None):
    state = load_state(args.run_id)
    cleanup_errors = {}
    state['down'] = {}
    for role in ('worker', 'head'):
        try:
            if (role == 'worker' and sleep_exit is not None and state.get('startup_only') is True
                    and state.get('startup_stage') == 'sleep'):
                state['down'][role] = remove_owned(state, role, on_absent=sleep_exit)
            else:
                state['down'][role] = remove_owned(state, role)
        except Exception as error:
            cleanup_errors[role] = repr(error)
    state['cleanup_errors'] = cleanup_errors
    write(HOST/'debug'/state['run_id']/'stack.json', state)
    if cleanup_errors:
        raise RuntimeError('owned cleanup incomplete: '+repr(cleanup_errors))
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
    if state.get('condition') and not all(
            state.get('exit_evidence', {}).get(role, {}).get('normal_exit_verified')
            or (role == 'worker' and sleep_exit and state.get('startup_only') is True
                and state.get('startup_stage') == 'sleep' and state['down']['worker'] == 'absent')
            for role in ('worker', 'head')):
        raise RuntimeError('supplement normal container exit not verified; resources cleaned, failure retained')
    return state


def supplement_sample(state, *, cgroup_retries=None, previous_mid_ns=None):
    """Host sampling, attributed by exact container ID and its host cgroup."""
    row = dict(sample_begin_ns=time.monotonic_ns(), host_meminfo=pathlib.Path('/proc/meminfo').read_text(),
               containers={}, processes=[], stage_timings_ns=[], run_id=state.get('run_id'),
               boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               cgroup_read_retries=[] if cgroup_retries is None else cgroup_retries)
    sample_deadline = row['sample_begin_ns']+5_000_000_000
    if previous_mid_ns is not None:
        sample_deadline = min(sample_deadline, previous_mid_ns+5_000_000_000)
    retry_deadline = None

    def cgroup_snapshot(base, role):
        nonlocal retry_deadline
        last_error = None
        while True:
            if last_error is not None and time.monotonic_ns() >= retry_deadline:
                raise last_error
            values = {}
            try:
                for name in ('memory.current', 'memory.stat', 'memory.events', 'cgroup.procs', 'cpu.stat', 'io.stat'):
                    values[name] = (base/name).read_text()
            except OSError as error:
                if error.errno not in (errno.ENOENT, errno.ESRCH):
                    raise
                now = time.monotonic_ns()
                if retry_deadline is None:
                    retry_deadline = min(now+2_500_000_000, sample_deadline)
                row['cgroup_read_retries'].append(dict(role=role, path=str(base/name), monotonic_ns=now,
                    deadline_ns=retry_deadline, errno=error.errno, filename=error.filename))
                if now >= retry_deadline:
                    raise
                last_error = error
                time.sleep(min(.05, (retry_deadline-now)/1e9))
            else:
                if last_error is not None and time.monotonic_ns() >= retry_deadline:
                    raise last_error
                return values
    for role in ('head', 'worker'):
        cid = state.get(role+'_container_id')
        if not cid:
            continue
        stage_begin = time.monotonic_ns()
        if role == 'worker' and state.get('starting_worker_id') == cid:
            group = state['verified_slice_cgroup']+'/docker-'+cid+'.scope'
            base = pathlib.Path('/sys/fs/cgroup')/group.lstrip('/')
            if base.resolve() != base or not base.is_relative_to('/sys/fs/cgroup'):
                raise RuntimeError('starting worker cgroup escaped the exact guarded path')
            item = dict(container_id=cid, starting=True, pid=None)
            try:
                inode = base.stat().st_ino
            except FileNotFoundError:
                if role in state.get('bound_cgroups', {}):
                    raise RuntimeError('starting worker cgroup disappeared after identity binding')
                item['memory_observation'] = 'startup cgroup not yet present'
            else:
                birth = dict(path=str(base), inode=inode, container_id=cid)
                if state.setdefault('bound_cgroups', {}).setdefault(role, birth) != birth:
                    raise RuntimeError('starting worker cgroup birth identity changed')
                values = cgroup_snapshot(base, role)
                if base.stat().st_ino != inode:
                    raise RuntimeError('starting worker cgroup inode changed during reads')
                item.update(cgroup_path=str(base), raw_cgroup='0::'+group+'\n', cgroup=values)
            row['containers'][role] = item
            row['stage_timings_ns'].append(('worker starting cgroup', stage_begin, time.monotonic_ns()))
            continue
        info = inspect(cid)
        row['stage_timings_ns'].append((role+' inspect', stage_begin, time.monotonic_ns()))
        if info is None:
            row['containers'][role] = dict(container_id=cid, absent=True)
            continue
        if not owned(info, state, role):
            raise RuntimeError('monitor container ownership differs')
        stage_begin = time.monotonic_ns()
        try:
            pid = info['State']['Pid']
            item = dict(container_id=cid, docker_state=info['State'], pid=pid)
            if pid:
                proc = pathlib.Path('/proc')/str(pid)
                before = proc.joinpath('stat').read_text()
                item.update(startticks=int(before.rsplit(')', 1)[1].split()[19]), status=proc.joinpath('status').read_text())
                cg, = [line[3:] for line in proc.joinpath('cgroup').read_text().splitlines() if line.startswith('0::')]
                base = pathlib.Path('/sys/fs/cgroup')/cg.lstrip('/')
                if not base.resolve().is_relative_to(pathlib.Path('/sys/fs/cgroup')):
                    raise RuntimeError('cgroup escaped host mount')
                item['cgroup_path'] = str(base)
                if cid not in base.name:
                    raise RuntimeError('container cgroup path does not bind exact Docker ID')
                birth = dict(path=str(base), inode=base.stat().st_ino, container_id=cid)
                registered = state.setdefault('bound_cgroups', {}).setdefault(role, birth)
                if registered != birth:
                    raise RuntimeError('container cgroup birth identity changed')
                item['cgroup'] = cgroup_snapshot(base, role)
                item['raw_cgroup'] = proc.joinpath('cgroup').read_text()
                if cg != next(line[3:] for line in item['raw_cgroup'].splitlines() if line.startswith('0::')) \
                        or base.stat().st_ino != birth['inode']:
                    raise RuntimeError('container cgroup identity changed during reads')
                row['stage_timings_ns'].append((role+' init and cgroup', stage_begin, time.monotonic_ns()))
                stage_begin = time.monotonic_ns()
                for member in item['cgroup']['cgroup.procs'].split():
                    member_proc = pathlib.Path('/proc')/member
                    try:
                        row['processes'].append(dict(container_id=cid, pid=int(member), stat=(member_proc/'stat').read_text(),
                            status=(member_proc/'status').read_text(), cgroup=(member_proc/'cgroup').read_text(),
                            pid_namespace=(member_proc/'ns/pid').readlink().as_posix()))
                    except OSError as error:
                        if error.errno not in (errno.ENOENT, errno.ESRCH):
                            raise
                        # Birth/disappearance is explicit; it is never a zero-memory sample.
                        row.setdefault('processes_disappeared_during_sample', []).append(
                            dict(pid=int(member), container_id=cid, errno=error.errno, filename=error.filename))
                row['stage_timings_ns'].append((role+' members', stage_begin, time.monotonic_ns()))
                stage_begin = time.monotonic_ns()
                item['smaps_rollup'] = proc.joinpath('smaps_rollup').read_text()
                row['stage_timings_ns'].append((role+' smaps_rollup', stage_begin, time.monotonic_ns()))
                if before.rsplit(')', 1)[1].split()[19] != proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]:
                    raise RuntimeError('monitor PID reused')
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ESRCH):
                raise
            final = inspect(cid)
            deadline = min(time.monotonic()+2, row['sample_begin_ns']/1e9+4)
            rechecks = []
            while final is not None and final['State']['Running'] and time.monotonic() < deadline:
                if not owned(final, state, role) or final['State']['Pid'] != pid:
                    raise RuntimeError('container birth identity changed during exit verification') from error
                if item.get('cgroup_path'):
                    group = pathlib.Path(item['cgroup_path'])
                    if group.exists() and group.stat().st_ino != state['bound_cgroups'][role]['inode']:
                        raise RuntimeError('cgroup inode reused during exit verification') from error
                try:
                    stat = (pathlib.Path('/proc')/str(pid)/'stat').read_text()
                except OSError as missing:
                    if missing.errno not in (errno.ENOENT, errno.ESRCH):
                        raise
                else:
                    if item.get('startticks') is None or int(stat.rsplit(')', 1)[1].split()[19]) != item['startticks']:
                        raise RuntimeError('monitor PID reused or birth proof unavailable') from error
                rechecks.append(dict(monotonic_ns=time.monotonic_ns(), state=final['State']))
                time.sleep(.05)
                final = inspect(cid)
            if final is not None and (not owned(final, state, role) or final['State']['Running']):
                details = dict(role=role, container_id=cid, pid=pid, startticks=item.get('startticks'),
                               cgroup_path=item.get('cgroup_path'), errno=error.errno, filename=error.filename,
                               stage='container init or cgroup read', rechecks=rechecks)
                raise RuntimeError('missing live container process/cgroup remains unverified: '+json.dumps(details)) from error
            item = dict(container_id=cid, absent=final is None, pid=0,
                        docker_state=final['State'] if final else None,
                        disappeared_during_sample=True, memory_observation='missing; not zero', error=repr(error),
                        errno=error.errno, filename=error.filename, rechecks=rechecks)
            row['processes'] = [p for p in row['processes'] if p.get('container_id') != cid]
        row['containers'][role] = item
    stage_begin = time.monotonic_ns()
    row['gpu'] = subprocess.check_output(['/usr/bin/timeout', '-k', '2s', '10s', 'nvidia-smi',
                                        '--query-gpu=index,uuid,memory.total,memory.used,utilization.gpu', '--format=csv,noheader'], text=True, timeout=15)
    row['stage_timings_ns'].append(('GPU devices', stage_begin, time.monotonic_ns()))
    stage_begin = time.monotonic_ns()
    row['gpu_processes'] = subprocess.check_output(['/usr/bin/timeout', '-k', '2s', '10s', 'nvidia-smi',
                                                  '--query-compute-apps=pid,used_memory', '--format=csv,noheader'], text=True, timeout=15)
    row['stage_timings_ns'].append(('GPU processes', stage_begin, time.monotonic_ns()))
    stage_begin = time.monotonic_ns()
    row['disk_stat'] = pathlib.Path('/sys/block/nvme1n1/stat').read_text()
    row['network'] = pathlib.Path('/proc/net/dev').read_text()
    row['gpu_pids'] = sorted({int(line.split(',', 1)[0]) for line in row['gpu_processes'].splitlines()})
    owned_pids = set()
    # Revalidate every GPU PID after NVML, including previously sampled members.
    for pid in row['gpu_pids']:
        previous = [member for member in row['processes'] if member['pid'] == pid]
        proc = pathlib.Path('/proc')/str(pid)
        try:
            stat = (proc/'stat').read_text()
            group = (proc/'cgroup').read_text()
            status = (proc/'status').read_text()
            namespace = (proc/'ns/pid').readlink().as_posix()
            first = stat.rsplit(')',1)[1].split()[19]
            last = (proc/'stat').read_text().rsplit(')',1)[1].split()[19]
            matches = [item for item in row['containers'].values() if item.get('raw_cgroup')==group]
            if first==last and len(matches)==1:
                item = matches[0]
                base = pathlib.Path(item['cgroup_path'])
                registered = next(r for r in state['bound_cgroups'].values() if r['container_id']==item['container_id'])
                if any(member['container_id'] != item['container_id'] or member['cgroup'] != group
                       or member['stat'].rsplit(')', 1)[1].split()[19] != first for member in previous):
                    continue
                if (proc.joinpath('cgroup').read_text() == group
                        and proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19] == first
                        and base.stat().st_ino == registered['inode']):
                    row['processes'] = [member for member in row['processes'] if member['pid'] != pid]
                    row['processes'].append(dict(container_id=item['container_id'], pid=pid, stat=stat,
                        status=status, cgroup=group, pid_namespace=namespace, sampled_after_gpu_query=True))
                    owned_pids.add(pid)
        except OSError:
            # Missing or unreadable identity stays unattributed and fails the monitor gate.
            pass
    row['foreign_gpu_pids'] = [p for p in row['gpu_pids'] if p not in owned_pids]
    row['registered_store_helper'] = None
    worker = row['containers'].get('worker', {})
    if worker.get('pid'):
        row['worker_identity'] = dict(container_id=worker['container_id'], cgroup=worker['raw_cgroup'])
        launch = HOST/'debug'/state['run_id']/'debug-service/store-numa-launch.json'
        if launch.exists():
            from gpu_identity13 import store_identity
            row['registered_store_helper'] = store_identity(json.loads(launch.read_text()), row['worker_identity'],
                                                           row['processes'], row['boot_id'])
    row['retained_cgroups'] = {}
    for role, registered in list(state.get('bound_cgroups', {}).items()):
        base = pathlib.Path(registered['path'])
        try:
            if not base.exists():
                row['retained_cgroups'][role] = dict(registered, absent=True)
            else:
                if base.stat().st_ino != registered['inode']:
                    raise RuntimeError('registered cgroup inode reused')
                row['retained_cgroups'][role] = dict(registered, absent=False,
                    values={name:(base/name).read_text() for name in
                            ('cgroup.events', 'cgroup.procs', 'memory.stat', 'memory.current', 'io.stat')})
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ESRCH):
                raise
            if base.exists():
                raise RuntimeError('retained cgroup lost fields while still present') from error
            row['retained_cgroups'][role] = dict(registered, absent=True, disappeared_during_sample=True)
        if row['containers'].get(role, {}).get('starting') and row['retained_cgroups'][role]['absent']:
            raise RuntimeError('starting worker cgroup disappeared after identity binding')
    if state.get('verified_slice_cgroup'):
        parent = pathlib.Path('/sys/fs/cgroup')/state['verified_slice_cgroup'].lstrip('/')
        row['private_slice_memory_events'] = (parent/'memory.events').read_text()
        state['last_private_slice_memory_events'] = row['private_slice_memory_events']
    row['stage_timings_ns'].append(('remaining resources', stage_begin, time.monotonic_ns()))
    row['last_sample_ns'] = row['monotonic_ns'] = time.monotonic_ns()
    row['sample_end_ns'] = time.monotonic_ns()
    return row


def gpu_memory_by_uuid(raw):
    rows = [[v.strip() for v in line.split(',')] for line in raw.splitlines()]
    if (len(rows) != 8 or any(len(r) not in (3, 5) for r in rows) or [r[0] for r in rows] != [str(i) for i in range(8)]
            or len({r[1] for r in rows}) != 8):
        raise RuntimeError('complete eight-rank NVML device identity required')
    result = {}
    for row in rows:
        uuid, value = row[1], row[3] if len(row) == 5 else row[2]
        fields = value.split()
        if len(fields) != 2 or fields[1] != 'MiB' or not fields[0].isdigit():
            raise RuntimeError('NVML memory value invalid')
        result[uuid] = int(fields[0])
    return result


def require_supplement_release(row, baseline):
    events = dict(line.split() for line in row['private_slice_memory_events'].splitlines())
    if not {'oom', 'oom_kill'} <= events.keys() or any(int(value) for name, value in events.items() if name.startswith('oom')):
        raise RuntimeError('owned slice OOM evidence missing or nonzero')
    if row['gpu_processes'].strip() or gpu_memory_by_uuid(row['gpu']) != baseline:
        raise RuntimeError('GPU process/memory baseline not released')
    if any(not r.get('absent') for r in row['containers'].values()):
        raise RuntimeError('owned containers remain')
    if set(row['retained_cgroups']) != {'head', 'worker'}:
        raise RuntimeError('owned cgroup release identity incomplete')
    for entry in row['retained_cgroups'].values():
        if entry['absent']:
            continue
        values = entry['values']
        events = dict(line.split() for line in values['cgroup.events'].splitlines())
        memory = {k:int(v) for k, v in (line.split() for line in values['memory.stat'].splitlines())}
        if events.get('populated') != '0' or values['cgroup.procs'].strip() or any(
                memory.get(k) != 0 for k in ('anon', 'shmem', 'unevictable')):
            raise RuntimeError('owned cgroup active or anonymous/locked/shared memory retained')


def verify_supplement_review(review, source, startup_only=False, startup_stage='sleep'):
    expected = {p.relative_to(TREE).as_posix():sha256(p) for p in TOOLS}
    if startup_stage not in ('sleep', 'ray') or (startup_stage != 'sleep' and not startup_only):
        raise RuntimeError('invalid startup review stage')
    scope = ('glm-ray-startup-monitor-control-v1' if startup_stage == 'ray' else 'glm-startup-monitor-control-v1') if startup_only else 'glm-controller-supplement-v1'
    if (review.get('scope') != scope or review.get('verdict') != 'PASS'
            or review.get('tool_sha256') != expected or review.get('source_tree_sha256') != source['tree_sha256']
            or review.get('code_commit') != source['code_commit']
            or review.get('work_seconds') != (180 if startup_only else SUPPLEMENT_WORK_SECONDS)
            or review.get('total_seconds') != (900 if startup_only else SUPPLEMENT_TOTAL_SECONDS)):
        raise RuntimeError('current full supplement R1 not approved')


def verify_supplement_window(authorization):
    budget = SUPPLEMENT_START_BUDGET_SECONDS
    if authorization.get('condition') == SUPPLEMENT_CONDITIONS[0]:
        budget *= len(SUPPLEMENT_CONDITIONS)
    start = datetime.datetime.fromisoformat(authorization['window_start'])
    end = datetime.datetime.fromisoformat(authorization['window_end'])
    now = datetime.datetime.now(datetime.timezone.utc)
    if (start.tzinfo is None or end.tzinfo is None or now < start
            or (end-now).total_seconds() < budget):
        raise RuntimeError(f'authorization window lacks full {budget}-second budget')
    return dict(authorization, checked_utc=now.isoformat())


def verify_supplement_attempt(review, review_sha, run, condition, boot, authorization, *, invocation=None):
    attempts = review['proposed_attempts']
    if (tuple(row['condition'] for row in attempts) != SUPPLEMENT_CONDITIONS
            or len({row['run_id'] for row in attempts}) != 4
            or len({row['holder'] for row in attempts}) != 4
            or any(row['status'] != 'not_started' or not re.fullmatch(plan.RUN_PATTERN, row['run_id'])
                   or not re.fullmatch('[0-9a-f]{32}', row['holder']) for row in attempts)
            or review['field_budget']['minimum_remaining_seconds_before_each_attempt'] != SUPPLEMENT_START_BUDGET_SECONDS):
        raise RuntimeError('approved attempt sequence or budget differs')
    index = SUPPLEMENT_CONDITIONS.index(condition)
    attempt = attempts[index]
    if attempt['run_id'] != run:
        raise RuntimeError('approved attempt identity differs')
    expected = dict(run_id=run, condition=condition, holder=attempt['holder'], boot_id=boot,
                    review_sha256=review_sha, node='qjhs003', uid=UID)
    if (any(authorization.get(key) != value for key, value in expected.items())
            or not isinstance(authorization.get('authority_reference'), str)
            or not authorization['authority_reference'].strip()):
        raise RuntimeError('authorization identity differs')
    sealed_finals = authorization.get('prior_final_sha256')
    if (not isinstance(sealed_finals, dict)
            or sealed_finals.keys() != {prior['run_id'] for prior in attempts[:index]}
            or any(not re.fullmatch('[0-9a-f]{64}', digest) for digest in sealed_finals.values())):
        raise RuntimeError('root authorization must bind the exact preceding final receipts')
    debug_root = HOST/'debug'
    if debug_root.is_symlink() or not debug_root.is_dir() or debug_root.stat().st_uid != UID:
        raise RuntimeError('supplement debug directory owner/type differs')
    for offset, prior in enumerate(attempts):
        root = HOST/'debug'/prior['run_id']
        if offset >= index:
            if root.exists() or root.is_symlink():
                raise RuntimeError('current or later attempt already has a run directory')
            continue
        paths = {name: root/name for name in ('controller-dispatch.json', 'controller-start.json',
                                             'controller-result.json', 'controller-final.json')}
        if root.is_symlink() or any(path.is_symlink() for path in paths.values()):
            raise RuntimeError('previous attempt evidence must be in its exact run directory')
        evidence = {}
        for name, path in paths.items():
            with path.open('rb') as stream:
                evidence[name] = stream.read(1024**2+1)
            if len(evidence[name]) > 1024**2:
                raise RuntimeError('previous attempt evidence exceeds its read bound')
        if sealed_finals[prior['run_id']] != hashlib.sha256(evidence['controller-final.json']).hexdigest():
            raise RuntimeError('previous final receipt differs from root authorization')
        dispatch, prior_start, result, final = (json.loads(evidence[name]) for name in paths)
        unit = final['unit']
        begin = int(unit.get('ExecMainStartTimestampMonotonic', '0'))*1000
        end = int(unit.get('ExecMainExitTimestampMonotonic', '0'))*1000
        if (any(prior_start.get(key) != prior[key] for key in ('run_id', 'holder', 'condition'))
                or any(dispatch.get(key) != prior[key] for key in ('run_id', 'holder', 'condition'))
                or prior_start.get('r1_sha256') != review_sha
                or dispatch.get('review_sha256') != review_sha
                or dispatch.get('boot_id') != prior_start.get('boot_id')
                or result.get('success') is not True or final.get('success') is not True
                or final.get('schema') != 'glm-independent-controller-supplement-v1'
                or final.get('attempt_id') != prior['run_id'] or final.get('condition') != prior['condition']
                or final.get('finalizer_source_sha256') != review['reviewed_extra_sha256']['glm/tools/historical_costs.py']
                or unit.get('Id') != prior['run_id']+'-controller.service' or unit.get('MainPID') != '0'
                or (unit.get('Result'), unit.get('ExecMainCode'), unit.get('ExecMainStatus')) != ('success', '1', '0')
                or unit.get('InvocationID') != prior_start['independent_controller_unit']['InvocationID']
                or unit.get('InvocationID') != dispatch.get('invocation_id')
                or not 0 < begin < end <= begin+SUPPLEMENT_TOTAL_SECONDS*10**9
                or begin != prior_start.get('controller_begin_ns') or begin != final.get('controller_begin_ns')
                or end != final.get('controller_end_ns') or not begin < result.get('controller_end_ns', 0) <= end
                or (final.get('metrics') or {}).get('controller_s') != (end-begin)/1e9
                or any(final['inputs'][name]['sha256'] != hashlib.sha256(evidence[name]).hexdigest()
                       for name in ('controller-start.json', 'controller-result.json'))):
            raise RuntimeError('previous attempt lacks a bound independent success receipt')
    window = verify_supplement_window(authorization)
    if invocation is not None:
        root = debug_root/run
        root.mkdir(mode=0o700)
        write(root/'controller-dispatch.json', dict(expected, invocation_id=invocation,
              authorization=authorization, status='dispatched; independent success required before next attempt'))
    return window


def supplement_authorization(run):
    path = pathlib.Path('/run')/(run+'-controller-window.json')
    meta = path.stat()
    if path.is_symlink() or not path.is_file() or meta.st_uid != 0 or meta.st_mode & 0o222:
        raise RuntimeError('controller window receipt must be a root-owned read-only file')
    with path.open('rb') as stream:
        data = stream.read(8193)
    if len(data) > 8192:
        raise RuntimeError('controller window receipt exceeds 8 KiB')
    return json.loads(data)


def verify_controller_unit(properties, unit, pid, cgroup, invocation, slice_cgroup):
    run = unit.removesuffix('-controller.service')
    if (not re.fullmatch(plan.RUN_PATTERN+r'-controller\.service', unit)
            or properties.get('Id') != unit or properties.get('User') != '2147'
            or properties.get('MainPID') != str(pid) or properties.get('KillMode') != 'control-group'
            or properties.get('Transient') != 'yes' or properties.get('Restart') != 'no'
            or properties.get('RemainAfterExit') != 'yes'
            or properties.get('ActiveState') != 'active' or properties.get('SubState') != 'running'
            or properties.get('SendSIGKILL') != 'yes' or properties.get('RuntimeMaxUSec') != '59min 30s'
            or properties.get('TimeoutStopUSec') != '30s' or properties.get('NRestarts') != '0'
            or not re.fullmatch('[0-9a-f]{32}', invocation or '') or properties.get('InvocationID') != invocation
            or properties.get('ControlGroup') != cgroup or properties.get('Slice') != run+'.slice'
            or not slice_cgroup.endswith('/'+run+'.slice') or cgroup != slice_cgroup+'/'+unit
            or str(HERE/'debug_stack.py') not in properties.get('ExecStart', '')):
        raise RuntimeError('independent root-managed controller deadline/identity differs')
    origin = int(properties['ExecMainStartTimestampMonotonic'])*1000
    if origin <= 0 or origin > time.monotonic_ns():
        raise RuntimeError('controller unit origin invalid')
    return origin


def controller_unit(unit):
    if not unit:
        raise RuntimeError('supplement requires its independent root-managed transient service')
    names = ('Id', 'User', 'MainPID', 'KillMode', 'SendSIGKILL', 'RuntimeMaxUSec', 'TimeoutStopUSec',
             'NRestarts', 'InvocationID', 'ControlGroup', 'Slice', 'ExecStart', 'ExecMainStartTimestampMonotonic',
             'Transient', 'Restart', 'ActiveState', 'SubState', 'RemainAfterExit')
    raw = subprocess.check_output(['systemctl', '--system', 'show', unit, '--property='+','.join(names)],
                                  text=True, timeout=15, env=dict(os.environ, LC_ALL='C'))
    properties = dict(line.split('=', 1) for line in raw.splitlines() if '=' in line)
    slice_cgroup = subprocess.check_output(['systemctl', '--system', 'show', properties['Slice'],
                                           '--property=ControlGroup', '--value'], text=True, timeout=15).strip()
    cg, = [line[3:] for line in pathlib.Path('/proc/self/cgroup').read_text().splitlines() if line.startswith('0::')]
    begin_ns = verify_controller_unit(properties, unit, os.getpid(), cg, os.environ.get('INVOCATION_ID'), slice_cgroup)
    properties['verified_slice_cgroup'] = slice_cgroup
    return begin_ns, properties


def check_supplement_resources(sample):
    gib = 1024**3
    memory = sample['host_memory_bytes']
    if sample['hostname'] != 'qjhs3' or sample['uid'] != UID:
        raise RuntimeError('supplement node/user identity differs')
    cgroup = sample['cgroup_filesystem']
    if cgroup['fstype'] != 'cgroup2' or 'memory_localevents' in cgroup['options'].split(','):
        raise RuntimeError('hierarchical private-slice OOM accounting unavailable')
    if (memory['MemTotal'] < 1900*gib or memory['MemAvailable'] < 1700*gib or memory['SwapTotal'] != 0
            or memory['AnonPages'] > 8*gib or any(memory[k] > gib for k in ('Mlocked', 'Unevictable', 'Shmem'))):
        raise RuntimeError('host available/anonymous/locked/shared memory admission failed')
    if (sample['numa_reclaimable_bytes'].get('node0', 0) < 768799145984+64*gib
            or sample['numa_reclaimable_bytes'].get('node1', 0) < 756177645648+64*gib):
        raise RuntimeError('NUMA capacity for pool/hot input plus64GiB margin insufficient')
    gpu = sample['gpu_devices']
    if (len(gpu) != 8 or [r['index'] for r in gpu] != list(range(8)) or len({r['uuid'] for r in gpu}) != 8
            or any(r['name'] not in ('H20-3e', 'NVIDIA H20-3e') or r['total_MiB'] != 143771 or r['used_MiB'] != 0 for r in gpu)
            or sample['gpu_processes'].strip()):
        raise RuntimeError('eight exclusive H20-3e devices unavailable')
    if sample['loadavg1'] >= 8 or sample['read_sectors'][0] != sample['read_sectors'][1] or any(sample['read_inflight']):
        raise RuntimeError('CPU/shared local read IO not quiescent')
    if len(sample['filesystems']) != 2 or any(row['fstype'] != 'xfs' or row['source'] != '/dev/nvme1n1' for row in sample['filesystems']):
        raise RuntimeError('checkpoint and Store must remain on local XFS')


def supplement_resources():
    sample = dict(begin_ns=time.monotonic_ns(), hostname=socket.gethostname(), uid=os.getuid(),
                  loadavg1=os.getloadavg()[0], filesystems=[], numa_reclaimable_bytes={})
    fstype, options = subprocess.check_output(['findmnt', '-n', '-T', '/sys/fs/cgroup', '-o', 'FSTYPE,OPTIONS'],
                                              text=True, timeout=15).split()
    sample['cgroup_filesystem'] = dict(fstype=fstype, options=options)
    text = pathlib.Path('/proc/meminfo').read_text()
    sample['host_memory_bytes'] = {line.split(':', 1)[0]:int(line.split()[1])*1024 for line in text.splitlines()}
    for path in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*/meminfo'):
        values = {line.split()[2].rstrip(':'):int(line.split()[3])*1024 for line in path.read_text().splitlines()}
        sample['numa_reclaimable_bytes'][path.parent.name] = values['MemFree']+values['FilePages']
    raw = subprocess.check_output(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.used',
                                   '--format=csv,noheader,nounits'], text=True, timeout=15)
    sample['gpu_devices'] = []
    for line in raw.splitlines():
        index, uuid, name, total, used = [v.strip() for v in line.split(',')]
        sample['gpu_devices'].append(dict(index=int(index), uuid=uuid, name=name, total_MiB=int(total), used_MiB=int(used)))
    sample['gpu_processes'] = subprocess.check_output(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader'], text=True, timeout=15)
    for name in ('checkpoint', 'store/tp8'):
        source, fstype = subprocess.check_output(['findmnt', '-n', '-T', str(HOST/name), '-o', 'SOURCE,FSTYPE'],
                                                 text=True, timeout=15).split()
        sample['filesystems'].append(dict(path=str(HOST/name), source=source, fstype=fstype))
    sample['read_sectors'], sample['read_inflight'] = [], []
    for number in range(2):
        sample['read_sectors'].append(int(pathlib.Path('/sys/block/nvme1n1/stat').read_text().split()[2]))
        sample['read_inflight'].append(int(pathlib.Path('/sys/block/nvme1n1/inflight').read_text().split()[0]))
        if number == 0:
            time.sleep(1)
    sample['end_ns'] = time.monotonic_ns()
    check_supplement_resources(sample)
    return sample


def verify_root_probe(path, run, boot, origin_ns, slice_cgroup, startup_only=False):
    path = pathlib.Path(path)
    meta, parent = path.stat(), path.parent.stat()
    if (path.is_symlink() or path.parent.is_symlink() or path.parent != pathlib.Path('/run')/run
            or meta.st_uid != 0 or meta.st_mode & 0o222 or parent.st_uid != 0 or parent.st_mode & 0o022):
        raise RuntimeError('root probe evidence is not protected by root ownership')
    proof = json.loads(path.read_text())
    if (proof['run_id'] != run or proof['boot_id'] != boot or not re.fullmatch('[0-9a-f]{32}', proof['holder'])
            or proof['installer_sha256'] != sha256(HERE/'install_debug_guard.sh')
            or proof['guard_sha256'] != sha256(HERE/'debug_guard.py')
            or not 0 <= time.time()-proof['completed_unix'] <= 120
            or proof['host_probe_final']['Result'] != 'timeout'
            or proof['slice_probe_final']['Result'] != 'success' or proof['slice_probe_final']['ExecMainStatus'] != '0'
            or proof['container_final']['State']['Running'] or proof['container_final']['State']['ExitCode'] != 137
            or proof['container_final']['State']['OOMKilled'] or proof['main_slice']['ControlGroup'] != slice_cgroup
            or proof['main_slice']['MemoryMax'] != '1825361100800' or proof['main_slice']['MemorySwapMax'] != '0'
            or proof['main_slice']['TasksMax'] != '8192'):
        raise RuntimeError('actual CPU deadline/slice probe evidence differs')
    expected_slice = run+'.slice'
    service = subprocess.check_output(['systemctl','show',run+'-deadline.service','--property=ExecStart','--value'],
                                      text=True, timeout=15)
    expected_command = 'argv[]=/usr/bin/systemctl kill --kill-who=all --signal=SIGKILL '+expected_slice+' ;'
    if expected_command not in service or subprocess.check_output(['systemctl','is-active',run+'-deadline.timer'],
                                                                  text=True, timeout=15).strip() != 'active':
        raise RuntimeError('actual independent exact-slice deadline is not armed')
    timer_path='/org/freedesktop/systemd1/unit/'+''.join(c if c.isalnum() else '_%02x'%ord(c) for c in run+'-deadline.timer')
    data = json.loads(subprocess.check_output(['busctl','--system','--json=short','get-property',
        'org.freedesktop.systemd1',timer_path,'org.freedesktop.systemd1.Timer','NextElapseUSecMonotonic'],text=True,timeout=15))
    deadline_ns = int(data['data'])*1000
    lower, upper = (660, 900) if startup_only else (3000, 3245)
    if (int(data['data']) != proof['deadline_next_monotonic_usec']
            or (startup_only and proof.get('deadline_delay_seconds') != 900)
            or not origin_ns+lower*10**9 <= deadline_ns <= origin_ns+upper*10**9):
        raise RuntimeError('actual exact-slice deadline does not fit the frozen total budget')
    for item in (proof['host']['process'], proof['setsid_child'], proof['slice_sentinel']['process'], proof['container_process']):
        proc = pathlib.Path('/proc')/str(item['pid'])/'stat'
        if proc.exists() and proc.read_text().rsplit(')',1)[1].split()[19] == item['stat'].rsplit(')',1)[1].split()[19]:
            raise RuntimeError('probe birth identity still alive')
    if inspect(proof['container_final']['Id']) is not None:
        raise RuntimeError('CPU probe container metadata was not removed')
    return proof


def verify_runtime_assets(reference):
    if reference['immutable_image_id'] != plan.IMAGE:
        raise RuntimeError('original GLM image identity changed')
    if (reference['reference_sha256']['native-gold.json'] != '9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0'
            or reference['reference_sha256']['store-restore-validation.json'] != 'e400fb42c4c15aa1c6103b289aba8f42203c8a91ef6f3ee6f6e58c978ce99f56'):
        raise RuntimeError('original GLM gold/restore identity changed')
    files = reference['asset_files']
    if not files or sum(row['bytes'] for row in files) > 16*1024**2:
        raise RuntimeError('bounded historical code/reference asset list missing or oversized')
    for row in files:
        path = pathlib.Path(row['path'])
        if not path.resolve().is_relative_to(HOST) or path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
            raise RuntimeError('frozen runtime asset changed: '+str(path))
    suffixes = {'.py', '.so', '.json', '.yaml', '.yml', '.toml', '.jinja', '.jinja2'}
    for closure in reference['code_closure']:
        root = pathlib.Path(closure['root'])
        if not root.resolve().is_relative_to(HOST):
            raise RuntimeError('runtime code closure escaped workspace')
        actual = sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix in suffixes)
        if actual != closure['paths']:
            raise RuntimeError('unexpected runtime code/config files appeared')


def await_first_monitor(root, errors, run, boot):
    from gpu_identity13 import require_monitor_ready
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        if errors:
            raise RuntimeError('monitor failed before container startup: '+repr(errors))
        try:
            with (root/'supplement-monitor-ready.json').open('rb') as stream:
                raw = stream.read(4*1024**2+1)
        except FileNotFoundError:
            time.sleep(.1)
            continue
        if len(raw) > 4*1024**2:
            raise RuntimeError('host monitor receipt exceeds4MiB')
        snapshot = json.loads(raw)
        require_monitor_ready(snapshot, time.monotonic_ns(), run, boot)
        return snapshot
    raise TimeoutError('no healthy host monitor before container startup')


def measure(args):
    """One independent supplementary condition; never changes a historical ID."""
    preflight = getattr(args, 'preflight', False)
    if not preflight:
        begin_ns, unit = controller_unit(args.controller_unit)
    startup_only = getattr(args, 'startup_only', False)
    startup_stage = getattr(args, 'startup_stage', 'sleep')
    work_seconds, total_seconds = (180, 900) if startup_only else (SUPPLEMENT_WORK_SECONDS, SUPPLEMENT_TOTAL_SECONDS)
    if startup_only and args.condition != 'safetensors_disk_cold':
        raise ValueError('startup control is not a four-condition model attempt')
    if preflight and startup_only:
        raise ValueError('supplement preflight is for full model attempts')
    boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    source = verified_source(args.source_tree)
    review_bytes = pathlib.Path(args.review).read_bytes()
    review = json.loads(review_bytes)
    verify_supplement_review(review, source, startup_only, startup_stage)
    review_sha = hashlib.sha256(review_bytes).hexdigest()
    run = args.controller_unit.removesuffix('-controller.service')
    if not re.fullmatch(plan.RUN_PATTERN, run) or args.controller_unit != run+'-controller.service':
        raise RuntimeError('exact controller unit required')
    if not startup_only:
        authorization = supplement_authorization(run)
        window = verify_supplement_attempt(review, review_sha, run, args.condition, boot, authorization,
                                           invocation=None if preflight else unit['InvocationID'])
        if preflight:
            refuse_shared_state(True)
            resources = supplement_resources()
            window = verify_supplement_window(authorization)
            return dict(success=True, scope='read-only preflight', authorization=window, resources=resources)
    verify_runtime_assets(review['runtime_reference'])
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(args.source_tree))
    import gpu_identity13
    if pathlib.Path(gpu_identity13.__file__).resolve() != pathlib.Path(args.source_tree).resolve()/'gpu_identity13.py':
        raise RuntimeError('monitor helper imported outside reviewed source')
    require_monitor_sample = gpu_identity13.require_monitor_sample
    refuse_shared_state(True)
    guard = guard_state()
    if not guard['active']:
        raise RuntimeError('supplement independent guard not ready')
    # A new actual probe receipt is mandatory; a prior timer snapshot alone is insufficient.
    probe = verify_root_probe(args.guard_probe, run, boot, begin_ns, unit['verified_slice_cgroup'], startup_only)
    if not startup_only and probe['holder'] != authorization['holder']:
        raise RuntimeError('root probe holder differs from approved attempt')
    admission = supplement_resources()
    if docker('image', 'inspect', plan.IMAGE, '--format', '{{.Id}}') != plan.IMAGE:
        raise RuntimeError('restore original image before the supplement, outside timing')
    if not startup_only:
        window = verify_supplement_window(authorization)
    state = dict(run_id=run, holder=probe['holder'], verified_slice_cgroup=unit['verified_slice_cgroup'])
    if startup_only:
        state['startup_only'] = True
        state['startup_stage'] = startup_stage
    debug_root = HOST/'debug'
    if debug_root.is_symlink() or not debug_root.is_dir() or debug_root.stat().st_uid != UID:
        raise RuntimeError('supplement debug directory owner/type differs')
    root = HOST/'debug'/state['run_id']
    if startup_only:
        root.mkdir(mode=0o700)
    initial = dict(state, controller_begin_ns=begin_ns, boot_id=boot, pid=os.getpid(),
                   startticks=int(pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]),
                   condition=args.condition, work_seconds=work_seconds, total_seconds=total_seconds,
                   r1_sha256=review_sha, guard_probe_sha256=sha256(args.guard_probe))
    initial['independent_controller_unit'] = unit
    initial['resource_admission'] = admission
    initial['runtime_reference'] = review['runtime_reference']
    if not startup_only:
        initial['authorization'] = window
    write(root/'controller-start.json', initial)
    stopped = threading.Event()
    monitor_errors = []

    def monitor():
        snapshot, stage, cgroup_retries = None, 'not started', []
        previous_begin = previous_mid = None
        try:
            with (root/'controller-monitor.jsonl').open('x') as stream:
                while not stopped.is_set():
                    snapshot, stage, cgroup_retries = None, 'collect', []
                    snapshot = supplement_sample(state, cgroup_retries=cgroup_retries, previous_mid_ns=previous_mid)
                    stage = 'validate'
                    require_monitor_sample(snapshot['sample_begin_ns'], snapshot['last_sample_ns'], snapshot['sample_end_ns'],
                                           previous_begin, previous_mid)
                    if list(gpu_memory_by_uuid(snapshot['gpu'])) != list(baseline):
                        raise RuntimeError('GPU UUID identity changed during supplement')
                    if snapshot['foreign_gpu_pids']:
                        raise RuntimeError('unattributed GPU processes: '+repr(snapshot['foreign_gpu_pids']))
                    previous_begin, previous_mid = snapshot['sample_begin_ns'], snapshot['last_sample_ns']
                    events = dict(line.split() for line in snapshot['private_slice_memory_events'].splitlines())
                    oom = (not {'oom', 'oom_kill'} <= events.keys()
                           or any(int(value) for name, value in events.items() if name.startswith('oom')))
                    snapshot.update(observers_ok=not oom, history_ok=True,
                                    first_monitor_error='owned slice OOM' if oom else None)
                    payload = json.dumps(snapshot)+'\n'
                    if stream.tell()+len(payload.encode()) > 256*1024**2:
                        raise RuntimeError('bounded supplementary monitor exceeded256MiB')
                    stream.write(payload)
                    stream.flush()
                    if oom:
                        raise RuntimeError('owned slice OOM: '+snapshot['private_slice_memory_events'])
                    write(root/'supplement-monitor-ready.json', snapshot)
                    elapsed_ns = time.monotonic_ns()-snapshot['sample_begin_ns']
                    # The sleep control must observe short Docker start transitions.
                    interval_ns = 200_000_000 if startup_only and startup_stage == 'sleep' else 1_000_000_000
                    stopped.wait(max(0, (interval_ns-elapsed_ns)/1e9))
        except BaseException as error:
            monitor_errors.append(repr(error))
            try:
                write(root/'controller-monitor-rejected.json', dict(snapshot=snapshot, error=repr(error), stage=stage,
                      previous_begin_ns=previous_begin, previous_mid_ns=previous_mid,
                      cgroup_read_retries=cgroup_retries, healthy=False))
            except BaseException as evidence_error:
                monitor_errors.append('monitor failure evidence write: '+repr(evidence_error))

    baseline = gpu_memory_by_uuid(supplement_sample({})['gpu'])
    if any(baseline.values()):
        raise RuntimeError('GPU memory already occupied before supplement')
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    result = dict(initial, status='failed', success=False)
    previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('supplement absolute work deadline')))
    signal.setitimer(signal.ITIMER_REAL, max(.001, work_seconds-(time.monotonic_ns()-begin_ns)/1e9))
    wait_channel = None
    wait_result = None

    def record_absence(cid):
        result['sleep_exit_evidence'] = sleep_exit_events(cid, state, since, root/'sleep-exit-events.jsonl', wait_result)

    def register(cid):
        nonlocal wait_channel
        wait_channel = register_sleep_exit_wait(cid)
        write(root/'sleep-exit-registration.json', dict(container_id=cid, run_id=run,
              holder=state['holder'], boot_id=boot, acknowledged_ns=time.monotonic_ns()))
    try:
        await_first_monitor(root, monitor_errors, state['run_id'], boot)
        args.head_only, args.no_query, args.borrow = False, False, True
        args.seconds, args.booking_id, args.booking_end = 300 if startup_only else work_seconds, None, None
        remaining = work_seconds-(time.monotonic_ns()-begin_ns)/1e9
        if remaining <= 0:
            raise TimeoutError('absolute supplement work budget exhausted before startup')
        args.guard_deadline_unix = int(time.time()+remaining+600)
        args.weight_format = 'safetensors' if args.condition.startswith('safetensors_') else 'serverless_llm'
        if startup_only and startup_stage == 'sleep':
            since = str(time.time())
            up(args, state, before_worker_start=register)
        else:
            up(args, state)
        while True:
            if monitor_errors:
                raise RuntimeError('continuous monitor failed: '+repr(monitor_errors))
            worker = inspect(state['worker_container_id'])
            if wait_channel is not None and (worker is None or not worker['State']['Running']):
                if worker is not None and (not owned(worker, state, 'worker')
                        or worker['State']['ExitCode'] != 0 or worker['State']['OOMKilled'] or worker['State'].get('Error')):
                    raise RuntimeError('sleep worker inspect contradicts normal exit')
                wait_result = read_sleep_exit_wait(wait_channel[1])
                write(root/'sleep-exit-wait.json', wait_result)
                break
            if worker is None:
                raise RuntimeError('supplement worker disappeared')
            if not worker['State']['Running']:
                if startup_only:
                    if worker['State']['ExitCode'] != 0 or worker['State']['OOMKilled']:
                        raise RuntimeError('startup control worker did not finish normally')
                    if startup_stage == 'ray':
                        control = json.loads((root/'debug-service/result.json').read_text())
                        if control.get('ray_control_pass') is not True or control.get('ray_only') is not True:
                            raise RuntimeError('Ray startup control did not pass')
                        result['worker_control'] = control
                    break
                worker_result = json.loads((root/'debug-service/result.json').read_text())
                result['worker'] = worker_result
                if worker['State']['ExitCode'] != 0 or worker_result.get('success') is not True:
                    raise RuntimeError('supplement native lifecycle failed')
                break
            time.sleep(1)
        result.update(status='business and worker exit verified', success=True)
    except BaseException as error:
        result['error'] = repr(error)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if wait_channel is not None:
            for channel in reversed(wait_channel):
                try:
                    channel.close()
                except OSError as error:
                    result.update(success=False, wait_close_error=repr(error))
        try:
            cleanup_args = argparse.Namespace(run_id=state['run_id'])
            released = down(cleanup_args, sleep_exit=record_absence) if wait_result is not None else down(cleanup_args)
            result['exit_evidence'] = released.get('exit_evidence')
            result['all_owned_containers_removed_ns'] = time.monotonic_ns()
        except BaseException as error:
            result.update(success=False, cleanup_error=repr(error))
        try:
            release_begin = None
            previous_io = None
            with (root/'release60.jsonl').open('x') as stream:
                while True:
                    row = supplement_sample(state)
                    if release_begin is None:
                        release_begin = row['monotonic_ns']
                    stream.write(json.dumps(row)+'\n'); stream.flush()
                    require_supplement_release(row, baseline)
                    current_io = {role:entry['values']['io.stat'] for role, entry in row['retained_cgroups'].items()
                                  if not entry['absent']}
                    if previous_io is not None and any(previous_io.get(role) != value for role, value in current_io.items()):
                        raise RuntimeError('owned cgroup IO still changing after exit')
                    previous_io = current_io
                    if (row['monotonic_ns']-release_begin)/1e9 >= 60:
                        break
                    time.sleep(1)
            result['release_observation_s'] = (row['monotonic_ns']-release_begin)/1e9
        except BaseException as error:
            result.update(success=False, release_error=repr(error))
        stopped.set(); thread.join(timeout=150)
        if thread.is_alive() or monitor_errors:
            result.update(success=False, monitor_errors=monitor_errors, monitor_alive=thread.is_alive())
        result.update(controller_end_ns=time.monotonic_ns(), end_boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        result['controller_elapsed_s'] = (result['controller_end_ns']-begin_ns)/1e9 if result['end_boot_id'] == boot else None
        if result['controller_elapsed_s'] is None or result['controller_elapsed_s'] > total_seconds:
            result.update(success=False, budget_or_clock_failure=True)
        result['status'] = 'components passed; awaiting kernel unit exit finalization' if result['success'] else 'failed independent supplement'
        result['last_private_slice_memory_events'] = state.get('last_private_slice_memory_events')
        result['controller_metric_provisional'] = True
        result['controller_finalizer'] = 'historical_costs.py --finalize-controller '+state['run_id']
        if startup_only:
            result.update(measurement_class=startup_stage+' startup monitor control only', model_service_pass=False,
                          publish_controller=False, controller_finalizer=None,
                          status='startup control passed' if result['success'] else 'startup control failed')
        write(root/'controller-result.json', result)
    return result


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
    measurement = sub.add_parser('measure', help='one independently reviewed four-condition supplementary sample; not C23')
    measurement.add_argument('--source-tree', required=True)
    measurement.add_argument('--condition', required=True, choices=SUPPLEMENT_CONDITIONS)
    measurement.add_argument('--review', required=True)
    measurement.add_argument('--guard-probe')
    measurement.add_argument('--controller-unit', required=True)
    measurement.add_argument('--startup-only', action='store_true', help='separately reviewed bounded startup control; never starts Store or model')
    measurement.add_argument('--preflight', action='store_true', help='read-only window, ordering and resource checks before root probe')
    measurement.add_argument('--startup-stage', choices=('sleep', 'ray'), default='sleep', help='separately reviewed diagnostic boundary; Ray stage never starts Store or model')
    for name in ('status', 'down'):
        sub.add_parser(name).add_argument('--run-id', required=True)
    args = parser.parse_args()
    if args.action == 'measure' and not args.preflight and not args.guard_probe:
        parser.error('measure requires --guard-probe outside --preflight')
    outcome = dict(up=up, status=status, down=down, measure=measure)[args.action](args)
    print(json.dumps(outcome, indent=2))
    if args.action == 'measure' and not outcome['success']:
        sys.exit(1)
