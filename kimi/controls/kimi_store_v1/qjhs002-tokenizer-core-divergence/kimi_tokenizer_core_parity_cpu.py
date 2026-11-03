"""Compare native defaults to official literal BPE definition, without importing checkpoint code."""
import ast,hashlib,json,pathlib,importlib.metadata
import torch,tiktoken
from tiktoken.load import load_tiktoken_bpe
assert not list(pathlib.Path('/dev').glob('nvidia*')) and not torch.cuda.is_initialized()
root=pathlib.Path('/checkpoint')
names={'config.json','generation_config.json','tokenizer_config.json','tiktoken.model','chat_template.jinja','tokenization_kimi.py'}
assert {p.name for p in root.iterdir()}==names
assert sum((root/name).stat().st_size for name in names)<3<<20
source=(root/'tokenization_kimi.py').read_bytes()
assert hashlib.sha256(source).hexdigest()=='2ab1ffb6f5c4380758bd8d9752ff1041c09024182676a4311528fbdf92fb9599'
tree=ast.parse(source)
classes=[n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='TikTokenTokenizer']
assert len(classes)==1
assignments={target.id:n.value for n in classes[0].body if isinstance(n,ast.Assign) for target in n.targets if isinstance(target,ast.Name)}
pattern_node=assignments['pat_str']
assert isinstance(pattern_node,ast.Call) and isinstance(pattern_node.func,ast.Attribute)
assert pattern_node.func.attr=='join' and ast.literal_eval(pattern_node.func.value)=='|'
parts=ast.literal_eval(pattern_node.args[0])
assert all(isinstance(part,str) for part in parts)
pattern='|'.join(parts)
reserved=ast.literal_eval(assignments['num_reserved_special_tokens'])
assert reserved==256
configuration=json.loads((root/'tokenizer_config.json').read_bytes())
declared={int(index):row['content'] for index,row in configuration['added_tokens_decoder'].items()}
ranks=load_tiktoken_bpe(str(root/'tiktoken.model'))
special={declared.get(index,f'<|reserved_token_{index}|>'):index for index in range(len(ranks),len(ranks)+reserved)}
reference=tiktoken.Encoding(name='official-kimi-core-cpu-reference',pat_str=pattern,mergeable_ranks=ranks,special_tokens=special)
from vllm.transformers_utils.config import get_config
get_config('/checkpoint',trust_remote_code=False,config_format='hf',local_files_only=True)
from vllm.tokenizers import get_tokenizer
native=get_tokenizer('/checkpoint',trust_remote_code=False,tokenizer_mode='hf',local_files_only=True)
added=native.get_added_vocab()
named=[{'token':token,'expected_id':index,'native_id':added.get(token)} for index,token in sorted(declared.items())]
id_mismatches=[row for row in named if row['expected_id']!=row['native_id']]
missing_reserved=[{'token':token,'expected_id':index} for token,index in special.items() if token not in added]
texts=['Kimi-K2.6 张量校验 0123456789\n','ABC中文def','中文English中文','HelloWorld XMLHTTPRequest','helloWORLD HTTPServer camelCase','I\'m WE\'RE Don\'t CAN\'T','áÉ e\u0301 Σσ中文ABC','1234567 0123456789',' \t\r\n中文\n\n!?','测试ServerlessLLM适配KimiK2.6','你好world123中文','aB ABc aBCd ABCdE']
cases=[]
for text in texts:
    expected=reference.encode(text,disallowed_special=())
    actual=native.encode(text,add_special_tokens=False)
    cases.append({'text':text,'reference_ids':expected,'native_ids':actual,'ids_equal':expected==actual,'reference_roundtrip':reference.decode(expected)==text,'native_roundtrip':native.decode(actual,skip_special_tokens=False,clean_up_tokenization_spaces=False)==text})
state=native.backend_tokenizer.pre_tokenizer.__getstate__()
if isinstance(state,bytes):state=state.decode()
pretokenizer=json.loads(state)
assert not torch.cuda.is_initialized()
divergent=bool(id_mismatches or missing_reserved or any(not c['ids_equal'] for c in cases))
print(json.dumps({'status':'TOKENIZER_CORE_DIVERGENCE_CONFIRMED' if divergent else 'TOKENIZER_CORE_CASES_MATCH','scope':'official literal pattern/reserved mapping through installed tiktoken only; no checkpoint Python import, model, chat-template parity or GPU','versions':{name:importlib.metadata.version(name) for name in ('transformers','tiktoken','tokenizers','vllm')},'source_sha256':hashlib.sha256(source).hexdigest(),'asset_sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(names)},'reference_pattern':pattern,'native_pretokenizer':pretokenizer,'base_vocab':len(ranks),'reference_vocab':reference.n_vocab,'native_len':len(native),'named_ids':named,'named_id_mismatches':id_mismatches,'missing_reserved_count':len(missing_reserved),'missing_reserved_first':missing_reserved[:5],'native_added_vocab':added,'cases':cases,'case_id_mismatch_count':sum(not c['ids_equal'] for c in cases),'cuda_initialized':False},ensure_ascii=False),flush=True)
