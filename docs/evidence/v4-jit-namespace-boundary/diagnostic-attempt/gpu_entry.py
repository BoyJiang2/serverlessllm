from pathlib import Path
import importlib.metadata as md,json,os,runpy,sys,tomllib
import torch
from sllm_store.v4_state import write_once
assert torch.cuda.is_available() and torch.cuda.device_count()==8
devices=[dict(index=i,name=torch.cuda.get_device_name(i),capability=torch.cuda.get_device_capability(i),total_memory=torch.cuda.get_device_properties(i).total_memory) for i in range(8)]
assert all('H20' in r['name'] and r['capability']==(9,0) for r in devices)
from vllm.platforms.cuda import CudaPlatform
raw_uuids=[CudaPlatform.get_device_uuid(i) for i in range(8)]
mapped_uuids=[value.decode('ascii') if isinstance(value,bytes) else value for value in raw_uuids]
expected_uuids=[row[1] for row in json.loads(Path('/control/preparation.json').read_bytes())['gpu_initial']['rows']]
write_once('/output/device-map-precheck.json',dict(raw_uuid_repr=[repr(value) for value in raw_uuids],mapped_uuids=mapped_uuids,expected_uuids=expected_uuids,nvidia_visible_devices_observed=os.environ.get('NVIDIA_VISIBLE_DEVICES')))
assert mapped_uuids==expected_uuids
maps=Path('/proc/self/maps').read_text()
cuda_maps=[line for line in maps.splitlines() if 'libcuda.so' in line]
assert cuda_maps and not any('/compat/' in line or '/stubs/' in line for line in cuda_maps)
lock=tomllib.loads(Path('/workspace/docs/evidence/v4-runtime-contract-cpu/final/pylock.v4-final.toml').read_text())
normalize=lambda n:n.lower().replace('_','-').replace('.','-')
installed={normalize(d.metadata['Name']):d.version for d in md.distributions()}
assert installed=={p['name']:p['version'] for p in lock['packages']}
write_once('/output/gpu-precheck.json',dict(devices=devices,libcuda_maps=cuda_maps,installed_packages=installed,cuda_initialized=torch.cuda.is_initialized(),cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'],vllm_mapped_gpu_uuids=mapped_uuids))
print('GPU_PRECHECK_OK',flush=True)
import hashlib
expected=json.loads(Path('/control/preparation.json').read_bytes())['source_overrides']
for name,digest in expected.items():
 assert hashlib.sha256((Path('/opt/sllm-v4/lib/python3.12/site-packages/sllm_store')/name).read_bytes()).hexdigest()==digest
sys.argv=['v4_store_recovery.py', '--execute', '--model', '/mnt/raid/models/DeepSeek-V4-Pro', '--store', '/data/brick1/boyjiang/sllm-v4-pro-store/store-conversion-20261107T020633Z', '--identity', '/control/conversion-identity.json', '--guard', '/control/guard/armed.json', '--output', '/output/recovery', '--pool-config', '/control/pool-config.json', '--diagnostic-pool']
runpy.run_path('/workspace/benchmarks/v4_store_recovery.py',run_name='__main__')
