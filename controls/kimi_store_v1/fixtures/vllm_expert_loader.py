# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Exact method ASTs from pinned v0.19.1 FusedMoE; no runtime construction.
import torch
from enum import Enum
from torch.nn.parameter import UninitializedParameter

class FusedMoeWeightScaleSupported(Enum):
    TENSOR = 'tensor'
    CHANNEL = 'channel'
    GROUP = 'group'
    BLOCK = 'block'

class ExtractedFusedMoE:

    def _load_model_weight_or_group_weight_scale(self, shard_dim: int, expert_data: torch.Tensor, shard_id: str, loaded_weight: torch.Tensor, tp_rank: int, load_full_w2: bool=False):
        """
        Load grouped weight scales for group quantization or model weights
            :param shard_dim: dimension to shard
            :param expert_data: parameter for a particular expert
            :param shard_id: either w1, w2, or w3
            :param loaded_weight: checkpoint weight to load into the param
            :param tp_rank: tensor parallel rank
            :param load_full_w2: whether or not the w2 loaded should be sharded.
        """
        if shard_id == 'w2':
            self._load_w2(shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=tp_rank, load_full=load_full_w2)
        elif shard_id in ('w1', 'w3'):
            self._load_w13(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=tp_rank)

    def _load_w13(self, expert_data: torch.Tensor, shard_dim: int, shard_id: str, loaded_weight: torch.Tensor, tp_rank: int, load_full: bool=False):
        if self.moe_config.is_act_and_mul:
            shard_size = expert_data.shape[shard_dim] // 2
        else:
            shard_size = expert_data.shape[shard_dim]
        if not load_full and loaded_weight.ndim > 0:
            start_offset = shard_size * tp_rank
            available = loaded_weight.shape[shard_dim] - start_offset
            if available <= 0:
                return
            narrow_size = min(shard_size, available)
            loaded_weight = loaded_weight.narrow(shard_dim, start_offset, narrow_size)
        if shard_id == 'w1':
            expert_data = expert_data.narrow(shard_dim, 0, shard_size)
        else:
            assert shard_id == 'w3'
            expert_data = expert_data.narrow(shard_dim, shard_size, shard_size)
        if expert_data.shape != loaded_weight.shape:
            expert_data = expert_data[:loaded_weight.shape[0], :loaded_weight.shape[1]]
        expert_data.copy_(loaded_weight)

    def _load_w2(self, expert_data: torch.Tensor, shard_dim: int, loaded_weight: torch.Tensor, tp_rank: int, load_full: bool=False):
        shard_size = expert_data.shape[shard_dim]
        if not load_full and loaded_weight.ndim > 0:
            start_offset = shard_size * tp_rank
            available = loaded_weight.shape[shard_dim] - start_offset
            if available <= 0:
                return
            narrow_size = min(shard_size, available)
            loaded_weight = loaded_weight.narrow(shard_dim, start_offset, narrow_size)
        if expert_data.shape != loaded_weight.shape:
            expert_data = expert_data[:loaded_weight.shape[0], :loaded_weight.shape[1]]
        expert_data.copy_(loaded_weight)

    def _load_single_value(self, param: torch.nn.Parameter, loaded_weight: torch.Tensor, expert_id: int):
        param_data = param.data
        param_data[expert_id] = loaded_weight

    def weight_loader(self, param: torch.nn.Parameter, loaded_weight: torch.Tensor, weight_name: str, shard_id: str, expert_id: int, return_success: bool=False) -> bool | None:
        if self.quant_config and self.quant_config.get_name() == 'mxfp4':
            if 'bias' in weight_name:
                dim1 = loaded_weight.shape[1]
                param.data[:, :dim1].copy_(loaded_weight)
            else:
                dim1 = loaded_weight.shape[1]
                dim2 = loaded_weight.shape[2]
                param.data[:, :dim1, :dim2].copy_(loaded_weight)
            return True if return_success else None
        quant_method_name = self.quant_method.__class__.__name__
        global_expert_id = expert_id
        expert_id = self._map_global_expert_id_to_local_expert_id(global_expert_id)
        use_global_sf = getattr(self.quant_method, 'use_global_sf', False) and 'input_scale' in weight_name
        if expert_id == -1 and (not use_global_sf):
            return False if return_success else None
        is_transposed = getattr(param, 'is_transposed', False)
        if quant_method_name in ('CompressedTensorsWNA16MarlinMoEMethod', 'CompressedTensorsWNA16MoEMethod'):
            if is_transposed:
                loaded_weight = loaded_weight.t().contiguous()
            else:
                loaded_weight = loaded_weight
        if shard_id not in ('w1', 'w2', 'w3'):
            raise ValueError(f"shard_id must be ['w1','w2','w3'] but got {shard_id}.")
        SHARD_ID_TO_SHARDED_DIM = {'w1': 0, 'w2': 1, 'w3': 0}
        is_gguf_weight = getattr(param, 'is_gguf_weight', False)
        is_gguf_weight_type = getattr(param, 'is_gguf_weight_type', False)
        if is_gguf_weight_type:
            param.weight_type = loaded_weight.item()
            param.data.copy_(loaded_weight)
            return True if return_success else None
        use_bitsandbytes_4bit = getattr(param, 'use_bitsandbytes_4bit', False)
        if use_bitsandbytes_4bit:
            shard_dim = 0
            expert_data = param.data[expert_id]
            if shard_id == 'w2':
                expert_data.copy_(loaded_weight)
            elif shard_id in ('w1', 'w3'):
                full_load = True
                self._load_w13(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank, load_full=full_load)
            return True if return_success else None
        shard_dim = SHARD_ID_TO_SHARDED_DIM[shard_id]
        if is_transposed:
            shard_dim = int(not shard_dim)
        full_load = len(loaded_weight.shape) == 3
        if full_load:
            shard_dim += 1
        if is_gguf_weight and isinstance(param, UninitializedParameter):
            assert full_load
            final_shape = list(loaded_weight.shape)
            if shard_id in {'w1', 'w3'}:
                final_shape[1] *= 2
            final_shape[shard_dim] = final_shape[shard_dim] // self.tp_size
            param.materialize(final_shape, dtype=loaded_weight.dtype)
        expert_data = param.data if full_load else param.data[expert_id]
        if 'input_scale' in weight_name:
            loaded_weight = loaded_weight.to(param.data.device)
            if 'compressed' in quant_method_name.lower() and param.data[expert_id] != 1 and ((param.data[expert_id] - loaded_weight).abs() > 1e-05):
                raise ValueError(f'input_scales of w1 and w3 of a layer must be equal. But got {param.data[expert_id]} vs. {loaded_weight}')
            self._load_single_value(param=param, loaded_weight=loaded_weight, expert_id=global_expert_id if use_global_sf else expert_id)
            return True if return_success else None
        if 'g_idx' in weight_name:
            self._load_g_idx(shard_dim=0, shard_id=shard_id, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank)
            return True if return_success else None
        if 'ModelOpt' in quant_method_name:
            uses_weight_scale_2 = self.quant_method.uses_weight_scale_2_pattern()
            quant_method = getattr(param, 'quant_method', None)
            is_block_weight_scale = 'weight_scale' in weight_name and quant_method == FusedMoeWeightScaleSupported.BLOCK.value
            is_per_tensor = ('weight_scale_2' in weight_name if uses_weight_scale_2 else 'weight_scale' in weight_name) or 'input_scale' in weight_name
            is_per_tensor = is_per_tensor and (not is_block_weight_scale)
            if is_per_tensor:
                self._load_per_tensor_weight_scale(shard_id=shard_id, param=param, loaded_weight=loaded_weight, expert_id=expert_id)
                return True if return_success else None
            if 'w13_weight_scale' in weight_name:
                loaded_weight_hidden_out = loaded_weight.shape[-2]
                param_hidden_out = param.data.shape[-2] * self.tp_size
                if loaded_weight_hidden_out == param_hidden_out:
                    self._load_combined_w13_weight_scale(shard_dim=shard_dim, loaded_weight=loaded_weight, param=expert_data, tp_rank=self.tp_rank)
                    return True if return_success else None
            if 'weight' in weight_name:
                self._load_model_weight_or_group_weight_scale(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank)
            return True if return_success else None
        if 'scale' in weight_name or 'zero' in weight_name or 'offset' in weight_name:
            quant_method = getattr(param, 'quant_method', None)
            if quant_method == FusedMoeWeightScaleSupported.CHANNEL.value:
                self._load_per_channel_weight_scale(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank)
            elif quant_method in [FusedMoeWeightScaleSupported.GROUP.value, FusedMoeWeightScaleSupported.BLOCK.value]:
                self._load_model_weight_or_group_weight_scale(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank, load_full_w2=getattr(param, 'load_full_w2', False))
            elif quant_method == FusedMoeWeightScaleSupported.TENSOR.value:
                self._load_per_tensor_weight_scale(shard_id=shard_id, param=param, loaded_weight=loaded_weight, expert_id=expert_id)
            else:
                WEIGHT_SCALE_SUPPORTED = [e.value for e in FusedMoeWeightScaleSupported]
                raise ValueError(f'quant method must be one of {WEIGHT_SCALE_SUPPORTED}')
            return True if return_success else None
        if 'weight_shape' in weight_name:
            self._load_single_value(param=param, loaded_weight=loaded_weight, expert_id=expert_id)
            return True if return_success else None
        if 'weight' in weight_name:
            self._load_model_weight_or_group_weight_scale(shard_id=shard_id, shard_dim=shard_dim, loaded_weight=loaded_weight, expert_data=expert_data, tp_rank=self.tp_rank)
            return True if return_success else None
        return False if return_success else None
