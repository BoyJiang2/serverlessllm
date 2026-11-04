import hashlib
import importlib.util
import json
import pathlib
import shlex
import subprocess
import time
import types
import urllib.request

OUT = pathlib.Path('outputs')
MODELS = '/data/brick1/boyjiang/ServerlessLLM/models/'
FIX = MODELS + '.generation-config-parity-20261104/transformers_buffers_compat.py'

def ssh(command):
    return subprocess.check_output(['ssh', 'qjhs002', command])

def write(path, data):
    path.write_text(json.dumps(data, indent=2), encoding='utf-8')

def booking(budget):
    path = pathlib.Path('C:/Users/23520/.codex/skills/gputime-booking-agent/scripts/gputime_agent.py')
    spec = importlib.util.spec_from_file_location('booking', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    snapshot = module.request('GET', module.DEFAULT_BASE_URL + '/api/agent/snapshot', token=module.load_token(types.SimpleNamespace(token_file=str(module.default_token_file()))))
    now = time.time() * 1000
    rows = [row for row in snapshot['bookings'] if row['resource_id'] == 'h20/qjhs002' and row['user'] == 'boyjiang' and row['start_ms'] <= now < row['end_ms']]
    assert any(row['end_ms'] - now > budget * 1000 for row in rows), 'insufficient booking budget'
    return dict(checked_epoch=time.time(), active_booking=rows, max_total_budget_seconds=budget)

def upload(folder, remote):
    ssh('mkdir -p ' + shlex.quote(remote + '/scripts') + ' ' + shlex.quote(remote + '/storage'))
    manifest = {}
    for path in folder.iterdir():
        if path.suffix not in ('.py', '.sh') and path.name != 'sllm-store':
            continue
        target = remote + '/scripts/' + path.name
        subprocess.run(['scp', str(path), 'qjhs002:' + target], check=True, capture_output=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert ssh('sha256sum ' + shlex.quote(target)).decode().split()[0] == digest
        manifest[path.name] = digest
    if 'sllm-store' in manifest:
        ssh('chmod +x ' + shlex.quote(remote + '/scripts/sllm-store'))
    write(folder / 'source-manifest.json', manifest)

def run(folder, command, budget, admission):
    assert ssh('nvidia-smi -i 7 --query-gpu=memory.used --format=csv,noheader,nounits').strip() == b'0'
    assert hashlib.sha256((OUT / 'generation-config-parity/transformers_buffers_compat.py').read_bytes()).hexdigest() == ssh('sha256sum ' + FIX).decode().split()[0]
    cid = ssh(shlex.join(command)).decode().strip()
    assert len(cid) == 64
    write(folder / 'registration.json', dict(cid=cid, command=command, max_budget_seconds=budget, admission=admission, sdk_sha256='8115dbbcc9310bd93009f9fa66c93ee24f6beb581de9e1c9fceabce53bf11e23', independent_guard='native systemd scope RuntimeMaxUSec; previous CPU guard verified', source_commit='dac95346fded9f721ae5f5f46157244b53464dbb'))
    ssh('docker start ' + cid)
    start = time.monotonic()
    with (folder / 'progress.jsonl').open('w') as output:
        while True:
            inspected = json.loads(ssh('docker inspect ' + cid))[0]
            state = inspected['State']
            row = dict(elapsed=time.monotonic() - start, status=state['Status'], exit_code=state['ExitCode'])
            output.write(json.dumps(row) + '\n'); output.flush()
            print(folder.name, json.dumps(row), flush=True)
            if not state['Running']:
                break
            if row['elapsed'] > budget:
                ssh('docker stop --time 5 ' + cid)
                raise RuntimeError('native guard did not finish within complete budget')
            time.sleep(25)
    release_start = time.monotonic()
    with (folder / 'final-release.jsonl').open('w') as output:
        while True:
            used = int(ssh('nvidia-smi -i 7 --query-gpu=memory.used --format=csv,noheader,nounits').strip())
            scope = '/sys/fs/cgroup/system.slice/docker-' + cid + '.scope'
            removed = subprocess.run(['ssh', 'qjhs002', 'test ! -e ' + shlex.quote(scope)]).returncode == 0
            row = dict(elapsed=time.monotonic() - release_start, cid=cid, gpu7_used_mib=used, scope_removed=removed, pid=state['Pid'], exit_code=state['ExitCode'])
            output.write(json.dumps(row) + '\n'); output.flush()
            assert used == 0 and removed and state['Pid'] == 0, 'release check failed'
            if row['elapsed'] >= 60:
                break
            time.sleep(5)
    write(folder / 'final-inspect.json', inspected)
    assert state['ExitCode'] == 0 and not state['OOMKilled'], 'benchmark failed; no automatic retry'

def retrieve(folder, remote, phase, n):
    rows = []
    for fmt in ('safetensors', 'sllm'):
        name = f'meta-llama_Llama-3.1-8B-Instruct_{fmt}_{n}_{phase}.json'
        data = ssh('cat ' + shlex.quote(remote + '/results/' + phase + '/' + name))
        path = folder / 'results' / phase / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data); rows.append(json.loads(data))
    assert len(rows[0]) == len(rows[1]) == n
    for left, right in zip(*rows):
        for key in ('model_name', 'generation_config', 'sampling_seed', 'input_token_ids', 'generated_token_ids'):
            assert left[key] == right[key], ('paired mismatch', phase, key, left['sampling_seed'])
        if n == 1:
            assert left['parameter_sha256'] == right['parameter_sha256']
    return rows

admission = booking(6600)
diag = OUT / 'llama31-buffer-parity-diagnostic-retry1'; diag.mkdir(exist_ok=True)
qwen = OUT / 'qwen3-fox50-buffer-parity'
llama = OUT / 'llama31-buffer-parity-final'
diag_remote = MODELS + '.llama31-buffer-parity-diagnostic-retry1-20261104'
qwen_remote = MODELS + '.qwen3-fox50-buffer-parity-20261104'
llama_remote = MODELS + '.llama31-buffer-parity-final-20261104'
ssh('mkdir -p ' + shlex.quote(diag_remote + '/storage'))
upload(qwen, qwen_remote)
upload(llama, llama_remote)
original = json.loads((OUT / 'llama31-generation-parity/launch-registration.txt').read_text().splitlines()[1])['command']
diag_command = [x.replace('llama31-generation-config-parity-20261104', 'llama31-buffer-parity-diagnostic-retry1-20261104').replace('RuntimeMaxUSec=uint64 3600000000', 'RuntimeMaxUSec=uint64 600000000').replace('/transformers_compat.py', '/transformers_buffers_compat.py') for x in original]
diag_command = [x.replace('src=' + MODELS + '.llama31-generation-parity-20261104,dst=/bench', 'src=' + diag_remote + ',dst=/bench').replace(MODELS + '.llama31-generation-parity-20261104/scripts', MODELS + '.llama31-buffer-parity-diagnostic-20261104/scripts') for x in diag_command]
run(diag, diag_command, 780, admission)
retrieve(diag, diag_remote, 'random', 1)
write(diag / 'verified-summary.json', dict(status='PAIRED_FAILURE_SEED_VERIFIED', seed=20261127, full_parameter_hash_equal=True, buffers_equal=True, token_ids_equal=True))
print('DIAGNOSTIC_PASSED', flush=True)
original_q = json.loads((OUT / 'qwen3-fox50-parity/launch-registration.txt').read_text().splitlines()[1])['command']
qcommand = [x.replace('.qwen3-fox50-parity-20261104', '.qwen3-fox50-buffer-parity-20261104').replace('qwen3-fox50-generation-parity-20261104', 'qwen3-fox50-buffer-parity-20261104').replace('/transformers_compat.py', '/transformers_buffers_compat.py') for x in original_q]
run(qwen, qcommand, 1980, booking(5760))
names = json.loads(ssh("python3 -c " + shlex.quote('import pathlib,json;print(json.dumps([p.name for p in pathlib.Path("' + qwen_remote + '/results").glob("*.json")]))')))
rows = []
for name in names:
    data = ssh('cat ' + shlex.quote(qwen_remote + '/results/' + name))
    path = qwen / 'results' / name; path.parent.mkdir(exist_ok=True); path.write_bytes(data); rows.append(json.loads(data))
assert len(rows) == 13
for row in rows:
    assert row['parameter_sha256'] == rows[0]['parameter_sha256']
    assert row['generation_config'] == rows[0]['generation_config']
    peers = [x for x in rows if x['seed_before_generation'] == row['seed_before_generation']]
    assert all(x['generated_token_ids'] == row['generated_token_ids'] for x in peers)
write(qwen / 'verified-summary.json', dict(status='PARAMETER_BUFFER_CONFIG_AND_PAIRED_OUTPUT_VERIFIED', measured_runs=12, warmup_runs=1))
print('QWEN_PASSED', flush=True)
lcommand = [x.replace('.llama31-generation-parity-20261104', '.llama31-buffer-parity-final-20261104').replace('llama31-generation-config-parity-20261104', 'llama31-buffer-parity-final-20261104').replace('/transformers_compat.py', '/transformers_buffers_compat.py') for x in original]
run(llama, lcommand, 3780, booking(3780))
retrieve(llama, llama_remote, 'random', 30)
retrieve(llama, llama_remote, 'cached', 5)
write(llama / 'verified-summary.json', dict(status='BUFFER_CONFIG_AND_PAIRED_OUTPUT_VERIFIED', random_pairs=30, cached_pairs=5, full_parameter_hash_equal_in_failure_seed_diagnostic=True))
assert int(ssh('find ' + shlex.quote(llama_remote + '/storage') + ' -mindepth 1 | wc -l')) == 0
print('ALL_PARITY_CHECKS_PASSED', flush=True)
