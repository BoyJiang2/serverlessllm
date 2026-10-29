"""Pre-postprocess expert mapping audit rules, not a vLLM model loader."""
import re

PROJECTIONS = {'gate': ('w13', 'w1'), 'up': ('w13', 'w3'), 'down': ('w2', 'w2')}


def parse_expert_key(name):
    match = re.fullmatch(r'language_model\.model\.layers\.(\d+)\.mlp\.experts\.(\d+)\.'
                         r'(gate|up|down)_proj\.(weight_packed|weight_scale|weight_shape)', name)
    if not match:
        raise ValueError('not an official expert tensor key')
    layer, expert = int(match[1]), int(match[2])
    if layer not in range(1, 61) or expert not in range(384):
        raise ValueError('expert or layer outside frozen model')
    fused, shard = PROJECTIONS[match[3]]
    suffix = match[4].removeprefix('weight_')
    return dict(layer=layer, expert=expert, projection=match[3], shard=shard,
                target=f'language_model.model.layers.{layer}.mlp.experts.{fused}_weight_{suffix}')


def checkpoint_shard_view(source, projection, kind, rank, backend):
    """Borrowed CPU audit view; no unpack, requantize, clone or tensor allocation."""
    import torch
    if (type(rank) is not int or rank not in range(8) or projection not in PROJECTIONS
            or backend not in ('Marlin', 'Flashinfer') or kind not in ('packed', 'scale', 'shape')):
        raise ValueError('unsupported mapping condition')
    if (not isinstance(source, torch.Tensor) or source.device.type != 'cpu'
            or source.is_neg() or source.is_conj()):
        raise ValueError('requires untransformed CPU source')
    expected_dtype = torch.bfloat16 if kind == 'scale' else torch.int32
    if source.dtype != expected_dtype or not source.is_contiguous():
        raise ValueError('source dtype/layout differs from official checkpoint')
    if kind == 'shape':
        if list(source.shape) != [2]:
            raise ValueError('weight_shape must have two entries')
        return source
    axis = 1 if projection == 'down' else 0
    if source.ndim != 2 or source.shape[axis] <= 0 or source.shape[axis] % 8:
        raise ValueError('cannot evenly shard checkpoint tensor')
    width = source.shape[axis] // 8
    view = source.narrow(axis, rank * width, width)
    return view.t() if backend == 'Marlin' else view
