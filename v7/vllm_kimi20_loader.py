"""V7 candidate derived from Kimi's qualified postprocessed Store loader.

Retain its empty-tensor and canonical-alias selection rules. Native 0.20.1's
ShardedStateLoader has different rules and is not an interchangeable helper.
"""
import importlib.metadata
import json
from pathlib import Path

import torch
from torch import nn
import collections
import os
import gc
from typing import Dict
from vllm.config import VllmConfig, ModelConfig
from vllm.config.load import LoadConfig
from vllm.model_executor.model_loader.base_loader import BaseModelLoader
from vllm.model_executor.model_loader import register_model_loader
from vllm.model_executor.model_loader.utils import (
    initialize_model)
from vllm.utils.torch_utils import set_default_torch_dtype
from sllm_store.event_trace import event_span, traced
from vllm.distributed import get_tensor_model_parallel_rank

LOAD_FORMAT = "sllm_kimi26_postprocessed_0201"


def finish_restored_weights(model, model_config, device):
    from vllm.model_executor.layers.attention import Attention, MLAAttention
    from vllm.model_executor.layers.quantization.compressed_tensors.compressed_tensors_moe.compressed_tensors_moe_wna16_marlin import CompressedTensorsWNA16MarlinMoEMethod
    from vllm.model_executor.layers.quantization.utils.marlin_utils import marlin_make_workspace_new

    for module in model.modules():
        method = getattr(module, "quant_method", None)
        if method is not None:
            # The frozen Kimi config quantizes expert weights only.
            # Do not run repack/scale permutation a second time.
            if type(method).__name__ in ("UnquantizedLinearMethod", "UnquantizedEmbeddingMethod"):
                continue
            if not isinstance(method, CompressedTensorsWNA16MarlinMoEMethod):
                raise ValueError(f"Unsupported restored Kimi quant method: {type(method)}")
            if method.kernel_backend != "Marlin" or method.marlin_input_dtype is not None:
                raise ValueError("Historical Kimi payload requires BF16-input Marlin")
            module.workspace = marlin_make_workspace_new(device, 4)
    # Match only the attention portion of target process_weights_after_loading.
    for module in model.modules():
        if isinstance(module, (Attention, MLAAttention)) and hasattr(module, "process_weights_after_loading"):
            module.process_weights_after_loading(model_config.dtype)

class ServerlessLLMLoader(BaseModelLoader):
    # DEFAULT_PATTERN = "model-rank-{rank}-part-{part}.safetensors"

    def __init__(self, load_config: LoadConfig):
        super().__init__(load_config)
        extra_config = ({} if load_config.model_loader_extra_config is None
                        else load_config.model_loader_extra_config.copy())
        # self.pattern = extra_config.pop("pattern", self.DEFAULT_PATTERN)
        if extra_config:
            raise ValueError(f"Unexpected extra config keys for load format "
                             f"{load_config.load_format}: "
                             f"{load_config.model_loader_extra_config.keys()}")

    @staticmethod
    def _filter_subtensors(
            tensors: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """
        Filter out all tensors that share the same memory or a subset of the
        memory of another tensor.
        """
        same_storage_groups = collections.defaultdict(list)
        result = {}
        for key, tensor in tensors.items():
            if not tensor.numel():
                result[key] = tensor
            else:
                ptr = tensor.untyped_storage().data_ptr()
                same_storage_groups[tensor.device, ptr].append((key, tensor))

        def get_end_ptr(tensor: torch.Tensor) -> int:
            return tensor.view(-1)[-1].data_ptr() + tensor.element_size()

        for group in same_storage_groups.values():
            for k, t in group:
                a, b = t.data_ptr(), get_end_ptr(t)
                for k2, t2 in group:
                    if not t2.is_contiguous():
                        continue
                    a2, b2 = t2.data_ptr(), get_end_ptr(t2)
                    if a < a2 or b2 < b:
                        continue
                    if a2 < a or b < b2 or not t.is_contiguous():
                        break  # t2 covers strictly more memory than t.
                    if k2 > k:
                        # Same tensors, keep the one with the longer key.
                        break
                else:
                    result[k] = t
        return result
        
    @traced("full_weight_load", values_from=lambda args, kwargs: dict(
        rank=get_tensor_model_parallel_rank()))
    def load_model(
        self,
        vllm_config: VllmConfig,
        model_config: ModelConfig,
        prefix: str = "",
    ) -> nn.Module:
        from sllm_store.torch import load_dict_owned
        from sllm_store.vllm_v4_loader import check_engine_source

        if (importlib.metadata.version("vllm"), torch.__version__) != ("0.20.1", "2.11.0+cu130"):
            raise ValueError("V7 Kimi loader requires the pinned target environment")
        check_engine_source()
        if (vllm_config.parallel_config.tensor_parallel_size != 8
                or model_config.quantization != "compressed-tensors"
                or model_config.dtype != torch.bfloat16
                or model_config.hf_config.model_type != "kimi_k25"):
            raise ValueError("Kimi restore is scoped to the frozen INT4 BF16 TP8 model")
        
        assert os.path.isdir(model_config.model)
        
        rank = get_tensor_model_parallel_rank()

        local_model_path = model_config.model
        local_model_path = os.path.join(local_model_path, f"rank_{rank}")
        index = json.loads((Path(local_model_path) / "tensor_index.json").read_text())

        # vLLM needs a local model path to read model config but
        # ServerlessLLM Store requires a global model path as the model ID
        storage_path = os.getenv("STORAGE_PATH", os.path.expanduser("~/models"))
        model_path = str(Path(local_model_path).resolve().relative_to(Path(storage_path).resolve()))
        
        with set_default_torch_dtype(model_config.dtype):
            # with torch.device(device_config.device):
            with torch.device("cpu"):
                model = initialize_model(
                    vllm_config=vllm_config,
                    model_config=model_config,
                    prefix=prefix,
                )
                model = model.eval()
            state_dict = self._filter_subtensors(model.state_dict())
            key_list = list(state_dict)
            if set(key_list) != set(index):
                raise ValueError("Kimi target skeleton and historical Store keys differ")
            bindings = dict(model.named_parameters(
                recurse=True, remove_duplicate=False))
            bindings.update(model.named_buffers(
                recurse=True, remove_duplicate=False))
            canonical_objects = {id(bindings[key]) for key in key_list}
            for name in model.state_dict():
                if name not in state_dict and id(bindings[name]) not in canonical_objects:
                    raise ValueError(f"Unsupported distinct-object Kimi storage alias: {name}")
            del state_dict
            # Release constructor storage before allocating the restored rank.
            for key in key_list:
                tensor = bindings[key]
                tensor.data = torch.empty(0, dtype=tensor.dtype, device="cpu")
            gc.collect()

            device_id = torch.cuda.current_device()
            device_map = {"": device_id}
            # Note: storage path is already included in the local model path
            with event_span("weight_ingestion", rank=rank):
                sllm_state_dict = load_dict_owned(model_path, device_map)
            if set(sllm_state_dict) != set(index):
                raise ValueError("Kimi Store returned an incomplete or extra tensor set")
            model._sllm_storage_owners = tuple(sllm_state_dict.values())

            for key in key_list:
                value = sllm_state_dict[key]
                _, size, shape, stride, dtype = index[key]
                if (list(value.shape) != shape or list(value.stride()) != stride
                        or str(value.dtype) != dtype
                        or value.numel() * value.element_size() != size):
                    raise ValueError(f"Kimi restored tensor metadata mismatch: {key}")
                bindings[key].data = sllm_state_dict[key]

            for name, buffer in model.named_buffers(recurse=True):
                if buffer.device.type != 'cuda':
                    buffer.data = buffer.data.to(f'cuda:{device_id}')

            with event_span("restore_postprocess", rank=rank):
                finish_restored_weights(model, model_config, torch.device(f"cuda:{device_id}"))
            
        return model
    
    def download_model(self, model_config: ModelConfig) -> None:
        pass

    def load_weights(self, model, model_config):
        raise NotImplementedError("V7 Kimi restore requires a fresh model; not a Sleep L2 reload")


def register():
    register_model_loader(LOAD_FORMAT)(ServerlessLLMLoader)
