# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: Copyright contributors to the vLLM project
# Original v0.19.1 loader-method ASTs only; runtime constructors not included.
import torch
from torch.nn import Parameter
from torch.nn.parameter import UninitializedParameter
# Guards for unselected quantized branches; tested params are plain BF16 Parameter.
class BlockQuantScaleParameter: pass
class BasevLLMParameter: pass

class ColumnParallelLinear:

    def weight_loader(self, param: Parameter, loaded_weight: torch.Tensor):
        output_dim = getattr(param, 'output_dim', None)
        is_sharded_weight = getattr(param, 'is_sharded_weight', False)
        use_bitsandbytes_4bit = getattr(param, 'use_bitsandbytes_4bit', False)
        is_sharded_weight = is_sharded_weight or use_bitsandbytes_4bit
        is_gguf_weight = getattr(param, 'is_gguf_weight', False)
        is_gguf_weight_type = getattr(param, 'is_gguf_weight_type', False)
        if is_gguf_weight_type:
            param.weight_type = loaded_weight.item()
        if is_gguf_weight and isinstance(param, UninitializedParameter):
            final_shape = list(loaded_weight.shape)
            if output_dim is not None:
                assert final_shape[output_dim] % self.tp_size == 0
                final_shape[output_dim] = final_shape[output_dim] // self.tp_size
            param.materialize(final_shape, dtype=loaded_weight.dtype)
        param_data = param.data
        if output_dim is not None and (not is_sharded_weight):
            shard_size = param_data.shape[output_dim]
            start_idx = self.tp_rank * shard_size
            loaded_weight = loaded_weight.narrow(output_dim, start_idx, shard_size)
        if len(loaded_weight.shape) == 0:
            loaded_weight = loaded_weight.reshape(1)
        assert param_data.shape == loaded_weight.shape
        param_data.copy_(loaded_weight)

class MergedColumnParallelLinear:

    def validate_shard_id(self, loaded_shard_id: int | tuple[int, ...] | None):
        if loaded_shard_id is None:
            return
        if isinstance(loaded_shard_id, tuple):
            for idx in loaded_shard_id:
                if not 0 <= idx < len(self.output_sizes):
                    raise ValueError(f'Shard id index {idx} should be between 0 and {len(self.output_sizes) - 1}. Got shard id {loaded_shard_id}.')
            if len(loaded_shard_id) > 1 and any((b - a != 1 for a, b in zip(loaded_shard_id[:-1], loaded_shard_id[1:]))):
                raise ValueError(f'Shard id with multiple indices should be consecutive. Got shard id {loaded_shard_id}.')
            return
        elif isinstance(loaded_shard_id, int):
            if loaded_shard_id < 0 or loaded_shard_id >= len(self.output_sizes):
                raise ValueError(f'Shard id should be between 0 and {len(self.output_sizes) - 1}. Got shard id {loaded_shard_id}.')
            return
        raise ValueError('This line should not be reached')

    def weight_loader(self, param: Parameter, loaded_weight: torch.Tensor, loaded_shard_id: tuple[int, ...] | int | None=None):
        self.validate_shard_id(loaded_shard_id)
        is_gguf_weight = getattr(param, 'is_gguf_weight', False)
        is_gguf_weight_type = getattr(param, 'is_gguf_weight_type', False)
        if isinstance(loaded_shard_id, tuple) and (is_gguf_weight or is_gguf_weight_type):
            raise NotImplementedError('Shard id with multiple indices is not supported for GGUF.')
        if is_gguf_weight_type:
            if loaded_shard_id is not None:
                param.data[loaded_shard_id].copy_(loaded_weight)
                param.shard_weight_type[loaded_shard_id] = loaded_weight.item()
            else:
                param.shard_weight_type = {i: loaded_weight.item() for i, _ in enumerate(self.output_sizes)}
            return
        if is_gguf_weight:
            output_dim = getattr(param, 'output_dim', None)
            shard_size = loaded_weight.size(output_dim) // self.tp_size
            start_idx = self.tp_rank * shard_size
            if loaded_shard_id is not None:
                loaded_weight = loaded_weight.narrow(output_dim, start_idx, shard_size)
                param.shard_id.append(loaded_shard_id)
                param.shard_id_map[loaded_shard_id] = len(param.data_container)
                param.data_container.append(loaded_weight)
                return
        param_data = param.data
        output_dim = getattr(param, 'output_dim', None)
        needs_scalar_to_array = getattr(param, 'needs_scalar_to_array', False)
        if loaded_shard_id is None or isinstance(loaded_shard_id, tuple):
            if output_dim is None:
                if needs_scalar_to_array:
                    param_data, loaded_weight = adjust_scalar_to_fused_array(param_data, loaded_weight, 0)
                assert param_data.shape == loaded_weight.shape
                param_data.copy_(loaded_weight)
                return
            output_sizes = self.output_sizes[loaded_shard_id[0]:loaded_shard_id[-1] + 1] if loaded_shard_id is not None else self.output_sizes
            current_shard_offset = 0
            use_bitsandbytes_4bit = getattr(param, 'use_bitsandbytes_4bit', False)
            if use_bitsandbytes_4bit and isinstance(loaded_shard_id, tuple) and (self.tp_size > 1):
                raise NotImplementedError('Shard id with multiple indices is not supported for BNB quantization with TP yet.')
            shard_offsets: list[tuple[int, int, int]] = []
            for i, output_size in enumerate(output_sizes):
                shard_offsets.append((i, current_shard_offset, output_size))
                current_shard_offset += output_size
            packed_dim = getattr(param, 'packed_dim', None)
            for shard_id, shard_offset, shard_size in shard_offsets:
                if isinstance(param, BlockQuantScaleParameter):
                    weight_block_size = getattr(self, 'weight_block_size', None)
                    shard_size, shard_offset = adjust_block_scale_shard(weight_block_size, shard_size, shard_offset)
                if packed_dim == output_dim:
                    shard_size = shard_size // param.packed_factor
                    shard_offset = shard_offset // param.packed_factor
                    shard_size, shard_offset = adjust_marlin_shard(param, shard_size, shard_offset)
                if use_bitsandbytes_4bit:
                    index = list(itertools.accumulate([0] + self.output_sizes))
                    orig_offsets = {str(i): (index[i], size) for i, size in enumerate(self.output_sizes)}
                    orig_offsets['total'] = (self.output_size, 0)
                    shard_size, shard_offset = adjust_bitsandbytes_4bit_shard(param, orig_offsets, str(shard_id))
                loaded_weight_shard = loaded_weight.narrow(output_dim, shard_offset, shard_size)
                self.weight_loader(param, loaded_weight_shard, shard_id)
            return
        assert loaded_shard_id < len(self.output_sizes)
        if output_dim is not None:
            shard_offset = sum(self.output_sizes[:loaded_shard_id])
            shard_size = self.output_sizes[loaded_shard_id]
            shard_offset //= self.tp_size
            shard_size //= self.tp_size
            if isinstance(param, BlockQuantScaleParameter):
                weight_block_size = getattr(self, 'weight_block_size', None)
                shard_size, shard_offset = adjust_block_scale_shard(weight_block_size, shard_size, shard_offset)
            packed_dim = getattr(param, 'packed_dim', None)
            if packed_dim == output_dim:
                shard_size = shard_size // param.packed_factor
                shard_offset = shard_offset // param.packed_factor
                shard_size, shard_offset = adjust_marlin_shard(param, shard_size, shard_offset)
            use_bitsandbytes_4bit = getattr(param, 'use_bitsandbytes_4bit', False)
            is_sharded_weight = getattr(param, 'is_sharded_weight', False)
            is_sharded_weight = is_sharded_weight or use_bitsandbytes_4bit
            if use_bitsandbytes_4bit:
                index = list(itertools.accumulate([0] + self.output_sizes))
                orig_offsets = {str(i): (index[i], size) for i, size in enumerate(self.output_sizes)}
                orig_offsets['total'] = (self.output_size, 0)
                shard_size, shard_offset = adjust_bitsandbytes_4bit_shard(param, orig_offsets, str(loaded_shard_id))
            param_data = param_data.narrow(output_dim, shard_offset, shard_size)
            start_idx = self.tp_rank * shard_size
            if not is_sharded_weight:
                loaded_weight = loaded_weight.narrow(output_dim, start_idx, shard_size)
        elif needs_scalar_to_array:
            param_data, loaded_weight = adjust_scalar_to_fused_array(param_data, loaded_weight, loaded_shard_id)
        else:
            ignore_warning = getattr(param, 'ignore_warning', False)
            if not ignore_warning:
                logger.warning('Loading a weight without `output_dim` attribute in MergedColumnParallelLinear, assume the weight is the same for all partitions.')
        assert param_data.shape == loaded_weight.shape
        param_data.copy_(loaded_weight)

class RowParallelLinear:

    def weight_loader(self, param: Parameter, loaded_weight: torch.Tensor):
        input_dim = getattr(param, 'input_dim', None)
        use_bitsandbytes_4bit = getattr(param, 'use_bitsandbytes_4bit', False)
        is_sharded_weight = getattr(param, 'is_sharded_weight', False)
        is_sharded_weight = is_sharded_weight or use_bitsandbytes_4bit
        is_gguf_weight = getattr(param, 'is_gguf_weight', False)
        is_gguf_weight_type = getattr(param, 'is_gguf_weight_type', False)
        if is_gguf_weight_type:
            param.weight_type = loaded_weight.item()
        if is_gguf_weight and isinstance(param, UninitializedParameter):
            weight_shape = list(loaded_weight.shape)
            if input_dim:
                weight_shape[input_dim] = weight_shape[input_dim] // self.tp_size
            param.materialize(tuple(weight_shape), dtype=loaded_weight.dtype)
        param_data = param.data
        if input_dim is not None and (not is_sharded_weight):
            shard_size = param_data.shape[input_dim]
            start_idx = self.tp_rank * shard_size
            loaded_weight = loaded_weight.narrow(input_dim, start_idx, shard_size)
        if len(loaded_weight.shape) == 0:
            loaded_weight = loaded_weight.reshape(1)
        assert param_data.shape == loaded_weight.shape
        param_data.copy_(loaded_weight)

class VocabParallelEmbedding:

    def weight_loader(self, param: Parameter, loaded_weight: torch.Tensor):
        output_dim = getattr(param, 'output_dim', None)
        packed_dim = getattr(param, 'packed_dim', None)
        if getattr(param, 'is_gguf_weight_type', None):
            param.data.copy_(loaded_weight)
            param.weight_type = loaded_weight.item()
            return
        elif isinstance(param, UninitializedParameter):
            shape = list(loaded_weight.shape)
            if output_dim is not None:
                shape[output_dim] = self.num_embeddings_per_partition
            param.materialize(tuple(shape), dtype=loaded_weight.dtype)
        if output_dim is None:
            assert param.data.shape == loaded_weight.shape
            param.data.copy_(loaded_weight)
            return
        start_idx = self.shard_indices.org_vocab_start_index
        shard_size = self.shard_indices.org_vocab_end_index - start_idx
        if packed_dim is not None and packed_dim == output_dim:
            packed_factor = param.packed_factor if isinstance(param, BasevLLMParameter) else param.pack_factor
            assert loaded_weight.shape[output_dim] == self.org_vocab_size // param.packed_factor
            start_idx = start_idx // packed_factor
            shard_size = shard_size // packed_factor
        else:
            assert loaded_weight.shape[output_dim] == self.org_vocab_size
        loaded_weight = loaded_weight.narrow(output_dim, start_idx, shard_size)
        param[:loaded_weight.shape[0]].data.copy_(loaded_weight)
        param[loaded_weight.shape[0]:].data.fill_(0)
