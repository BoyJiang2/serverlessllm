"""Actual installed engine/component imports and CPU tokenizer; no engine/model construction."""
from pathlib import Path
import json,importlib,importlib.metadata as md,hashlib,glob,datetime,os,sys,subprocess
os.umask(0o002)
assert not glob.glob('/dev/nvidia*')
import torch
assert not torch.cuda.is_available()
base=Path('/logs');label=os.environ['P4A_CPU_SMOKE_LABEL'];assert label in ('base','compat');results=[]
for name in ['vllm','vllm._C','vllm._C_stable_libtorch','vllm._moe_C','vllm.cumem_allocator','vllm.model_executor.models.deepseek_v4','sllm_store.vllm_v4_loader','sllm.backends.vllm_backend']:
 try:
  m=importlib.import_module(name);assert m.__file__.startswith('/opt/sllm-v4/');results.append(dict(module=name,path=m.__file__,passed=True))
 except Exception as e:results.append(dict(module=name,passed=False,error_type=type(e).__name__,error=str(e)))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),imports=results,torch=torch.__version__,cxx11_abi=torch._C._GLIBCXX_USE_CXX11_ABI,gpu_nodes=[],scope='Actual installed source-built component imports; no engine/model construction or CUDA operations')
(base/('target-engine-imports-'+label+'.json')).write_text(json.dumps(report,indent=2)+'\n')
assert all(r['passed'] for r in results),report
from sllm_store import vllm_v4_loader as adapter
adapter.check_engine_source();adapter.register()
from vllm.model_executor.model_loader import _LOAD_FORMAT_TO_MODEL_LOADER as loaders
formats=['sllm_v4_export','sllm_v4_store','sllm_v4_grouped_export','sllm_v4_grouped_store']
assert all(name in loaders for name in formats)
assert md.version('vllm')=='0.20.1'
from vllm import _version
assert _version.__commit_id__=='g132765e35'
assert hasattr(torch.ops._moe_C,'topk_softplus_sqrt')
assert not torch.cuda.is_initialized()
from vllm.tokenizers.deepseek_v4 import DeepseekV4Tokenizer
inputs=Path('/opt/p4a-tokenizer/inputs');assets=Path('/opt/p4a-tokenizer/assets')
for row in json.loads((inputs/'asset-sha.json').read_bytes()):assert hashlib.sha256((assets/row['file']).read_bytes()).hexdigest()==row['sha256']
tokenizer=DeepseekV4Tokenizer.from_pretrained(str(assets),local_files_only=True)
prior=json.loads((base/'target-cpu-bpe-ast.json').read_bytes());outputs=[]
for row in prior['results']:
 request=json.loads((inputs/row['file']).read_bytes())
 ids=tokenizer.apply_chat_template(request['messages'],tokenize=True,enable_thinking=False,drop_thinking=True)
 assert ids==row['input_tokens'] and ids.count(0)==1
 prompt=tokenizer.apply_chat_template(request['messages'],tokenize=False,enable_thinking=False,drop_thinking=True)
 assert hashlib.sha256(prompt.encode()).hexdigest()==row['encoded_prompt_sha256']
 assert tokenizer.decode(ids,skip_special_tokens=False,clean_up_tokenization_spaces=False)==prompt
 outputs.append(dict(file=row['file'],token_count=len(ids),input_tokens=ids,canonical_ids_sha256=hashlib.sha256(json.dumps(ids,separators=(',',':')).encode()).hexdigest(),bos_count=ids.count(0),roundtrip_matches=True))
maps=sorted({line.split()[-1] for line in Path('/proc/self/maps').read_text().splitlines() if '.so' in line and '/' in line.split()[-1]})
assert not any('/opt/src/' in name or '/stubs/' in name for name in maps)
final=dict(loaded_shared_objects=maps,loader_library_path=os.environ.get('LD_LIBRARY_PATH'),cpu_smoke_label=label,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),imports=results,loader_formats={n:loaders[n].__qualname__ for n in formats},source_pin_check_passed=True,packages={n:md.version(n) for n in ['vllm','torch','serverless-llm','serverless-llm-store','transformers','tokenizers']},cpu_tokenizer_outputs=outputs,engine_commit_id=_version.__commit_id__,topk_softplus_sqrt_registered=True,cuda_initialized=torch.cuda.is_initialized(),model_constructed=False,native_model_gold=False,gpu_execution=False,scope='Real complete target imports, registry binding and input-token equivalence; not kernel execution, full Store or model inference')
(base/('target-engine-cpu-smoke-'+label+'.json')).write_text(json.dumps(final,indent=2)+'\n');print(json.dumps(final))
