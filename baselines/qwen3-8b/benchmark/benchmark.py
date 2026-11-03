import argparse,ctypes,gc,hashlib,json,mmap,os,pathlib,signal,subprocess,sys,time
ROOT=pathlib.Path('/models/Qwen3-8B');TASK=pathlib.Path('/task');STORE=TASK/'store';MID='qwen3-8b'
parser=argparse.ArgumentParser();parser.add_argument('mode');parser.add_argument('--backend');parser.add_argument('--case',default='warm');parser.add_argument('--trial',type=int,default=0);args=parser.parse_args()
signal.alarm(300 if args.mode=='trial' else 2200)
def cache_bytes(paths):
 libc=ctypes.CDLL(None,use_errno=True);libc.mmap.restype=ctypes.c_void_p;libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long];libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p];libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
 resident=0
 for p in paths:
  with p.open('rb') as f:
   n=p.stat().st_size;address=libc.mmap(None,n,0,1,f.fileno(),0);assert address!=ctypes.c_void_p(-1).value
   vec=(ctypes.c_ubyte*((n+4095)//4096))()
   try:assert libc.mincore(address,n,vec)==0;resident+=sum(v&1 for v in vec)*4096
   finally:libc.munmap(address,n)
 return resident
if args.mode=='server':
 import asyncio
 from sllm_store.server import serve
 asyncio.run(serve(host='127.0.0.1',port=8073,storage_path=str(STORE),mem_pool_size=32*1024**3,num_thread=1,chunk_size=32*1024**2,registration_required=False))
elif args.mode=='convert':
 import torch
 from transformers import AutoModelForCausalLM
 from sllm_store.transformers import save_model
 assert not (STORE/MID).exists(),'refuse to overwrite converted archive'
 model=AutoModelForCausalLM.from_pretrained(ROOT,torch_dtype=torch.bfloat16,device_map={'':'cpu'},local_files_only=True)
 save_model(model,str(STORE/MID))
 print(json.dumps({'conversion_complete':True,'path':str(STORE/MID)}),flush=True)
elif args.mode=='trial':
 import torch,transformers
 from transformers import AutoModelForCausalLM,AutoTokenizer
 from sllm_store.transformers import load_model
 torch.set_num_threads(4);torch.manual_seed(0)
 tokenizer=AutoTokenizer.from_pretrained(ROOT,local_files_only=True)
 inputs=tokenizer('Hello',return_tensors='pt')
 torch.cuda.init();dummy=torch.ones(1024,device='cuda');torch.cuda.synchronize();del dummy;torch.cuda.empty_cache()
 paths=sorted(ROOT.glob('*.safetensors')) if args.backend=='safetensors' else sorted((STORE/MID).glob('tensor.data_*'))
 assert paths
 if args.backend=='serverless':
  from sllm_store.client import SllmStoreClient
  client=SllmStoreClient()
  if args.case=='cold':assert client.unload_from_cpu(MID),'CPU cache unload failed'
 if args.case=='cold':
  for p in paths:
   with p.open('rb') as f:os.posix_fadvise(f.fileno(),0,0,os.POSIX_FADV_DONTNEED)
 elif args.backend=='safetensors':
  for p in paths:
   with p.open('rb') as f:
    while f.read(8*1024*1024):pass
 before=cache_bytes(paths)
 if args.case=='cold':assert before==0,('file pages remain resident',before)
 import pynvml,threading
 pynvml.nvmlInit();handle=pynvml.nvmlDeviceGetHandleByUUID('GPU-736d7f37-f647-b82d-ab11-693753ac6822');memory_peak=[0];memory_errors=[];stop=threading.Event()
 def sample_memory():
  while not stop.is_set():
   try:memory_peak[0]=max(memory_peak[0],pynvml.nvmlDeviceGetMemoryInfo(handle).used)
   except Exception as error:memory_errors.append(type(error).__name__);return
   stop.wait(0.01)
 sampler=threading.Thread(target=sample_memory,daemon=True);sampler.start()
 torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats();started=time.perf_counter()
 if args.backend=='safetensors':model=AutoModelForCausalLM.from_pretrained(ROOT,torch_dtype=torch.bfloat16,device_map={'':0},local_files_only=True)
 else:
  model=load_model(MID,device_map={'':0},torch_dtype=torch.bfloat16,storage_path=str(STORE))
 torch.cuda.synchronize();elapsed=time.perf_counter()-started;peak=torch.cuda.max_memory_allocated();stop.set();sampler.join();pynvml.nvmlShutdown();assert elapsed>0 and not memory_errors and memory_peak[0]>0
 model.eval();h=hashlib.sha256();count=0
 for name,p in sorted(model.named_parameters()):
  assert p.dtype==torch.bfloat16
  h.update(name.encode()+b'\0');h.update(str(tuple(p.shape)).encode());data=p.detach().contiguous().view(-1).view(torch.uint8);count+=p.numel()
  for offset in range(0,data.numel(),8*1024*1024):h.update(data[offset:offset+8*1024*1024].cpu().numpy().tobytes())
 with torch.inference_mode():logits=model(**{k:v.cuda() for k,v in inputs.items()}).logits[:,-1,:].float().cpu()
 assert torch.isfinite(logits).all();torch.cuda.synchronize()
 result={'backend':args.backend,'cache_case':args.case,'trial':args.trial,'load_seconds':elapsed,'peak_torch_allocated_bytes':peak,'sampled_gpu_used_peak_bytes':memory_peak[0],'gpu_memory_sample_interval_ms':10,'torch_allocator_counter_excludes_native_cuda_malloc':True,'parameter_count':count,'parameter_sha256':h.hexdigest(),'next_token':int(logits.argmax()),'logits_sha256':hashlib.sha256(logits.numpy().tobytes()).hexdigest(),'input_tokens':inputs['input_ids'].tolist(),'file_cache_resident_before':before,'torch':torch.__version__,'transformers':transformers.__version__,'dtype':'bfloat16','cuda_init_and_imports_timed':False,'full_parameter_hash_and_forward_timed':False}
 (TASK/'results').mkdir(exist_ok=True);(TASK/'results'/f'{args.backend}-{args.case}-{args.trial}.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
else:raise ValueError(args.mode)
