import json,os,getpass,pwd,sys,multiprocessing
from pathlib import Path
import torch
from vllm import LLM,SamplingParams
import vllm
from torch._inductor.runtime.cache_dir_utils import cache_dir
def check(name):
 assert getpass.getuser()=='boyjiang' and pwd.getpwuid(os.getuid()).pw_name=='boyjiang'
 assert not list(Path('/dev').glob('nvidia*')) and not torch.cuda.is_initialized()
 path=Path(cache_dir());path.mkdir(parents=True,exist_ok=True)
 (path/(name+'.probe')).write_text('writable')
 assert vllm.__file__.startswith('/opt/sllm-v4/lib/python3.12/site-packages/')
 d=dict(uid=os.getuid(),gid=os.getgid(),getpass_user=getpass.getuser(),passwd_user=pwd.getpwuid(os.getuid()).pw_name,
  torch_version=torch.__version__,vllm_version=vllm.__version__,vllm_path=vllm.__file__,cache_dir=str(path),
  cuda_initialized=torch.cuda.is_initialized(),model_constructed=False,role=name)
 Path('/output/'+name+'.json').write_text(json.dumps(d,indent=2)+'\n')
if __name__=='__main__':
 check('parent')
 p=multiprocessing.get_context('spawn').Process(target=check,args=('spawn-child',));p.start();p.join(45)
 assert p.exitcode==0
 print('UID_PROFILE_IMPORT_AND_SPAWN_PASS',flush=True)
