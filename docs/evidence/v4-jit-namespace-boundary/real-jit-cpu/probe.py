import ast,hashlib,json,types
from pathlib import Path
import torch
import triton


def template(x):
    return x


def checker(path):
    node=next(n for n in ast.parse(Path(path).read_text()).body
              if isinstance(n,ast.FunctionDef) and n.name=='assert_no_raw_references')
    ns=dict(torch=torch,types=types)
    exec(compile(ast.Module(body=[node],type_ignores=[]),path,'exec'),ns)
    return ns['assert_no_raw_references']

raw=torch.arange(4,dtype=torch.uint8)
fn=types.FunctionType(template.__code__,{'__name__':'__main__','retained_for_audit':raw},'template')
kernel=triton.jit(fn)
model=types.SimpleNamespace(compressor=types.SimpleNamespace(_fused_kernel=kernel))
old_error=None
try:checker('/control/old-loader.py')(model,{'expert':raw})
except ValueError as error:old_error=str(error)
assert old_error, 'Old checker must reproduce actual JIT global-namespace false positive'
checker('/control/new-loader.py')(model,{'expert':raw})
kernel.cached_scale=raw
try:
    checker('/control/new-loader.py')(model,{'expert':raw})
except ValueError as error:
    assert 'cached_scale' in str(error)
    real_field_rejected=True
else:raise AssertionError('Actual JIT tensor field must remain guarded')
assert not torch.cuda.is_initialized()
result=dict(torch_version=torch.__version__,triton_version=triton.__version__,jit_type=type(kernel).__module__+'.'+type(kernel).__qualname__,old_false_positive_reproduced=True,new_globals_boundary_passed=True,actual_jit_tensor_field_rejected=real_field_rejected,cuda_initialized=False,scope='Actual same-image Triton JITFunction and CPU tensors; no JIT compile, GPU, model or Store runtime qualification')
Path('/output/result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
