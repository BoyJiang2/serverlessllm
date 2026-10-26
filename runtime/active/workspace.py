"""One source for canonical workspace, source and per-run output locations."""
import json,os,pathlib,re,sys

if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')

HOST=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')
SOURCE=pathlib.Path(__file__).resolve().parent

def workspace():
    value=os.environ.get('SLLM_CONTROL_WORKSPACE',str(HOST))
    if value not in (str(HOST),'/experiment'):
        raise RuntimeError('unexpected canonical workspace')
    return pathlib.Path(value)

def manifest():
    value=json.loads((SOURCE/'run-manifest.json').read_text())
    if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',value['run_id']):
        raise RuntimeError('invalid control run identity')
    return value

def run_root():
    return workspace()/'validation/controls'/manifest()['run_id']

def archive():
    return workspace()/'archive/original-run'
