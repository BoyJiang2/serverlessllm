"""Native tokenizer CPU smoke; checkpoint exposes only five small assets."""
import hashlib,json,pathlib
import torch
assert not list(pathlib.Path('/dev').glob('nvidia*')) and not torch.cuda.is_initialized()
root=pathlib.Path('/checkpoint')
names={'config.json','generation_config.json','tokenizer_config.json','tiktoken.model','chat_template.jinja'}
assert {p.name for p in root.iterdir()}==names
sizes={name:(root/name).stat().st_size for name in names}
assert sum(sizes.values()) < 3<<20
assert sizes['tiktoken.model']==2795286
hashes={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sorted(names)}
assert hashes['config.json']=='85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899'
assert hashes['tokenizer_config.json']=='12fcab43d2b6068f46769f5ff373960bf7c17a94d7abbc50e2491306b2f6cf58'
from vllm.transformers_utils.config import get_config
get_config('/checkpoint',trust_remote_code=False,config_format='hf',local_files_only=True)
from vllm.tokenizers import get_tokenizer
tokenizer=get_tokenizer('/checkpoint',trust_remote_code=False,tokenizer_mode='hf',local_files_only=True)
text='Kimi-K2.6 张量校验 0123456789\n'
ids=tokenizer.encode(text,add_special_tokens=False)
decoded=tokenizer.decode(ids,skip_special_tokens=False,clean_up_tokenization_spaces=False)
assert decoded==text
vocab_size=json.loads((root/'config.json').read_text())['text_config']['vocab_size']
assert ids and all(0<=i<vocab_size for i in ids)
assert not torch.cuda.is_initialized()
print(json.dumps({'status':'NATIVE_TOKENIZER_CPU_SMOKE_PASS','scope':'native tokenizer construction and one roundtrip only; not remote tokenizer parity, model inference or GPU','tokenizer_class':type(tokenizer).__module__+'.'+type(tokenizer).__qualname__,'len_tokenizer':len(tokenizer),'vocab_size':tokenizer.vocab_size,'model_vocab_size':vocab_size,'special_ids':{name:getattr(tokenizer,name,None) for name in ('bos_token_id','eos_token_id','pad_token_id')},'sample_text':text,'sample_ids':ids,'sample_decoded':decoded,'asset_sha256':hashes,'asset_bytes':sizes,'torch_cuda_initialized':False,'trust_remote_code':False,'local_files_only':True},ensure_ascii=False),flush=True)
