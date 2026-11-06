import sys,importlib.util,os,json
s=importlib.util.spec_from_file_location('nvml_probe','/control/pynvml.py');m=importlib.util.module_from_spec(s);sys.modules[s.name]=m;s.loader.exec_module(m)
m.nvmlInit()
try:values=[m.nvmlDeviceGetUUID(m.nvmlDeviceGetHandleByIndex(i)) for i in range(8)]
finally:m.nvmlShutdown()
print(json.dumps(dict(values=values,types=[type(v).__name__ for v in values],cuda_visible=os.getenv('CUDA_VISIBLE_DEVICES'),nvidia_visible=os.getenv('NVIDIA_VISIBLE_DEVICES'),torch_imported='torch' in sys.modules)),flush=True)
