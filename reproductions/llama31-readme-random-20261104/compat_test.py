import torch
from accelerate import init_empty_weights
from transformers import AutoConfig, AutoModelForCausalLM
from sllm_store.device_map_utils import _compute_device_placement_from_map
config=AutoConfig.from_pretrained('/checkpoint',local_files_only=True)
with init_empty_weights():
    model=AutoModelForCausalLM.from_config(config,torch_dtype=torch.float16)
assert not hasattr(model,'_get_no_split_modules')
assert set(model._no_split_modules)=={'LlamaDecoderLayer'}
assert all(p.is_meta for p in model.parameters())
placement=_compute_device_placement_from_map(model,'auto',torch.float16,max_memory={'cpu':200*1024**3})
assert placement=={'':'cpu'},placement
assert not torch.cuda.is_initialized()
print('LLAMA_TF55_AUTO_PLACEMENT_CPU_META_PASS',placement)
