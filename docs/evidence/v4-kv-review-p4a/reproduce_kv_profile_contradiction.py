from pathlib import Path
import ast,json,types,typing,hashlib,datetime,sys,copy
root=Path('/home/boyjiang/sllm-v4-pro/code');engine=Path('/home/boyjiang/sllm-v4-pro/engine-src/vllm-132765e35606/vllm');base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');sys.path.insert(0,str(root/'sllm_store'))
from sllm_store.v4_service import freeze_registration_config
config=json.loads(Path('/mnt/raid/models/DeepSeek-V4-Pro/config.json').read_bytes());utility=engine/'utils/torch_utils.py';tree=ast.parse(utility.read_text());names={'get_kv_cache_quant_algo_string','resolve_kv_cache_dtype_string'};functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names];ns=dict(Any=typing.Any,ModelConfig=object)
exec(compile(ast.Module(body=functions,type_ignores=[]),str(utility),'exec'),ns)
resolved=ns['resolve_kv_cache_dtype_string']('auto',types.SimpleNamespace(hf_config=types.SimpleNamespace(quantization_config=config['quantization_config'])));assert resolved=='auto'
attention=engine/'model_executor/layers/deepseek_v4_attention.py';tree=ast.parse(attention.read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='DeepseekV4MLAAttention');fn=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='__init__');assign=next(n for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='kv_cache_dtype' for t in n.targets));guard=next(n for n in fn.body if isinstance(n,ast.Assert) and 'DeepseekV4 only supports fp8' in ast.unparse(n));cases=[]
for value in [resolved,'fp8']:
 try:exec(compile(ast.Module(body=[assign,guard],type_ignores=[]),str(attention),'exec'),dict(cache_config=types.SimpleNamespace(cache_dtype=value)));passed=True;error=None
 except AssertionError as e:passed=False;error=str(e)
 cases.append(dict(cache_dtype=value,assertion_passed=passed,error=error))
assert cases[0]['assertion_passed'] is False and cases[1]['assertion_passed'] is True
backend=dict(torch_dtype='bfloat16',enforce_eager=True,enable_prefix_caching=False,max_model_len=2048,max_num_seqs=1,max_num_batched_tokens=2048,pretrained_model_name_or_path='/mnt/raid/models/DeepSeek-V4-Pro',load_format='safetensors',tensor_parallel_size=8,tokenizer_mode='deepseek_v4')
service=dict(model='v4-pro',backend='vllm',num_gpus=8,auto_scaling_config=dict(min_instances=0,max_instances=1),backend_config=backend);freeze_registration_config(service)
requested=copy.deepcopy(service);requested['backend_config']['kv_cache_dtype']='fp8'
try:freeze_registration_config(requested);raise AssertionError('Expected current closed profile to reject missing field')
except ValueError as e:service_error=str(e)
convert=root/'sllm_store/sllm_store/v4_convert.py';ctree=ast.parse(convert.read_text());cfn=next(n for n in ctree.body if isinstance(n,ast.FunctionDef) and n.name=='export_engine_config');ret=next(n for n in cfn.body if isinstance(n,ast.Return));keys=[k.arg for k in ret.value.keywords];assert 'kv_cache_dtype' not in keys
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),resolved_auto=resolved,actual_upstream_assertion_cases=cases,old_service_profile_accepted=True,explicit_fp8_rejected_by_current_profile=service_error,converter_kwarg_names=keys,source_fragments=dict(assignment=ast.get_source_segment(attention.read_text(),assign),assertion=ast.get_source_segment(attention.read_text(),guard)),source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [utility,attention,root/'sllm_store/sllm_store/v4_service.py',convert]},proposed_inputs=dict(kv_cache_dtype='fp8',attention_config=dict(use_fp4_indexer_cache=False)),scope='Actual pinned source CPU AST guard and current pure service validator; no vLLM/model construction, GPU, patch, protocol change or runtime proof')
(base/'kv-profile-contradiction.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ('source_sha256','converter_kwarg_names','source_fragments')}))
