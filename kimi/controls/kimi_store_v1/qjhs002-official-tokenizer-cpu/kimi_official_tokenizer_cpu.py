"""Use the existing tokenizer_cls API with two explicitly reviewed local files."""
import argparse,hashlib,inspect,json,pathlib,pickle,importlib.metadata
parser=argparse.ArgumentParser()
parser.add_argument('--allow-reviewed-local-code',action='store_true')
args=parser.parse_args()
if not args.allow_reviewed_local_code:raise PermissionError('reviewed local code authorization is required')
root=pathlib.Path('/checkpoint')
names={'config.json','generation_config.json','tokenizer_config.json','tiktoken.model','chat_template.jinja','tokenization_kimi.py','tool_declaration_ts.py'}
assert {p.name for p in root.iterdir()}==names
assert sum((root/name).stat().st_size for name in names)<3<<20
digests={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(names)}
assert digests['tokenization_kimi.py']=='2ab1ffb6f5c4380758bd8d9752ff1041c09024182676a4311528fbdf92fb9599'
assert digests['tool_declaration_ts.py']=='97e09ed262f1cd38f87584eaa46c5d4d5b3c9725dd299b21642f389040794ebc'
gold_path=pathlib.Path('/reference/core.json')
assert hashlib.sha256(gold_path.read_bytes()).hexdigest()=='0870a824e31d178bdeb9a04f93fee2289ba2bd8815b49dacb252189935b7d664'
gold=json.loads(gold_path.read_bytes())
for name,digest in gold['asset_sha256'].items():assert digests[name]==digest,name
model_config=json.loads((root/'config.json').read_bytes())
tokenizer_config=json.loads((root/'tokenizer_config.json').read_bytes())
assert model_config['model_type']=='kimi_k25' and tokenizer_config['tokenizer_class']=='TikTokenTokenizer'
assert tokenizer_config['auto_map']['AutoTokenizer']==['tokenization_kimi.TikTokenTokenizer',None]
import torch
assert not list(pathlib.Path('/dev').glob('nvidia*')) and not torch.cuda.is_initialized()
from vllm.transformers_utils.config import get_config
get_config('/checkpoint',trust_remote_code=False,config_format='hf',local_files_only=True)
print(json.dumps({'phase':'reviewed_local_code_gate_passed','source_sha256':digests}),flush=True)
from transformers.dynamic_module_utils import get_class_from_dynamic_module
Official=get_class_from_dynamic_module('tokenization_kimi.TikTokenTokenizer','/checkpoint',local_files_only=True)
assert hashlib.sha256(pathlib.Path(inspect.getfile(Official)).read_bytes()).hexdigest()==digests['tokenization_kimi.py']
from vllm.tokenizers import get_tokenizer
tokenizer=get_tokenizer('/checkpoint',tokenizer_cls=Official,tokenizer_mode='hf',trust_remote_code=True,local_files_only=True)
assert isinstance(tokenizer,Official)
print(json.dumps({'phase':'official_class_constructed','class':type(tokenizer).__module__+'.'+type(tokenizer).__qualname__,'len':len(tokenizer),'vocab_size':tokenizer.vocab_size,'special_ids':{k:getattr(tokenizer,k,None) for k in ('bos_token_id','eos_token_id','pad_token_id','unk_token_id')}}),flush=True)
expected_named={row['token']:row['expected_id'] for row in gold['named_ids']}
named_by_id={index:token for token,index in expected_named.items()}
special={named_by_id.get(index,f'<|reserved_token_{index}|>'):index for index in range(gold['base_vocab'],gold['reference_vocab'])}
def verify(current):
    assert isinstance(current,Official)
    assert len(current)==gold['reference_vocab'] and current.vocab_size==gold['reference_vocab']
    for token,index in expected_named.items():assert current.convert_tokens_to_ids(token)==index,(token,index)
    vocab=current.get_vocab()
    for token,index in special.items():assert vocab[token]==index,(token,index)
    for case in gold['cases']:
        actual=current.encode(case['text'],add_special_tokens=False)
        assert actual==case['reference_ids'],case['text']
        assert current.decode(actual,skip_special_tokens=False,clean_up_tokenization_spaces=False)==case['text']
verify(tokenizer)
print(json.dumps({'phase':'official_class_core_pass','named_ids_checked':len(expected_named),'reserved_slots_checked':len(special),'exact_id_cases':len(gold['cases'])}),flush=True)
from vllm.tokenizers.hf import get_cached_tokenizer
cached=get_cached_tokenizer(tokenizer)
verify(cached)
serialized=pickle.dumps(cached)
restored=pickle.loads(serialized)
verify(restored)
assert not torch.cuda.is_initialized()
print(json.dumps({'status':'OFFICIAL_TOKENIZER_CPU_CORE_CACHE_PICKLE_PASS','scope':'official checkpoint tokenizer via existing vLLM tokenizer_cls; core IDs, kwargs encode/decode, cached wrapper and pickle only; no chat/tools/model/Store/GPU','versions':{name:importlib.metadata.version(name) for name in ('transformers','vllm','tiktoken','tokenizers')},'class':Official.__module__+'.'+Official.__qualname__,'class_cached_file':inspect.getfile(Official),'vocab_size':tokenizer.vocab_size,'len_tokenizer':len(tokenizer),'named_ids_checked':len(expected_named),'reserved_slots_checked':len(special),'exact_id_cases':len(gold['cases']),'pickle_bytes':len(serialized),'asset_sha256':digests,'gold_sha256':'0870a824e31d178bdeb9a04f93fee2289ba2bd8815b49dacb252189935b7d664','explicit_reviewed_code_flag':args.allow_reviewed_local_code,'tokenizer_trust_remote_code':True,'cuda_initialized':False}),flush=True)
