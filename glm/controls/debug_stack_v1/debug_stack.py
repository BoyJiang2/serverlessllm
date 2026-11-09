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
         TREE/'controls/service_smoke_v1/docker_args.py', TREE/'tools/restore_original_image.py', TREE/'tools/docker_plan.py')
SUPPLEMENT_WORK_SECONDS = 1800
SUPPLEMENT_TOTAL_SECONDS = 3600


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
        state.setdefault('exit_evidence', {})[role] = dict(container_id=cid, normal_exit_verified=False,
                                                        reason='container absent before exit inspection')
        return 'absent'
    if not owned(info, state, role):
        raise RuntimeError(role+' container identity differs; not touching it')
    evidence = dict(container_id=cid, before=info['State'], stop_returncode=None, normal_exit_verified=False)
    state.setdefault('exit_evidence', {})[role] = evidence
    if info['State']['Running']:
        stopped = subprocess.run(['docker', 'stop', '-t', str(STOP_SECONDS[role]), cid], capture_output=True,
                                 timeout=STOP_SECONDS[role]+60)
        evidence.update(stop_returncode=stopped.returncode, stop_stderr=stopped.stderr.decode(errors='replace'))
    final = inspect(cid)
    if final is None or not owned(final, state, role):
        raise RuntimeError('final container exit identity missing')
    evidence['final'] = final['State']
    code = final['State']['ExitCode']
    evidence['normal_exit_verified'] = (not final['State']['Running'] and not final['State']['OOMKilled']
        and not final['State'].get('Error') and (code == 0 if role == 'worker' else
        (code == 0 or (info['State']['Running'] and evidence['stop_returncode'] == 0 and code in (1, 143, -15)))))
    docker('rm', cid)
    if docker('ps', '-aq', '--no-trunc', '--filter', 'id='+cid):
        raise RuntimeError(role+' container remains after removal')
    return 'removed'


def up(args, state=None):
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
        head_plan = plan.plan(run_id, holder, args.seconds, 'PENDING_OBSERVED_HEAD_IP', active=active, deadline_unix=deadline)['head']
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
                               supplement_start_sha=sha256(run_root/'controller-start.json') if condition else None)['worker']
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
    cleanup_errors = {}
    state['down'] = {}
    for role in ('worker', 'head'):
        try:
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
    if state.get('condition') and not all(state.get('exit_evidence', {}).get(role, {}).get('normal_exit_verified')
                                        for role in ('worker', 'head')):
        raise RuntimeError('supplement normal container exit not verified; resources cleaned, failure retained')
    return state


def supplement_sample(state):
    """Host sampling, attributed by exact container ID and its host cgroup."""
    row = dict(sample_begin_ns=time.monotonic_ns(), host_meminfo=pathlib.Path('/proc/meminfo').read_text(),
               containers={}, processes=[], run_id=state.get('run_id'),
               boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip())
    for role in ('head', 'worker'):
        cid = state.get(role+'_container_id')
        if not cid:
            continue
        info = inspect(cid)
        if info is None:
            row['containers'][role] = dict(container_id=cid, absent=True)
            continue
        if not owned(info, state, role):
            raise RuntimeError('monitor container ownership differs')
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
            item['cgroup'] = {name:(base/name).read_text() for name in
                              ('memory.current', 'memory.stat', 'memory.events', 'cgroup.procs', 'cpu.stat', 'io.stat')}
            item['raw_cgroup'] = proc.joinpath('cgroup').read_text()
            for member in item['cgroup']['cgroup.procs'].split():
                member_proc = pathlib.Path('/proc')/member
                try:
                    row['processes'].append(dict(pid=int(member), stat=(member_proc/'stat').read_text(),
                        status=(member_proc/'status').read_text(), cgroup=(member_proc/'cgroup').read_text(),
                        pid_namespace=(member_proc/'ns/pid').readlink().as_posix()))
                except FileNotFoundError:
                    # Birth/disappearance is explicit; it is never a zero-memory sample.
                    row.setdefault('processes_disappeared_during_sample', []).append(int(member))
            item['smaps_rollup'] = proc.joinpath('smaps_rollup').read_text()
            if before.rsplit(')', 1)[1].split()[19] != proc.joinpath('stat').read_text().rsplit(')', 1)[1].split()[19]:
                raise RuntimeError('monitor PID reused')
        row['containers'][role] = item
    row['gpu'] = subprocess.check_output(['/usr/bin/timeout', '-k', '2s', '10s', 'nvidia-smi',
                                        '--query-gpu=index,uuid,memory.total,memory.used,utilization.gpu', '--format=csv,noheader'], text=True, timeout=15)
    row['gpu_processes'] = subprocess.check_output(['/usr/bin/timeout', '-k', '2s', '10s', 'nvidia-smi',
                                                  '--query-compute-apps=pid,used_memory', '--format=csv,noheader'], text=True, timeout=15)
    row['disk_stat'] = pathlib.Path('/sys/block/nvme1n1/stat').read_text()
    row['network'] = pathlib.Path('/proc/net/dev').read_text()
    row['gpu_pids'] = sorted({int(line.split(',', 1)[0]) for line in row['gpu_processes'].splitlines()})
    owned_pids = {p['pid'] for p in row['processes']}
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
        if not base.exists():
            row['retained_cgroups'][role] = dict(registered, absent=True)
        else:
            if base.stat().st_ino != registered['inode']:
                raise RuntimeError('registered cgroup inode reused')
            row['retained_cgroups'][role] = dict(registered, absent=False,
                values={name:(base/name).read_text() for name in
                        ('cgroup.events', 'cgroup.procs', 'memory.stat', 'memory.current', 'io.stat')})
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


def verify_supplement_review(review, source):
    expected = {p.relative_to(TREE).as_posix():sha256(p) for p in TOOLS}
    if (review.get('scope') != 'glm-controller-supplement-v1' or review.get('verdict') != 'PASS'
            or review.get('tool_sha256') != expected or review.get('source_tree_sha256') != source['tree_sha256']
            or review.get('code_commit') != source['code_commit']
            or review.get('work_seconds') != SUPPLEMENT_WORK_SECONDS
            or review.get('total_seconds') != SUPPLEMENT_TOTAL_SECONDS):
        raise RuntimeError('current full supplement R1 not approved')


def verify_controller_unit(properties, unit, pid, cgroup, invocation, slice_cgroup):
    run = unit.removesuffix('-controller.service')
    if (not re.fullmatch(plan.RUN_PATTERN+r'-controller\.service', unit)
            or properties.get('Id') != unit or properties.get('User') != '2147'
            or properties.get('MainPID') != str(pid) or properties.get('KillMode') != 'control-group'
            or properties.get('Transient') != 'yes' or properties.get('Restart') != 'no'
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
             'Transient', 'Restart', 'ActiveState', 'SubState')
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


def measure(args):
    """One independent supplementary condition; never changes a historical ID."""
    begin_ns, unit = controller_unit(args.controller_unit)
    boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    source = verified_source(args.source_tree)
    review = json.loads(pathlib.Path(args.review).read_text())
    verify_supplement_review(review, source)
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
    probe = json.loads(pathlib.Path(args.guard_probe).read_text())
    if (probe.get('boot_id') != boot or probe.get('guard_sha256') != sha256(HERE/'debug_guard.py')
            or probe.get('removed_by_guard') is not True or not 0 <= time.time()-probe['completed_unix'] <= 900):
        raise RuntimeError('fresh actual supplement guard probe missing')
    admission = supplement_resources()
    if docker('image', 'inspect', plan.IMAGE, '--format', '{{.Id}}') != plan.IMAGE:
        raise RuntimeError('restore original image before the supplement, outside timing')
    state = dict(run_id=args.controller_unit.removesuffix('-controller.service'), holder=uuid.uuid4().hex)
    debug_root = HOST/'debug'
    if debug_root.is_symlink() or not debug_root.is_dir() or debug_root.stat().st_uid != UID:
        raise RuntimeError('supplement debug directory owner/type differs')
    root = HOST/'debug'/state['run_id']
    root.mkdir(mode=0o700)
    initial = dict(state, controller_begin_ns=begin_ns, boot_id=boot, pid=os.getpid(),
                   startticks=int(pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]),
                   condition=args.condition, work_seconds=SUPPLEMENT_WORK_SECONDS, total_seconds=SUPPLEMENT_TOTAL_SECONDS,
                   r1_sha256=sha256(args.review), guard_probe_sha256=sha256(args.guard_probe))
    initial['independent_controller_unit'] = unit
    initial['resource_admission'] = admission
    initial['runtime_reference'] = review['runtime_reference']
    write(root/'controller-start.json', initial)
    stopped = threading.Event()
    monitor_errors = []

    def monitor():
        try:
            previous_begin = previous_mid = None
            with (root/'controller-monitor.jsonl').open('x') as stream:
                while not stopped.is_set():
                    snapshot = supplement_sample(state)
                    require_monitor_sample(snapshot['sample_begin_ns'], snapshot['last_sample_ns'], snapshot['sample_end_ns'],
                                           previous_begin, previous_mid)
                    if list(gpu_memory_by_uuid(snapshot['gpu'])) != list(baseline):
                        raise RuntimeError('GPU UUID identity changed during supplement')
                    previous_begin, previous_mid = snapshot['sample_begin_ns'], snapshot['last_sample_ns']
                    snapshot.update(observers_ok=True, history_ok=True, first_monitor_error=None)
                    payload = json.dumps(snapshot)+'\n'
                    if stream.tell()+len(payload.encode()) > 256*1024**2:
                        raise RuntimeError('bounded supplementary monitor exceeded256MiB')
                    stream.write(payload)
                    stream.flush()
                    write(root/'supplement-monitor-ready.json', snapshot)
                    stopped.wait(1)
        except BaseException as error:
            monitor_errors.append(repr(error))

    baseline = gpu_memory_by_uuid(supplement_sample({})['gpu'])
    if any(baseline.values()):
        raise RuntimeError('GPU memory already occupied before supplement')
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    result = dict(initial, status='failed', success=False)
    previous = signal.signal(signal.SIGALRM, lambda *_: (_ for _ in ()).throw(TimeoutError('supplement absolute work deadline')))
    signal.setitimer(signal.ITIMER_REAL, max(.001, SUPPLEMENT_WORK_SECONDS-(time.monotonic_ns()-begin_ns)/1e9))
    try:
        args.head_only, args.no_query, args.borrow = False, False, True
        args.seconds, args.booking_id, args.booking_end = SUPPLEMENT_WORK_SECONDS, None, None
        remaining = SUPPLEMENT_WORK_SECONDS-(time.monotonic_ns()-begin_ns)/1e9
        if remaining <= 0:
            raise TimeoutError('absolute supplement work budget exhausted before startup')
        args.guard_deadline_unix = int(time.time()+remaining+600)
        args.weight_format = 'safetensors' if args.condition.startswith('safetensors_') else 'serverless_llm'
        up(args, state)
        while True:
            if monitor_errors:
                raise RuntimeError('continuous monitor failed: '+repr(monitor_errors))
            worker = inspect(state['worker_container_id'])
            if worker is None:
                raise RuntimeError('supplement worker disappeared')
            if not worker['State']['Running']:
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
        try:
            released = down(args=argparse.Namespace(run_id=state['run_id']))
            result['exit_evidence'] = released.get('exit_evidence')
            result['all_owned_containers_removed_ns'] = time.monotonic_ns()
        except BaseException as error:
            result.update(success=False, cleanup_error=repr(error))
        try:
            release_begin = time.monotonic_ns()
            previous_io = None
            with (root/'release60.jsonl').open('x') as stream:
                while True:
                    row = supplement_sample(state)
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
        if result['controller_elapsed_s'] is None or result['controller_elapsed_s'] > SUPPLEMENT_TOTAL_SECONDS:
            result.update(success=False, budget_or_clock_failure=True)
        result['status'] = 'passed independent supplement' if result['success'] else 'failed independent supplement'
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
    measurement.add_argument('--condition', required=True, choices=('safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit'))
    measurement.add_argument('--review', required=True)
    measurement.add_argument('--guard-probe', required=True)
    measurement.add_argument('--controller-unit', required=True)
    for name in ('status', 'down'):
        sub.add_parser(name).add_argument('--run-id', required=True)
    args = parser.parse_args()
    print(json.dumps(dict(up=up, status=status, down=down, measure=measure)[args.action](args), indent=2))
