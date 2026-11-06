from pathlib import Path
import os,json,sys,datetime,glob,hashlib
os.umask(0o002);base=Path('/logs/runtime-contract-20261106');base.mkdir(exist_ok=True);label=sys.argv[1]
import torch
from sllm.backends.vllm_backend import VllmBackend
from sllm_store.v4_service import freeze_registration_config
from sllm_store.v4_state import REVISION
model='/mnt/raid/models/DeepSeek-V4-Pro';assert Path(model,'CONFIG_ONLY_NOT_RUNTIME_WEIGHTS').is_file() and not list(Path(model).glob('*.safetensors')) and not glob.glob('/dev/nvidia*')
config=dict(model='v4-config-probe',backend='vllm',num_gpus=8,auto_scaling_config=dict(min_instances=0,max_instances=1),backend_config=dict(pretrained_model_name_or_path=model,torch_dtype='bfloat16',load_format='safetensors',tokenizer_mode='deepseek_v4',tensor_parallel_size=8,enforce_eager=True,enable_prefix_caching=False,max_model_len=2048,max_num_seqs=1,max_num_batched_tokens=2048,revision=REVISION,tokenizer_revision=REVISION,kv_cache_dtype='fp8',seed=20261105,distributed_executor_backend='mp',attention_config=dict(use_fp4_indexer_cache=False)))
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),label=label,registration_input=config,config_only_files=sorted(p.name for p in Path(model).iterdir()),config_sha256=hashlib.sha256(Path(model,'config.json').read_bytes()).hexdigest())
try:report['registration_accepted']=freeze_registration_config(config)==config
except Exception as e:report.update(registration_accepted=False,registration_error=str(e))
try:
 backend=VllmBackend('v4-config-probe',config['backend_config']);args=backend.engine_args
 report['actual_engine_args']={k:getattr(args,k) for k in ('model','revision','tokenizer_revision','dtype','quantization','kv_cache_dtype','seed','gpu_memory_utilization','distributed_executor_backend')}
 m=args.create_model_config();report['actual_model_config']={k:getattr(m,k) for k in ('model','revision','tokenizer_revision','dtype','quantization','max_model_len')};report['actual_model_config']['dtype']=str(report['actual_model_config']['dtype']);report['actual_model_config']['hf_quantization_config']=m.hf_config.quantization_config
 report['actual_model_config']['seed']=m.seed
 attn=args.attention_config
 report['effective_fp4_indexer_cache']=attn.get('use_fp4_indexer_cache') if isinstance(attn,dict) else attn.use_fp4_indexer_cache
 from sllm_store.v4_convert import export_engine_config
 from sllm_store.v4_state import FORMAT,ENGINE_COMMIT,KERNEL_PACKAGES
 from vllm import AsyncEngineArgs
 os.environ['STORAGE_PATH']='/data/brick1/boyjiang/sllm-v4-pro-build'
 identity=dict(format=FORMAT,state='before_quantization_postprocessing',source_revision=REVISION,engine_commit=ENGINE_COMMIT,vllm_version='0.20.1',tp=8,pp=1,dp=1,ep=False,activation_dtype='torch.bfloat16',conversion_id='config-probe-only',torch_version=torch.__version__,kernel_packages=KERNEL_PACKAGES)
 conversion=export_engine_config(model,os.environ['STORAGE_PATH']+'/config-only-no-store',identity)
 ca=AsyncEngineArgs(**conversion);cm=ca.create_model_config()
 report['converter_engine_args']={k:getattr(ca,k) for k in ('revision','tokenizer_revision','quantization','kv_cache_dtype','seed','gpu_memory_utilization','distributed_executor_backend')}
 report['converter_model_config']={k:str(getattr(cm,k)) for k in ('revision','tokenizer_revision','dtype','quantization','max_model_len','seed')}
 assert m.revision==cm.revision==REVISION and m.tokenizer_revision==cm.tokenizer_revision==REVISION
 assert m.quantization==cm.quantization=='deepseek_v4_fp8' and args.kv_cache_dtype==ca.kv_cache_dtype=='fp8'
except Exception as e:report.update(config_error_type=type(e).__name__,config_error=str(e))
assert label=='before' or report.get('registration_accepted') and 'config_error' not in report,report
import importlib.metadata as md
report['packages']={n:md.version(n) for n in ('vllm','torch','serverless-llm','serverless-llm-store')}
from vllm.config import ParallelConfig, CacheConfig
parallel=ParallelConfig(tensor_parallel_size=args.tensor_parallel_size,pipeline_parallel_size=args.pipeline_parallel_size,data_parallel_size=args.data_parallel_size,enable_expert_parallel=args.enable_expert_parallel,distributed_executor_backend=args.distributed_executor_backend)
cache=CacheConfig(cache_dtype=args.kv_cache_dtype,enable_prefix_caching=args.enable_prefix_caching)
report['actual_parallel_config']={k:getattr(parallel,k) for k in ('tensor_parallel_size','pipeline_parallel_size','data_parallel_size','enable_expert_parallel','distributed_executor_backend')}
report['actual_cache_config']=dict(cache_dtype=cache.cache_dtype,enable_prefix_caching=cache.enable_prefix_caching)
assert m.seed==cm.seed==20261105 and parallel.distributed_executor_backend=='mp' and report['effective_fp4_indexer_cache'] is False
report.update(cuda_initialized=torch.cuda.is_initialized(),model_or_engine_constructed=False,scope='Actual installed backend/AsyncEngineArgs/ModelConfig with frozen small config files only; no weights, NN model, engine or GPU execution')
(base/('effective-config-'+label+'.json')).write_text(json.dumps(report,indent=2,default=str)+'\n');print(json.dumps(report,default=str))
