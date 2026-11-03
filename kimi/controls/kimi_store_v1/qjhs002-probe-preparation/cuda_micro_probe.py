"""One MiB CUDA arithmetic probe; no model, Store, or vLLM."""
import json,time
import torch
torch.set_num_threads(1)
assert torch.cuda.device_count()==1
torch.cuda.reset_peak_memory_stats()
value=torch.full((1<<20,),3,dtype=torch.uint8,device='cuda:0')
torch.cuda.synchronize()
host=value.cpu()
assert host.numel()==1<<20 and bool((host==3).all())
assert int(host.sum())==3*(1<<20)
print(json.dumps({'event':'CUDA_MICRO_COMPUTE_PASS','torch':torch.__version__,'cuda':torch.version.cuda,'device':torch.cuda.get_device_name(0),'payload_bytes':value.numel()*value.element_size(),'allocator_peak_bytes':torch.cuda.max_memory_allocated(),'allocator_reserved_peak_bytes':torch.cuda.max_memory_reserved(),'scope':'one MiB CUDA arithmetic and CPU copy only; hold for independent guard kill'}),flush=True)
time.sleep(120)
