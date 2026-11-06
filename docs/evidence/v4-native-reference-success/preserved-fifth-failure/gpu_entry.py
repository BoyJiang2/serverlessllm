from pathlib import Path
import importlib.metadata as md,json,os,runpy,sys,tomllib
import torch
from sllm_store.v4_state import write_once
assert torch.cuda.is_available() and torch.cuda.device_count()==8
devices=[dict(index=i,name=torch.cuda.get_device_name(i),capability=torch.cuda.get_device_capability(i),total_memory=torch.cuda.get_device_properties(i).total_memory) for i in range(8)]
assert all('H20' in r['name'] and r['capability']==(9,0) for r in devices)
from vllm.platforms.cuda import CudaPlatform
mapped_uuids=[CudaPlatform.get_device_uuid(i) for i in range(8)]
assert mapped_uuids==os.environ['NVIDIA_VISIBLE_DEVICES'].split(',')
maps=Path('/proc/self/maps').read_text()
cuda_maps=[line for line in maps.splitlines() if 'libcuda.so' in line]
assert cuda_maps and not any('/compat/' in line or '/stubs/' in line for line in cuda_maps)
lock=tomllib.loads(Path('/workspace/docs/evidence/v4-runtime-contract-cpu/final/pylock.v4-final.toml').read_text())
normalize=lambda n:n.lower().replace('_','-').replace('.','-')
installed={normalize(d.metadata['Name']):d.version for d in md.distributions()}
assert installed=={p['name']:p['version'] for p in lock['packages']}
write_once('/output/gpu-precheck.json',dict(devices=devices,libcuda_maps=cuda_maps,installed_packages=installed,cuda_initialized=torch.cuda.is_initialized(),cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'],vllm_mapped_gpu_uuids=mapped_uuids))
print('GPU_PRECHECK_OK',flush=True)
sys.argv=['v4_native_reference.py','--execute','--guard','/control/guard/armed.json','--output','/output/native']
runpy.run_path('/workspace/benchmarks/v4_native_reference.py',run_name='__main__')
