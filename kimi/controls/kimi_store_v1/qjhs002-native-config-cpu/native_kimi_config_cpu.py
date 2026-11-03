"""Instantiate native Kimi config only; no model or weight files are mounted."""
import hashlib,importlib,inspect,json,pathlib
import torch
assert not list(pathlib.Path('/dev').glob('nvidia*'))
assert not torch.cuda.is_initialized()
path=pathlib.Path('/checkpoint/config.json')
data=path.read_bytes()
assert hashlib.sha256(data).hexdigest()=='85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899'
assert sorted(p.name for p in path.parent.iterdir())==['config.json']
from vllm.transformers_utils.config import get_config,get_hf_text_config
config=get_config('/checkpoint',trust_remote_code=False,config_format='hf',local_files_only=True)
text=get_hf_text_config(config)
expected=json.loads(data)
assert config.model_type=='kimi_k25'
assert type(config).__module__.startswith('vllm.transformers_utils.configs.')
fields=('hidden_size','moe_intermediate_size','n_routed_experts','num_hidden_layers','num_attention_heads','vocab_size')
for name in fields:assert getattr(text,name)==expected['text_config'][name],name
assert not torch.cuda.is_initialized()
module=importlib.import_module(type(config).__module__)
module_path=pathlib.Path(inspect.getfile(module))
print(json.dumps({'status':'NATIVE_CONFIG_CPU_PASS','scope':'native config construction only; no model, tokenizer, Store, GPU or weight payload','config_sha256':hashlib.sha256(data).hexdigest(),'config_class':type(config).__module__+'.'+type(config).__qualname__,'config_class_path':str(module_path),'config_class_source_sha256':hashlib.sha256(module_path.read_bytes()).hexdigest(),'text_config_class':type(text).__module__+'.'+type(text).__qualname__,'text_dimensions':{name:getattr(text,name) for name in fields},'torch_cuda_initialized':torch.cuda.is_initialized(),'checkpoint_visible_files':['config.json'],'trust_remote_code':False,'config_format':'hf','local_files_only':True}),flush=True)
