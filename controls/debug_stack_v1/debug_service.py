import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Debug-only native stack inside the worker container: Ray worker, Store, sllm server, one optional gold request.

Self-contained on purpose: the formal entries read workspace.manifest(), which would stamp a formal run identity
onto debug evidence. Process commands and parameters mirror bootstrap_control13.py and validation_control13.py.
Serves until the lifetime timeout or `debug_stack.py down` sends TERM, then tears down like the formal stack.
"""
import json
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


def cpu_list(text):
    cpus = set()
    for part in text.strip().split(','):
        low, _, high = part.partition('-')
        cpus.update(range(int(low), int(high or low)+1))
    return cpus


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
    gold = json.loads(GOLD.read_text())
    pool_bytes, chunk_bytes = pool_for(weight_format, json.loads(RESTORE.read_text()))
    OUT.mkdir(exist_ok=False)
    env = dict(os.environ, STORAGE_PATH='/experiment/store', RAY_TMPDIR=str(RUN/'ray-debug'),
               RAY_USAGE_STATS_ENABLED='0', SLLM_SERVICE_WEIGHT_FORMAT=weight_format)
    address = env['SLLM_PRIVATE_RAY_ADDRESS']
    env['RAY_ADDRESS'] = address
    ip = socket.gethostbyname(socket.gethostname())
    record = dict(status='starting', purpose='DEBUG-UNREVIEWED', weight_format=weight_format, query=query,
                  pool_bytes=pool_bytes, chunk_bytes=chunk_bytes, worker_ip=ip, head_address=address, begin_ns=time.monotonic_ns())
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
        launch('store', [PYTHON, '-u', '-c', STORE_SERVE, env['STORAGE_PATH'], str(chunk_bytes), str(pool_bytes)],
               preexec_fn=lambda: os.sched_setaffinity(0, cpus))

        def store_ready():
            with socket.create_connection(('127.0.0.1', 8073), timeout=1):
                return True
        wait_ready(store_ready, 1800, 'store')
        launch('sllm', [PYTHON, '-u', '-c', 'from sllm.cli._cli_utils import start_server; start_server(host="127.0.0.1",port=8343)'])

        def healthy():
            with urllib.request.urlopen('http://127.0.0.1:8343/health', timeout=2) as response:
                return json.load(response) == {'status': 'ok'}
        wait_ready(healthy, 90, 'sllm')
        record.update(status='control plane healthy', healthy_ns=time.monotonic_ns())
        if query:
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
        for name in ('sllm', 'store', 'ray-worker'):
            process = processes.get(name)
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGINT) if name == 'store' else process.terminate()
        subprocess.run([PYTHON, '-m', 'ray.scripts.scripts', 'stop', '--force'], env=env, timeout=30)
        forced = []
        for name, process in processes.items():
            try:
                process.wait(timeout=STORE_GRACE_SECONDS if name == 'store' else 10)
            except subprocess.TimeoutExpired:
                forced.append(name)
                process.kill()
                process.wait(timeout=5)
        for log in logs:
            log.close()
        record.update(end_ns=time.monotonic_ns(), forced_kill=forced,
                      returncodes={name: process.returncode for name, process in processes.items()})
        write(OUT/'result.json', record)
    # Like bootstrap_control13, only a Store that misses its grace is a failure; other helpers may need a kill.
    return 0 if record['status'] == 'stopped after serving' and 'store' not in forced else 1


if __name__ == '__main__':
    sys.exit(main())
