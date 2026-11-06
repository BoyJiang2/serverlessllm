import inspect,json,os,hashlib,glob
from pathlib import Path
import torch
from vllm.platforms.cuda import CudaPlatform
assert not glob.glob('/dev/nvidia*') and not torch.cuda.is_initialized()
data=json.loads(Path('/control/inputs.json').read_bytes())
os.environ['CUDA_VISIBLE_DEVICES']=','.join(data['uuids'])
try:
 CudaPlatform.device_id_to_physical_device_id(0)
except ValueError as error:negative=repr(error)
else:raise RuntimeError('Expected upstream UUID parsing failure was not reproduced')
os.environ['CUDA_VISIBLE_DEVICES']=','.join(map(str,data['indices']))
mapped=[CudaPlatform.device_id_to_physical_device_id(i) for i in range(8)]
assert mapped==data['indices'] and not torch.cuda.is_initialized()
source=Path(inspect.getsourcefile(CudaPlatform.device_id_to_physical_device_id))
result=dict(uuid_failure_reproduced=negative,integer_mapping=mapped,cuda_visible_devices=os.environ['CUDA_VISIBLE_DEVICES'],
 source_path=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),cuda_initialized=False,model_constructed=False,
 scope='Actual installed fixed-version CUDA platform mapping function; no GPU/NVML capability calls or model inspector execution')
Path('/output/result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
