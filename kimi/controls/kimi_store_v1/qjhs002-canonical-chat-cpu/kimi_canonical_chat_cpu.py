"""Exercise the canonical tokenizer factory with two reviewed local source files."""
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
from vllm.tokenizers import get_tokenizer
from vllm.tokenizers import hf as canonical_hf
factory_path=pathlib.Path(inspect.getfile(canonical_hf))
assert hashlib.sha256(factory_path.read_bytes()).hexdigest()=='0d620af05b7c8b86e5c7d3c34db180ad058c4564694dea35911e476ad31df480'
tokenizer=get_tokenizer('/checkpoint',tokenizer_mode='hf',trust_remote_code=True,local_files_only=True)
Official=next(cls for cls in type(tokenizer).__mro__ if cls.__name__=='TikTokenTokenizer')
assert hashlib.sha256(pathlib.Path(inspect.getfile(Official)).read_bytes()).hexdigest()==digests['tokenization_kimi.py']
assert type(tokenizer).__name__=='CachedTikTokenTokenizer'
print(json.dumps({'phase':'official_class_constructed','class':type(tokenizer).__module__+'.'+type(tokenizer).__qualname__,'len':len(tokenizer),'vocab_size':tokenizer.vocab_size,'special_ids':{k:getattr(tokenizer,k,None) for k in ('bos_token_id','eos_token_id','pad_token_id','unk_token_id')}}),flush=True)
expected_named={row['token']:row['expected_id'] for row in gold['named_ids']}
named_by_id={index:token for token,index in expected_named.items()}
special={named_by_id.get(index,f'<|reserved_token_{index}|>'):index for index in range(gold['base_vocab'],gold['reference_vocab'])}
import tiktoken
from tiktoken.load import load_tiktoken_bpe
reference=tiktoken.Encoding(name='official-kimi-chat-core-reference',pat_str=gold['reference_pattern'],mergeable_ranks=load_tiktoken_bpe(str(root/'tiktoken.model')),special_tokens=special)
assert reference.n_vocab==gold['reference_vocab']
from vllm.renderers.hf import resolve_chat_template_kwargs
messages=[{'role':'user','content':'你好 Kimi'}]
tool={'type':'function','function':{'name':'echo_text','parameters':{'type':'object','properties':{}}}}
plain='<|im_user|>user<|im_middle|>你好 Kimi<|im_end|><|im_assistant|>assistant<|im_middle|>'
tool_text='# Tools\n\n## functions\nnamespace functions {\ntype echo_text = (_: {}) => any;\n}'
cases=[('default_thinking',{},None,plain+'<think>'),
       ('thinking_false',{'thinking':False},None,plain+'<think></think>'),
       ('tool_declaration',{},[tool],'<|im_system|>tool_declare<|im_middle|>'+tool_text+'<|im_end|>'+plain+'<think>')]
results=[]
for name,flags,tools,expected_text in cases:
    options={'add_generation_prompt':True,'return_dict':False,**flags}
    resolved=resolve_chat_template_kwargs(tokenizer,tokenizer.chat_template,options)
    assert resolved==options,(name,resolved)
    rendered=tokenizer.apply_chat_template(messages,tools=tools,tokenize=False,**resolved)
    assert rendered==expected_text,(name,rendered,expected_text)
    ids=tokenizer.apply_chat_template(messages,tools=tools,tokenize=True,**resolved)
    assert isinstance(ids,list) and all(isinstance(i,int) for i in ids),name
    expected_ids=reference.encode(expected_text,allowed_special='all')
    assert ids==expected_ids,(name,ids,expected_ids)
    assert tokenizer.encode(rendered,add_special_tokens=False)==expected_ids,name
    assert tokenizer.decode(ids,skip_special_tokens=False,clean_up_tokenization_spaces=False)==rendered,name
    results.append({'name':name,'rendered_text':rendered,'ids':ids,'reference_ids_equal':True,'control_tokens_included_once':True,'return_dict':False,'thinking':flags.get('thinking','official_default_true'),'tools_declared':bool(tools),'tool_executed':False})
    print(json.dumps({'phase':'chat_case_pass','name':name,'tokens':len(ids)},ensure_ascii=False),flush=True)
assert not torch.cuda.is_initialized()
assert not list(pathlib.Path('/dev').glob('nvidia*'))
methods=[{'class':cls.__module__+'.'+cls.__qualname__,'signature':str(inspect.signature(cls.__dict__['apply_chat_template']))} for cls in type(tokenizer).__mro__ if 'apply_chat_template' in cls.__dict__]
print(json.dumps({'status':'CANONICAL_KIMI_TOKENIZER_CPU_CHAT_CASES_PASS','scope':'canonical patched factory; official template rendered text and core BPE IDs for three bounded text-only cases; no default AutoProcessor resolution, HTTP serving, multimodal, tool execution, model, weights, Store or GPU','canonical_factory_sha256':hashlib.sha256(factory_path.read_bytes()).hexdigest(),'asset_sha256':digests,'gold_sha256':'0870a824e31d178bdeb9a04f93fee2289ba2bd8815b49dacb252189935b7d664','versions':{name:importlib.metadata.version(name) for name in ('transformers','vllm','tiktoken','tokenizers')},'apply_chat_template_mro':methods,'cases':results,'explicit_reviewed_code_flag':args.allow_reviewed_local_code,'tokenizer_trust_remote_code':True,'cuda_initialized':False},ensure_ascii=False),flush=True)
