"""Read only small historical evidence/source files; never imports model code."""
import hashlib
import json
from pathlib import Path
import subprocess

V = Path('/home/boyjiang/sllm-v4-pro')
G = Path('/data/brick1/boyjiang/glm5.1-fp8')
OUT = V / 'results/report-boundaries-20261109.json'
sources = {}

def raw(path):
    data = path.read_bytes()
    sources[str(path)] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    return data

def read(path):
    return json.loads(raw(path))

def optional(path):
    return read(path) if path.exists() else None

report = dict(sources=sources, v4={}, glm={}, code={})
protocol = read(V/'code/benchmarks/protocols/v4-nfs40.json') if (V/'code/benchmarks/protocols/v4-nfs40.json').exists() else None
rows = read(V/'results/nfs40-final-20261108-v2/qualified-rows.json')
phases = ['weights_pipeline_including_allocation', 'weight_ingestion', 'quantization_postprocess']
names = {p+s for p in phases for s in ['_begin','_end','_failed']}
for row in rows:
    if not row['success']:
        continue
    base = V/'evidence'/row['attempt_id']
    events = []
    for path in sorted((base/'output/events').glob('*.jsonl')):
        for line in raw(path).decode().splitlines():
            event = json.loads(line)
            if event['name'] in names or event.get('rank') is not None and event['name'] == 'backend_engine_ready':
                events.append(event)
    # Rank is attached to inner load/postprocess events, not the outer Worker hook.
    ranks = {}
    for path in sorted((base/'output/events').glob('*.jsonl')):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event.get('rank') is not None:
                key = (event['container_hostname'],event['pid'])
                assert key not in ranks or ranks[key] == event['rank']
                ranks[key] = event['rank']
    for event in events:
        event['resolved_rank'] = ranks.get((event['container_hostname'], event['pid']))
    preparation = {}
    for name in ['preparation.json','candidate.json','launch.json','output/store-daemon-started.json','output/controller-ready.json','output/store-configuration-checked.json','output/trial-cache-complete.json','output/trial-startup.json','output/trial-service-ready.json','output/trial-http-complete.json']:
        preparation[name] = optional(base/name)
    report['v4'][row['attempt_id']] = dict(condition=row['condition'],block=row['block'],events=events,preparation=preparation)

root = G/'results/reanalysis-20261106/v2'
for row in read(root/'historical-supplement.json')['trials']:
    name = row['attempt_id']
    trial = G/'validation/controls/sllm-glm51fp8-20261027T011726Z-28c92313/formal-trials'/name
    report['glm'][name] = dict(phases=read(trial/'phases.json'), result=read(trial/'result.json'))
for path in [G/'code/c23-reference-f176e05/formal_trials.py',G/'code/c23-reference-f176e05/phase_summary.py']:
    report['code'][str(path)] = raw(path).decode()
for name in ['sllm_store/sllm_store/v4_trace.py','sllm_store/sllm_store/vllm_v4_loader.py','benchmarks/v4_service_once.py','benchmarks/v4_native_service.py']:
    data = subprocess.check_output(['git','-C',str(V/'code'),'show','88cc71ec50c8668c26f26636d7d432c3351356fe:'+name])
    report['code']['git:88cc71e:'+name] = data.decode()
    sources['git:88cc71e:'+name] = dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
for name in ['vllm/v1/worker/gpu_worker.py','vllm/v1/worker/gpu_model_runner.py','vllm/model_executor/model_loader/base_loader.py']:
    path = V/'engine-src/vllm-132765e35606'/name
    report['code'][str(path)] = raw(path).decode()
OUT.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(dict(output=str(OUT),bytes=OUT.stat().st_size,sha256=hashlib.sha256(OUT.read_bytes()).hexdigest(),v4_attempts=len(report['v4']),glm_attempts=len(report['glm']))))
