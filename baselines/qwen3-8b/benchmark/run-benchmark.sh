set -euo pipefail
export PYTHONPATH=/task/sdk
export LD_LIBRARY_PATH=/task/sdk/sllm_store:/usr/local/lib/python3.12/dist-packages/torch/lib:/usr/local/cuda/lib64
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_ENABLE_PARALLEL_LOADING=false
python3 /task/benchmark.py convert
python3 /task/benchmark.py server > /task/store.log 2>&1 &
store_pid=$!
trap 'kill "$store_pid" 2>/dev/null || true; wait "$store_pid" 2>/dev/null || true' EXIT
python3 - <<'PY'
import grpc
channel=grpc.insecure_channel('127.0.0.1:8073');grpc.channel_ready_future(channel).result(timeout=180);channel.close()
PY
for cache in cold warm; do
 for trial in 1 2 3; do
  python3 /task/benchmark.py trial --backend safetensors --case "$cache" --trial "$trial"
 done
done
python3 /task/benchmark.py trial --backend serverless --case warmup --trial 0
for cache in cold warm; do
 for trial in 1 2 3; do
  python3 /task/benchmark.py trial --backend serverless --case "$cache" --trial "$trial"
 done
done
python3 - <<'PY'
import pathlib,json,statistics
r=pathlib.Path('/task/results');files=sorted(r.glob('*.json'));rows=[json.loads(p.read_text()) for p in files]
reference=next(x for x in rows if x['backend']=='safetensors')
for row in rows:
 assert row['parameter_sha256']==reference['parameter_sha256'],'loaded parameter bytes differ'
 assert row['next_token']==reference['next_token'],'forward smoke differs'
summary={'scope':'official source 9f50241 standalone Transformers loader on shared runtime210-cu129; not published cluster image benchmark','source_commit':'9f50241baa5386e06a9321c51f19a9ef5f964c2b','results':{},'parameter_bytes_verified_equal':True,'single_token_forward_agrees':True,'trials':rows}
for cache in ('cold','warm'):
 summary['results'][cache]={}
 for backend in ('safetensors','serverless'):
  values=[x['load_seconds'] for x in rows if x['cache_case']==cache and x['backend']==backend]
  assert len(values)==3
  summary['results'][cache][backend]={'median_seconds':statistics.median(values),'min_seconds':min(values),'max_seconds':max(values),'n':len(values)}
(pathlib.Path('/task')/'benchmark-summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary['results']),flush=True)
PY
