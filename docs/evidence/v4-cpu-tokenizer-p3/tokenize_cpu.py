"""CPU BPE using installed fast tokenizer and pinned target V4 wrapper bodies."""
import ast,contextlib,copy,glob,hashlib,importlib.metadata,json,os,runpy,sys,typing
from pathlib import Path
blocked=('torch','vllm','ray','tensorflow','jax')
assert not any(v is not None and n.split('.')[0] in blocked for n,v in sys.modules.items())
for module in blocked:sys.modules[module]=None
assert not glob.glob('/dev/nvidia*')
assert os.environ['USE_TORCH']=='0' and os.environ['HF_HUB_OFFLINE']=='1'
from transformers import PreTrainedTokenizerFast
base=Path('/inputs')
for row in json.loads((base/'input-manifest.json').read_bytes())['files']:
 assert hashlib.sha256((base/row['file']).read_bytes()).hexdigest()==row['sha256']
expected=json.loads((base/'asset-sha.json').read_bytes())
for row in expected:
 assert hashlib.sha256((Path('/assets')/row['file']).read_bytes()).hexdigest()==row['sha256']
encoder=runpy.run_path(str(base/'v4_encoding.py'))
reference=runpy.run_path(str(base/'reference_encoding.py'))
ns=dict(copy=copy,contextlib=contextlib,Any=typing.Any,HfTokenizer=PreTrainedTokenizerFast,
        PreTrainedTokenizerFast=PreTrainedTokenizerFast,TokenizerLike=object,
        encode_messages=encoder['encode_messages'])
for file,names in (('hf_tokenizer.py',{'get_cached_tokenizer'}),
                   ('v4_tokenizer.py',{'get_deepseek_v4_tokenizer','DeepseekV4Tokenizer'})):
 tree=ast.parse((base/file).read_text())
 selected=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names]
 assert {n.name for n in selected}==names
 exec(compile(ast.Module(body=selected,type_ignores=[]),str(base/file),'exec'),ns)
tokenizer=ns['DeepseekV4Tokenizer'].from_pretrained('/assets',local_files_only=True,cache_dir='/tmp/hf')
prereg=json.loads((base/'preregistration.json').read_bytes())
results=[]
for file,prompt_file,sha_key in (('request.json','request-prompt.txt','request_sha256'),
                                 ('workload32.json','workload32-prompt.txt','workload32_sha256')):
 data=(base/file).read_bytes();assert hashlib.sha256(data).hexdigest()==prereg[sha_key]
 request=json.loads(data)
 prompt=reference['encode_messages'](request['messages'],**request['encoding_options'])
 target_prompt=tokenizer.apply_chat_template(request['messages'],tokenize=False,
     enable_thinking=False,drop_thinking=True)
 assert prompt==target_prompt==(base/prompt_file).read_text()
 assert hashlib.sha256(prompt.encode()).hexdigest()==request['encoded_prompt_sha256']
 ids=tokenizer.apply_chat_template(request['messages'],tokenize=True,
     enable_thinking=False,drop_thinking=True)
 assert ids==tokenizer.encode(prompt,add_special_tokens=False)
 assert ids==tokenizer.apply_chat_template(request['messages'],tokenize=True,
     enable_thinking=False,drop_thinking=True)
 assert ids and all(type(t)is int and t>=0 for t in ids) and len(ids)<=2016
 assert ids[0]==0 and ids.count(0)==1
 decoded=tokenizer.decode(ids,skip_special_tokens=False,clean_up_tokenization_spaces=False)
 assert decoded==prompt
 canonical=json.dumps(ids,separators=(',',':')).encode()
 results.append(dict(file=file,request_sha256=prereg[sha_key],encoded_prompt_sha256=request['encoded_prompt_sha256'],
                     token_count=len(ids),input_tokens=ids,token_ids_canonical_json_sha256=hashlib.sha256(canonical).hexdigest(),
                     roundtrip_matches=True,within_2016=True,bos_count=ids.count(0)))
active=[n for n,v in sys.modules.items() if v is not None and n.split('.')[0] in blocked]
assert not active,active
report=dict(schema=1,job_script_sha256=hashlib.sha256(Path('/job.py').read_bytes()).hexdigest(),scope='Actual CPU BPE with pinned target wrapper bodies; not native inference or full engine qualification',
 model_revision=prereg['model_revision'],engine_source=prereg['engine_source'],
 ast_selected_target_functions=True,tokenizer_protocol_base_is_object=True,
 full_target_engine_imported=False,model_libraries_imported=False,gpu_device_nodes=[],gpu_execution=False,
 weights_opened=False,network_disabled=True,package_installation=False,
 packages={name:importlib.metadata.version(name) for name in ('transformers','tokenizers','regex')},
 source_sha256={name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in ('v4_encoding.py','v4_tokenizer.py','hf_tokenizer.py','reference_encoding.py')},
 input_assets=expected,results=results,native_gold_generated=False,target_runtime_revalidation_required=True)
print(json.dumps(report))
