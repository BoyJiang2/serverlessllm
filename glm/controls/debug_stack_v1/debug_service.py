import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Debug-only native stack inside the worker container: Ray worker, Store, sllm server, one optional gold request.

Self-contained on purpose: the formal entries read workspace.manifest(), which would stamp a formal run identity
onto debug evidence. Process commands and parameters mirror bootstrap_control13.py and validation_control13.py.
Serves until the lifetime timeout or `debug_stack.py down` sends TERM, then tears down like the formal stack.
"""
import json
import math
import hashlib
import pathlib
import signal
import socket
import subprocess
import time
import urllib.request

RUN = pathlib.Path('/r')
OUT = RUN/'debug-service'
PYTHON = '/experiment/.venv/bin/python'
GOLD = pathlib.Path('/experiment/validation/native-gold.json')
RESTORE = pathlib.Path('/experiment/validation/store-restore-validation.json')
MODEL = 'glm51-fp8'
SUPPLEMENT_CONDITIONS = ('safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit')
STORE_GRACE_SECONDS = 240  # budget_control13.STORE_SHUTDOWN_GRACE
STORE_SERVE = ('import asyncio,importlib,sys;'
               'serve=importlib.import_module("sllm_store.server").serve;'
               'asyncio.run(serve(host="0.0.0.0",port=8073,storage_path=sys.argv[1],num_thread=4,'
               'chunk_size=int(sys.argv[2]),mem_pool_size=int(sys.argv[3]),registration_required=True))')


def pool_for(weight_format, restored):
    # Same choice as bootstrap: the full restored pool for Store format, the 64 MiB minimum for safetensors.
    if weight_format == 'serverless_llm':
        return restored['pool_bytes'], restored['chunk_bytes']
    if weight_format == 'safetensors':
        return 67108864, 32*1024*1024
    raise ValueError('weight format must be safetensors or serverless_llm')


def model_config(gold, weight_format):
    backend = dict(gold['engine_config'])
    backend.pop('model')
    backend.pop('load_format')
    backend.update(pretrained_model_name_or_path='/experiment/checkpoint', torch_dtype='bfloat16')
    if weight_format == 'safetensors':
        backend['load_format'] = 'safetensors'
    return {'model': MODEL, 'backend': 'vllm', 'num_gpus': 8, 'backend_config': backend,
            'placement_config': {'local_disk': ['0'], 'memory_pool': []},
            'auto_scaling_config': {'metric': 'concurrency', 'target': 1, 'min_instances': 0,
                                    'max_instances': 1, 'keep_alive': 3600}, 'router_config': {}}


def gold_request(gold, number):
    case = gold['requests'][number]
    return {'model': MODEL, 'input_tokens': case['input_tokens'], 'temperature': 0, 'seed': 20261024,
            'max_tokens': case['max_tokens'], 'ignore_eos': case['ignore_eos'], 'logprobs': 1,
            'return_token_ids': True, 'request_id': 'debug-'+case['name']}


def matches_gold(result, expected):
    choice = result['choices'][0]
    return (choice['token_ids'] == expected['token_ids'] and choice['message']['content'] == expected['text']
            and choice['finish_reason'] == expected['finish_reason'])


def supplement_registration():
    data = (RUN/'controller-start.json').read_bytes()
    if hashlib.sha256(data).hexdigest() != os.environ.get('SLLM_SUPPLEMENT_START_SHA'):
        raise RuntimeError('supplement controller registration changed')
    return json.loads(data)


def serve_supplement_store():
    """Use the old actual-import checks inside the actual Store process before allocation."""
    import asyncio
    import importlib
    from run_store_server13 import verify_store_imports, verify_event_trace
    registration = supplement_registration()
    reference = registration['runtime_reference']
    modules = verify_store_imports(reference['store_import_sha256'])
    trace = verify_event_trace(reference['event_trace_sha256'])
    import torch, vllm
    actual = dict(torch=str(torch.__version__), vllm=str(vllm.__version__), cuda=str(torch.version.cuda))
    if actual != reference['runtime_versions'] or sys.version_info[:2] != (3, 12):
        raise RuntimeError('actual Store Python/Torch/vLLM/CUDA ABI differs')
    backend = {}
    for name, module in [('torch', torch), ('vllm', vllm)]:
        path = pathlib.Path(module.__file__).resolve()
        expected = reference['backend_reference'][name]
        if str(path) != expected['path'] or hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise RuntimeError('actual backend import path or bytes differ: '+name)
        backend[name] = expected
    write(OUT/'store-runtime-imports.json', dict(run_id=registration['run_id'], pid=os.getpid(),
          startticks=pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19],
          boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(), modules=modules,
          event_trace=trace, actual_runtime_versions=actual, actual_backend_imports=backend, python=sys.version,
          scope='actual Store imports before pool allocation; not a mapped-library claim for every GPU rank'))
    storage, chunk, pool = sys.argv[2:]
    if storage != '/experiment/store' or int(pool) != 768799145984 or int(chunk) != 536870912:
        raise RuntimeError('frozen Store pool arguments differ')
    serve = importlib.import_module('sllm_store.server').serve
    asyncio.run(serve(host='0.0.0.0', port=8073, storage_path=storage, num_thread=4,
                      chunk_size=int(chunk), mem_pool_size=int(pool), registration_required=True))


def cpu_list(text):
    cpus = set()
    for part in text.strip().split(','):
        low, _, high = part.partition('-')
        cpus.update(range(int(low), int(high or low)+1))
    return cpus


def supplement_requests(gold, condition, record):
    """One same-run Prep/A/phase record; caller must finish cache admission first."""
    from phase_summary import summarize
    record['condition'] = condition
    record['startup_command_ns'] = time.monotonic_ns()
    if http('/register', model_config(gold, 'safetensors' if condition.startswith('safetensors_') else 'serverless_llm')) != {'status': 'ok'}:
        raise RuntimeError('supplement registration failed')
    record['register_complete_ns'] = time.monotonic_ns()
    for number in (1, 2, 0):
        case = gold['requests'][number]
        body = gold_request(gold, number)
        body['request_id'] = 'supplement-'+condition+'-'+case['name']
        begin = time.monotonic_ns()
        result = http('/v1/chat/completions', body)
        end = time.monotonic_ns()
        write(OUT/(case['name']+'-response.json'), result)
        if not matches_gold(result, gold['results'][number]):
            raise RuntimeError('supplement gold mismatch: '+case['name'])
        choice = result['choices'][0]
        if len(choice['logprobs']) != len(choice['token_ids']) or not all(
                math.isfinite(row[str(token)]['logprob'])
                for token, row in zip(choice['token_ids'], choice['logprobs'], strict=True)):
            raise RuntimeError('supplement invalid logprobs: '+case['name'])
        record.setdefault('requests', []).append(dict(name=case['name'], begin_ns=begin, response_complete_ns=end,
                                                       gold_match=True, request_id=body['request_id']))
        if number == 1:
            record.update(business_trigger_ns=begin, correct_business_response_ns=end)
            phases = summarize(os.environ['SLLM_EVENT_DIR'], record['startup_command_ns'], end, body['request_id'])
            write(OUT/'phases.json', phases)
            record['metrics'] = dict(preparation_s=(record['preparation_complete_ns']-record['preparation_begin_ns'])/1e9,
                                   command_to_correct_business_response_s=(end-record['startup_command_ns'])/1e9,
                                   trigger_to_correct_business_response_s=(end-begin)/1e9)
            record['metrics'].update({'phase_'+name+'_span_s': value['min_begin_to_max_end_seconds']
                                      for name, value in phases['phases'].items()})
        write(OUT/'measurement-progress.json', record)


def prepare_supplement(condition, record):
    """Reuse C23 input closure, NUMA placement and full-list mincore primitives."""
    if condition not in SUPPLEMENT_CONDITIONS:
        raise ValueError('unknown supplement condition')
    from cache_contract13 import frozen_paths
    from store_cache_inputs import store_inputs
    from cache_prepare_v6 import read_hot, wait_read_idle
    from cache_prepare_v8 import prepare_hot_placement, numa_receipt
    from cache_repair_v9 import scan_files, repair_hot
    from sllm_store.client import SllmStoreClient
    source = pathlib.Path(__file__).resolve().parent
    root = pathlib.Path('/experiment')
    checkpoint, store = root/'checkpoint', root/'store/vllm/glm51-fp8'
    # Both closures and metadata are checked before final eviction / admission.
    sf_paths = frozen_paths(checkpoint, source/'cache-inputs13.json')
    metadata = json.loads((source/'store-metadata-inputs.json').read_text())
    raw_bytes = (store/'raw-disk-verification.json').read_bytes()
    expected_raw = supplement_registration()['runtime_reference']['reference_sha256']['raw-disk-verification.json']
    if hashlib.sha256(raw_bytes).hexdigest() != expected_raw:
        raise RuntimeError('raw Store verification receipt changed')
    raw = json.loads(raw_bytes)
    store_paths = store_inputs(store, checkpoint, raw['ranks'], metadata['files'])
    client = SllmStoreClient()
    for rank in range(8):
        if client.register_model(f'vllm/glm51-fp8/rank_{rank}') <= 0:
            raise RuntimeError('Store rank registration failed')
    record['preparation_begin_ns'] = time.monotonic_ns()
    if condition == 'store_pool_hit':
        for rank in range(8):
            name = f'vllm/glm51-fp8/rank_{rank}'
            if client.load_into_cpu(name) is False or not client.wait_until_in_cpu(name, timeout=600):
                raise RuntimeError('Store pool hit confirmation failed: '+name)
        record['pool_confirmed_ranks'] = list(range(8))
    paths = sf_paths if condition.startswith('safetensors_') else store_paths
    dest = OUT/'cache-admission'
    dest.mkdir()
    begin = time.monotonic_ns()
    placement = None
    if condition == 'safetensors_disk_hot':
        nodes = {name: value for name, value in numa_receipt()['node_meminfo'].items() if name != 'node0'}
        placement = prepare_hot_placement(paths, root, nodes, dest)
        reads = read_hot(paths)
        write(dest/'reads.json', reads)
        if {r['file'] for r in reads} != {str(p) for p in paths} or any(
                r['bytes_read'] != pathlib.Path(r['file']).stat().st_size for r in reads):
            raise RuntimeError('hot input read closure differs')
        allowed = set(placement['reader_cpu_affinity'])
        if placement['node'] != 'node1' or any(r['cpu_begin'] not in allowed or r['cpu_end'] not in allowed for r in reads):
            raise RuntimeError('hot reader NUMA placement differs')
        wait_read_idle(time.time()+120+30)
    elif condition != 'store_pool_hit':
        for path in paths:
            with path.open('rb') as stream:
                os.posix_fadvise(stream.fileno(), 0, path.stat().st_size, os.POSIX_FADV_DONTNEED)
    # Store inputs include shared checkpoint metadata; use the common /experiment base.
    rows = scan_files(paths, root, dest, begin)
    history = []
    if condition == 'safetensors_disk_hot':
        rows, history = repair_hot(paths, root, dest, rows, set(placement['reader_cpu_affinity']), begin, time.time()+600)
    passed = (all(r['resident_pages'] == r['pages'] for r in rows) if condition.endswith('_hot')
              else True if condition == 'store_pool_hit' else all(r['resident_pages'] == 0 for r in rows))
    write(dest/'summary.json', dict(condition=condition, files=rows, passed=passed,
                                   pool_confirmed_ranks=record.get('pool_confirmed_ranks'),
                                   placement=placement, repair_history=history))
    if not passed:
        raise RuntimeError('supplement cache admission failed')
    record['preparation_complete_ns'] = time.monotonic_ns()


def fresh_host_snapshot(record, earliest_ns):
    from gpu_identity13 import require_monitor_ready
    stop = time.monotonic()+15
    while time.monotonic() < stop:
        try:
            with (RUN/'supplement-monitor-ready.json').open('rb') as stream:
                data = stream.read(4*1024**2+1)
            if len(data) > 4*1024**2:
                raise RuntimeError('host monitor receipt exceeds4MiB')
            snapshot = json.loads(data)
        except FileNotFoundError:
            time.sleep(.1)
            continue
        require_monitor_ready(snapshot, time.monotonic_ns(), record['run_id'], record['boot_id'])
        if snapshot['sample_begin_ns'] >= earliest_ns and snapshot.get('registered_store_helper'):
            return snapshot
        time.sleep(.1)
    raise TimeoutError('fresh bound Store helper host snapshot unavailable')


def auxiliary_baseline(record):
    from gpu_identity13 import require_helper_binding, require_helper_only
    launch = record['resident_pool_identity']
    snapshot = fresh_host_snapshot(record, launch['monotonic_ns'])
    helper = snapshot['registered_store_helper']
    require_helper_binding(helper, launch, snapshot['worker_identity'], record['boot_id'])
    require_helper_only(snapshot, helper, time.monotonic_ns(), record['run_id'], record['boot_id'])
    baseline = [int(line.split(',')[3].strip().split()[0]) for line in snapshot['gpu'].splitlines()]
    if len(baseline) != 8:
        raise RuntimeError('auxiliary GPU baseline does not cover8 devices')
    record.update(helper_gpu_identity=helper, auxiliary_gpu_baseline_MiB=baseline)
    write(OUT/'auxiliary-gpu-baseline.json', dict(snapshot=snapshot, helper=helper, memory_MiB=baseline))


def observe_deleted(record):
    from accept_control13 import validate_native_idle
    record['delete_command_ns'] = time.monotonic_ns()
    if http('/delete', {'model': MODEL}) != {'status': 'deleted model '+MODEL}:
        raise RuntimeError('supplement model deletion failed')
    record['delete_complete_ns'] = time.monotonic_ns()
    rows = []
    with (OUT/'idle60.jsonl').open('x') as stream:
        for second in range(61):
            snapshot = fresh_host_snapshot(record, record['delete_complete_ns'])
            shm = os.statvfs('/dev/shm')
            cg = pathlib.Path('/sys/fs/cgroup')
            row = dict(second=second, monotonic_ns=time.monotonic_ns(), delete_ack_ns=record['delete_complete_ns'],
                       gpu=snapshot['gpu'], gpu_processes=snapshot['gpu_processes'], host_gpu_snapshot=snapshot,
                       memory_current=int((cg/'memory.current').read_text()),
                       memory_stat=(cg/'memory.stat').read_text(), cpu_stat=(cg/'cpu.stat').read_text(),
                       pids_current=(cg/'pids.current').read_text(),
                       shm_used_bytes=(shm.f_blocks-shm.f_bfree)*shm.f_frsize)
            rows.append(row)
            stream.write(json.dumps(row)+'\n')
            stream.flush()
            if second < 60:
                time.sleep(1)
    validate_native_idle(rows, record['auxiliary_gpu_baseline_MiB'], record['helper_gpu_identity'],
                         record['run_id'], record['boot_id'])
    record['idle_observation_seconds'] = (rows[-1]['monotonic_ns']-rows[0]['monotonic_ns'])/1e9
    write(OUT/'measurement-progress.json', record)


def require_resident_pool(client, process, pool_bytes, chunk_bytes, cpus):
    """The same concrete pool checks as bootstrap_control13, not TCP readiness."""
    from listener_control13 import listener
    from store_numa_exec_v13 import audit_store_threads
    actual = client.get_server_config()
    if actual != {'mem_pool_size':pool_bytes, 'chunk_size':chunk_bytes}:
        raise RuntimeError('actual Store pool configuration differs')
    proc = pathlib.Path('/proc')/str(process.pid)
    first_stat = (proc/'stat').read_text()
    status = (proc/'status').read_text()
    rss = int(next(line.split()[1] for line in status.splitlines() if line.startswith('VmRSS:')))*1024
    if rss < pool_bytes or 'Memory pool created with 716GB' not in (OUT/'store.log').read_text():
        raise RuntimeError('actual Store pool residency not established')
    socket_owner = listener(process.pid, 8073)
    threads = audit_store_threads(process.pid, sorted(cpus))
    second_stat = (proc/'stat').read_text()
    start_ticks = first_stat.rsplit(')', 1)[1].split()[19]
    if start_ticks != second_stat.rsplit(')', 1)[1].split()[19] or process.poll() is not None:
        raise RuntimeError('Store birth identity changed while checking pool')
    launch = dict(pid=process.pid, pid_namespace=(proc/'ns/pid').readlink().as_posix(),
                  start_ticks=start_ticks, boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                  node='node0', target_cpu_affinity=sorted(cpus), monotonic_ns=time.monotonic_ns())
    write(OUT/'store-numa-launch.json', launch)
    write(OUT/'pool-ready.json', dict(pool_bytes=pool_bytes, chunk_bytes=chunk_bytes, store_pid=process.pid,
                                    rpc_config=actual, rss_bytes=rss, listener=socket_owner, threads=threads,
                                    identity=launch))
    return launch


def stop_processes(processes, env, logs, record, strict=False):
    """Preserve all cleanup outcomes even if one shutdown operation fails."""
    errors, forced = [], []
    unexpected = {name:p.poll() for name, p in processes.items() if p.poll() is not None}
    try:
        for name in ('sllm', 'store', 'ray-worker'):
            process = processes.get(name)
            try:
                if process is not None and process.poll() is None:
                    process.send_signal(signal.SIGINT) if name == 'store' else process.terminate()
            except BaseException as error:
                errors.append(dict(stage='signal', process=name, error=repr(error)))
        try:
            record['ray_stop_returncode'] = subprocess.run(
                [PYTHON, '-m', 'ray.scripts.scripts', 'stop', '--force'], env=env, timeout=30).returncode
            if record['ray_stop_returncode'] != 0:
                errors.append(dict(stage='ray stop', returncode=record['ray_stop_returncode']))
        except BaseException as error:
            errors.append(dict(stage='ray stop', error=repr(error)))
        for name, process in processes.items():
            try:
                process.wait(timeout=STORE_GRACE_SECONDS if name == 'store' else 10)
            except subprocess.TimeoutExpired:
                forced.append(name)
                try:
                    process.kill()
                    process.wait(timeout=5)
                except BaseException as error:
                    errors.append(dict(stage='forced wait', process=name, error=repr(error)))
            except BaseException as error:
                errors.append(dict(stage='wait', process=name, error=repr(error)))
    finally:
        for log in logs:
            try:
                log.close()
            except BaseException as error:
                errors.append(dict(stage='log close', error=repr(error)))
        record.update(end_ns=time.monotonic_ns(), forced_kill=forced, shutdown_errors=errors,
                      unexpected_pre_cleanup_exits=unexpected,
                      returncodes={name:p.returncode for name, p in processes.items()})
        if strict:
            allowed = {'store':(0,), 'sllm':(0, -signal.SIGTERM), 'ray-worker':(0, -signal.SIGTERM)}
            record['controlled_exit_rules'] = allowed
            record['success'] = (record['status'] == 'supplement business and instance deletion completed'
                and not forced and not errors and not unexpected and set(record['returncodes']) == set(allowed)
                and all(code in allowed[name] for name, code in record['returncodes'].items()))
        write(OUT/'result.json', record)
    return forced


def write(path, record):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    temporary.replace(path)


def http(path, body=None):
    request = urllib.request.Request('http://127.0.0.1:8343'+path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=1800) as response:
        assert response.status == 200
        return json.load(response)


def main():
    weight_format = os.environ['SLLM_DEBUG_WEIGHT_FORMAT']
    query = os.environ['SLLM_DEBUG_QUERY'] == '1'
    condition = os.environ.get('SLLM_SUPPLEMENT_CONDITION')
    if condition is not None and (condition not in SUPPLEMENT_CONDITIONS or not query or
            weight_format != ('safetensors' if condition.startswith('safetensors_') else 'serverless_llm')):
        raise ValueError('supplement requires matching format, condition and gold queries')
    if condition:
        controller = supplement_registration()
        reference = controller['runtime_reference']
        if hashlib.sha256(GOLD.read_bytes()).hexdigest() != reference['reference_sha256']['native-gold.json']:
            raise RuntimeError('frozen native gold changed')
        if hashlib.sha256(RESTORE.read_bytes()).hexdigest() != reference['reference_sha256']['store-restore-validation.json']:
            raise RuntimeError('frozen Store restore configuration changed')
    gold = json.loads(GOLD.read_text())
    restored = json.loads(RESTORE.read_text())
    if condition and (restored['pool_bytes'] != 768799145984 or restored['chunk_bytes'] != 536870912):
        raise RuntimeError('frozen716GiB pool geometry changed')
    pool_bytes, chunk_bytes = pool_for('serverless_llm' if condition else weight_format, restored)
    OUT.mkdir(exist_ok=False)
    env = dict(os.environ, STORAGE_PATH='/experiment/store', RAY_TMPDIR=str(RUN/'ray-debug'),
               RAY_USAGE_STATS_ENABLED='0', SLLM_SERVICE_WEIGHT_FORMAT=weight_format)
    address = env['SLLM_PRIVATE_RAY_ADDRESS']
    env['RAY_ADDRESS'] = address
    ip = socket.gethostbyname(socket.gethostname())
    record = dict(status='starting', purpose='DEBUG-UNREVIEWED', weight_format=weight_format, query=query,
                  pool_bytes=pool_bytes, chunk_bytes=chunk_bytes, worker_ip=ip, head_address=address, begin_ns=time.monotonic_ns())
    if condition:
        if controller['condition'] != condition:
            raise RuntimeError('supplement controller/worker condition identity differs')
        record.update(condition=condition, measurement_class='independent supplement candidate, not C23',
                      run_id=controller['run_id'], holder=controller['holder'],
                      boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                      pid=os.getpid(), startticks=int(pathlib.Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19]))
    processes, logs = {}, []
    ready = False

    def launch(name, args, **options):
        log = (OUT/(name+'.log')).open('x')
        logs.append(log)
        processes[name] = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, env=env, **options)

    def wait_ready(check, seconds, name):
        stop = time.monotonic()+seconds
        while time.monotonic() < stop:
            if processes[name].poll() is not None:
                raise RuntimeError(f'{name} exited {processes[name].returncode}')
            try:
                if check():
                    return
            except (OSError, ValueError):
                pass
            time.sleep(1)
        raise TimeoutError(name+' readiness timeout')

    def stop(signum, frame):
        raise SystemExit('stopped by signal %d' % signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        launch('ray-worker', [PYTHON, '-m', 'ray.scripts.scripts', 'start', '--address='+address, '--node-ip-address='+ip,
                              '--num-cpus=16', '--num-gpus=8', '--resources={"worker_node":1,"worker_id_0":1}',
                              '--min-worker-port=22000', '--max-worker-port=22999',
                              '--object-store-memory=268435456', '--disable-usage-stats', '--block'])
        wait_ready(lambda: 'Ray runtime started.' in (OUT/'ray-worker.log').read_text(), 60, 'ray-worker')
        # store_numa_exec_v13 pins only the Store to node-0 CPUs; do the same without its formal receipt.
        cpus = cpu_list(pathlib.Path('/sys/devices/system/node/node0/cpulist').read_text()) & os.sched_getaffinity(0)
        assert cpus
        record['store_cpu_affinity'] = sorted(cpus)
        store_argv = ([PYTHON, '-u', str(pathlib.Path(__file__)), 'serve-store'] if condition else [PYTHON, '-u', '-c', STORE_SERVE])
        launch('store', store_argv+[env['STORAGE_PATH'], str(chunk_bytes), str(pool_bytes)],
               preexec_fn=lambda: os.sched_setaffinity(0, cpus))

        def store_ready():
            with socket.create_connection(('127.0.0.1', 8073), timeout=1):
                return True
        wait_ready(store_ready, 1800, 'store')
        if condition:
            from sllm_store.client import SllmStoreClient
            record['resident_pool_identity'] = require_resident_pool(
                SllmStoreClient(), processes['store'], pool_bytes, chunk_bytes, cpus)
        launch('sllm', [PYTHON, '-u', '-c', 'from sllm.cli._cli_utils import start_server; start_server(host="127.0.0.1",port=8343)'])

        def healthy():
            with urllib.request.urlopen('http://127.0.0.1:8343/health', timeout=2) as response:
                return json.load(response) == {'status': 'ok'}
        wait_ready(healthy, 90, 'sllm')
        record.update(status='control plane healthy', healthy_ns=time.monotonic_ns())
        if condition:
            auxiliary_baseline(record)
            prepare_supplement(condition, record)
            supplement_requests(gold, condition, record)
            observe_deleted(record)
            record['status'] = 'supplement business and instance deletion completed'
        elif query:
            number = int(os.environ.get('SLLM_DEBUG_GOLD_CASE', '1'))
            assert http('/register', model_config(gold, weight_format)) == {'status': 'ok'}
            begin = time.monotonic_ns()
            result = http('/v1/chat/completions', gold_request(gold, number))
            write(OUT/'query-result.json', result)
            match = matches_gold(result, gold['results'][number])
            record.update(gold_case=gold['requests'][number]['name'], gold_match=match,
                          query_seconds=(time.monotonic_ns()-begin)/1e9,
                          status='serving; gold match' if match else 'serving; GOLD MISMATCH')
        write(OUT/'ready.json', record)
        if not condition:
            ready = True
            while all(process.poll() is None for process in processes.values()):
                time.sleep(5)
            raise RuntimeError('stack process exited: '+json.dumps({n: p.returncode for n, p in processes.items()}))
    except BaseException as error:
        record['stop_reason'] = repr(error)
        # A TERM after readiness is the normal end of a debug session, not a failure.
        record['status'] = 'stopped after serving' if ready and isinstance(error, SystemExit) else 'failed'
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        forced = stop_processes(processes, env, logs, record, strict=bool(condition))
    # Like bootstrap_control13, only a Store that misses its grace is a failure; other helpers may need a kill.
    if condition:
        return 0 if record['success'] else 1
    return 0 if record['status'] == 'stopped after serving' and 'store' not in forced and not record['shutdown_errors'] else 1


if __name__ == '__main__':
    if len(sys.argv) == 5 and sys.argv[1] == 'serve-store':
        serve_supplement_store()
    else:
        sys.exit(main())
