"""Conditional nonexpert checkpoint audit rules; not runtime admission."""
import re


def describe(name, *, encoder_tp_mode='data', sequence_parallel_moe=False, use_mla=True):
    if encoder_tp_mode != 'data' or sequence_parallel_moe is not False or use_mla is not True:
        raise ValueError('profile outside reviewed conditional mapping')
    target, operation, slot = name, 'replicate', None
    category = None
    if name in ('language_model.lm_head.weight', 'language_model.model.embed_tokens.weight'):
        category, operation = 'vocabulary', 'column'
    elif name == 'language_model.model.norm.weight':
        category = 'norm'
    elif name.startswith('mm_projector.'):
        if re.fullmatch(r'mm_projector\.pre_norm\.(weight|bias)', name):
            category = 'projector'
        else:
            match = re.fullmatch(r'mm_projector\.proj\.(0|2)\.(weight|bias)', name)
            if match:
                category = 'projector'
                target = name.replace('proj.' + match[1], 'linear_' + ('1' if match[1] == '0' else '2'))
    elif name.startswith('vision_tower.'):
        block = re.fullmatch(r'vision_tower\.encoder\.blocks\.(\d+)\.'
                            r'(mlp\.fc[01]|norm[01]|wo|wqkv)\.(weight|bias)', name)
        if block and int(block[1]) in range(27):
            category = 'vision_encoder_data'
        elif re.fullmatch(r'vision_tower\.(encoder\.final_layernorm|patch_embed\.proj)\.(weight|bias)', name):
            category = 'vision_encoder_data'
        elif name == 'vision_tower.patch_embed.pos_emb.weight':
            category = 'vision_encoder_data'
    else:
        match = re.fullmatch(r'language_model\.model\.layers\.(\d+)\.(.+)', name)
        if match and int(match[1]) in range(61):
            layer, suffix = int(match[1]), match[2]
            if suffix in ('input_layernorm.weight', 'post_attention_layernorm.weight',
                          'self_attn.q_a_layernorm.weight', 'self_attn.kv_a_layernorm.weight'):
                category = 'norm'
            elif suffix in ('self_attn.q_a_proj.weight', 'self_attn.kv_a_proj_with_mqa.weight'):
                category, operation = 'mla_a', 'merged_replicate'
                slot = 0 if 'q_a_proj.' in suffix else 1
                target = name.replace('q_a_proj.weight' if slot == 0 else 'kv_a_proj_with_mqa.weight',
                                      'fused_qkv_a_proj.weight')
            elif suffix in ('self_attn.q_b_proj.weight', 'self_attn.kv_b_proj.weight'):
                category, operation = 'mla_b', 'column'
            elif suffix == 'self_attn.o_proj.weight':
                category, operation = 'attention_output', 'row'
            elif layer > 0 and suffix in ('mlp.gate.weight', 'mlp.gate.e_score_correction_bias'):
                category = 'router'
            else:
                mlp = re.fullmatch(r'mlp\.(shared_experts\.)?(gate|up|down)_proj\.weight', suffix)
                if mlp and ((layer == 0 and mlp[1] is None) or (layer > 0 and mlp[1] is not None)):
                    category = 'dense_mlp' if layer == 0 else 'shared_mlp'
                    if mlp[2] == 'down':
                        operation = 'row'
                    else:
                        operation, slot = 'merged_column', 0 if mlp[2] == 'gate' else 1
                        target = name.replace(mlp[2] + '_proj.weight', 'gate_up_proj.weight')
    if category is None:
        raise ValueError('unmapped nonexpert checkpoint name: ' + name)
    return dict(source=name, target=target, category=category, operation=operation, slot=slot,
                conditional_profile='MLA, no sequence-parallel MoE, encoder TP data',
                runtime_mapping_verified=False)


def checkpoint_shard_view(source, description, rank):
    import torch
    if type(rank) is not int or rank not in range(8):
        raise ValueError('invalid TP8 rank')
    if (not isinstance(source, torch.Tensor) or source.device.type != 'cpu'
            or source.is_neg() or source.is_conj() or not source.is_contiguous()
            or source.dtype not in (torch.bfloat16, torch.float32)):
        raise ValueError('requires original mixed-precision CPU source')
    operation = description['operation']
    if operation in ('replicate', 'merged_replicate'):
        return source
    if operation not in ('column', 'merged_column', 'row'):
        raise ValueError('unreviewed slicing operation')
    axis = 1 if operation == 'row' else 0
    if source.ndim != 2 or source.shape[axis] <= 0 or source.shape[axis] % 8:
        raise ValueError('unreviewed padding or indivisible shape')
    width = source.shape[axis] // 8
    return source.narrow(axis, rank * width, width)
