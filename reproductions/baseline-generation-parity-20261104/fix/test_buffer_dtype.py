import ast,gc,json,pathlib,torch
from transformers import AutoConfig,AutoModelForCausalLM
from accelerate import init_empty_weights
source=ast.parse(pathlib.Path('/parity/transformers_buffers_compat.py').read_text())
for name,dtype in [('Llama-3.1-8B-Instruct',torch.float16),('Qwen3-8B',torch.bfloat16)]:
 path='/models/'+name
 baseline=AutoModelForCausalLM.from_pretrained(path,torch_dtype=dtype,device_map={'':'cpu'},local_files_only=True)
 reference={n:t.clone() for n,t in baseline.named_buffers()}
 del baseline;gc.collect()
 config=AutoConfig.from_pretrained(path,local_files_only=True);config.torch_dtype=dtype
 for fname in ['best_effort_load','fully_parallel_load']:
  fn=next(x for x in source.body if isinstance(x,ast.FunctionDef) and x.name==fname)
  construction=next(x.value for x in ast.walk(fn) if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='model' for t in x.targets))
  with init_empty_weights():model=eval(compile(ast.Expression(construction),'<actual-loader-construction>','eval'),{'_class':AutoModelForCausalLM,'config':config})
  assert all(t.is_meta and t.dtype==dtype for t in model.parameters())
  actual=dict(model.named_buffers());assert actual.keys()==reference.keys()
  for n,t in actual.items():assert t.dtype==reference[n].dtype and torch.equal(t,reference[n]),(name,fname,n)
  print(json.dumps({'model':name,'loader':fname,'parameter_dtype':str(dtype),'buffer_names':list(actual),'buffers_equal_to_safetensors':True}),flush=True)
  del model;gc.collect()
assert not torch.cuda.is_initialized()
