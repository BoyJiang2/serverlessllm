from pathlib import Path
import subprocess,json,hashlib
cid='ac3a81f1426c8697120ddcadca8f3688654d0aa7370acba9f86cee6b924f0d8c';root=Path('/home/boyjiang/sllm-v4-pro/evidence');base=root/'p4a-bootstrap-20261106'
def run(*a):return subprocess.run(['docker',*a],check=True)
run('exec',cid,'mkdir','-p','/opt/p4a-tokenizer/assets')
run('cp',str(root/'cpu-tokenizer-20261106/inputs'),cid+':/opt/p4a-tokenizer/inputs')
for name in ['tokenizer.json','tokenizer_config.json']:run('cp','/mnt/raid/models/DeepSeek-V4-Pro/'+name,cid+':/opt/p4a-tokenizer/assets/'+name)
with (base/'target-cpu-bpe-ast.log').open('x') as log:
 r=subprocess.run(['docker','exec','-e','USE_TORCH=0','-e','USE_TF=0','-e','USE_FLAX=0','-e','HF_HUB_OFFLINE=1','-e','TRANSFORMERS_OFFLINE=1','-e','TOKENIZERS_PARALLELISM=false','-e','RAYON_NUM_THREADS=1',cid,'/opt/sllm-v4/bin/python','/logs/check_target_cpu_bpe_ast.py'],stdout=log,stderr=subprocess.STDOUT)
assert r.returncode==0,(base/'target-cpu-bpe-ast.log').read_text()[-2000:]
old=json.loads((root/'cpu-tokenizer-20261106/stdout.json').read_bytes());new=json.loads((base/'target-cpu-bpe-ast.json').read_bytes())
assert [x['input_tokens'] for x in old['results']]==[x['input_tokens'] for x in new['results']]
print(json.dumps(dict(packages=new['packages'],results=[dict(file=x['file'],tokens=x['token_count']) for x in new['results']],identical_to_prior_cpu_input_ids=True,scope='Actual target Transformers BPE with pinned wrapper AST; full engine import still pending')))
