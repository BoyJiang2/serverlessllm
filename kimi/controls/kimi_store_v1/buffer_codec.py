"""Explicit registered complex64 buffer codec: views only, never numeric casts."""
import math
import json

CODEC = 'complex64-int32-bits-v1'
VISION_CACHE = 'vision_tower.encoder.rope_2d.freqs_cis'


def _original_layout(tensor):
    import torch
    if (tensor.device.type != 'cpu' or tensor.dtype != torch.complex64
            or not tensor.is_contiguous() or tensor.is_conj() or tensor.is_neg()
            or tensor.storage_offset() != 0
            or tensor.untyped_storage().nbytes() != tensor.numel() * 8):
        raise ValueError('codec requires full owned untransformed CPU complex64 buffer')
    stride, step = [], 1
    for dim in reversed(tensor.shape):
        stride.insert(0, step)
        step *= max(dim, 1)
    if list(tensor.stride()) != stride:
        raise ValueError('codec requires canonical strides')
    return dict(shape=list(tensor.shape), stride=stride, dtype='torch.complex64',
                storage_offset=0, storage_bytes=tensor.numel() * 8)


def validate_buffer_encodings(encodings, layouts, aliases=None):
    if not isinstance(encodings, dict):
        raise ValueError('buffer encoding manifest must be a dictionary')
    if VISION_CACHE in layouts and VISION_CACHE not in encodings:
        raise ValueError('visual complex cache requires its encoding manifest')
    for name, record in encodings.items():
        original = record['original']
        shape = original['shape']
        if (name not in layouts or not isinstance(shape, list)
                or any(type(d) is not int or d < 0 for d in shape)):
            raise ValueError('invalid buffer encoding coverage/shape')
        stride, step = [], 1
        for dim in reversed(shape):
            stride.insert(0, step)
            step *= max(dim, 1)
        size = math.prod(shape) * 8
        expected_original = dict(shape=shape, stride=stride, dtype='torch.complex64', storage_offset=0, storage_bytes=size)
        encoded = dict(shape=[size // 4], stride=[1], dtype='torch.int32', storage_offset=0, storage_bytes=size)
        expected = dict(codec=CODEC, original=expected_original, encoded=encoded)
        if json.dumps(record, sort_keys=True) != json.dumps(expected, sort_keys=True) or layouts[name] != encoded:
            raise ValueError('changed buffer encoding layout/recipe')
    if aliases is not None and not isinstance(aliases, dict):
        raise ValueError('invalid alias map')
    for name, canonical in ({} if aliases is None else aliases).items():
        if ((name in encodings) != (canonical in encodings)
                or (name in encodings and encodings[name] != encodings[canonical])):
            raise ValueError('alias buffer encoding recipes differ')
    return encodings


def encode_cpu_state(model, buffer_codecs=None):
    import torch
    from cpu_tensor_io import tensor_layout
    codecs = {} if buffer_codecs is None else buffer_codecs
    if not isinstance(codecs, dict):
        raise ValueError('explicit buffer codec dictionary required')
    parameters = list(model.named_parameters(remove_duplicate=False))
    buffers = list(model.named_buffers(remove_duplicate=False))
    if not set(codecs) <= {n for n, _ in buffers} or set(codecs) & {n for n, _ in parameters}:
        raise ValueError('codec can only target registered buffers')
    named, layouts, encodings, owners = [], {}, {}, {}
    for name, original in parameters + buffers:
        tensor = original
        if name in codecs:
            if codecs[name] != CODEC:
                raise ValueError('unknown buffer codec')
            source = _original_layout(original)
            tensor = original.reshape(-1).view(torch.int32)
            encodings[name] = dict(codec=CODEC, original=source, encoded=tensor_layout(tensor))
        layout = tensor_layout(tensor)
        if name in layouts:
            raise ValueError('duplicate state name')
        if layout['storage_bytes']:
            pointer = original.untyped_storage().data_ptr()
            if pointer in owners:
                prior = owners[pointer]
                if ((name in codecs) != (prior in codecs)
                        or (name in codecs and encodings[name] != encodings[prior])):
                    raise ValueError('unreviewed cross-layout buffer storage sharing')
            owners[pointer] = name
        named.append((name, tensor))
        layouts[name] = layout
    validate_buffer_encodings(encodings, layouts)
    return named, layouts, encodings


def decode_cpu_buffers(state, encodings):
    import torch
    from cpu_tensor_io import tensor_layout
    layouts = {name: tensor_layout(tensor) for name, tensor in state.items()}
    aliases, seen = {}, {}
    for name, tensor in state.items():
        if layouts[name]['storage_bytes']:
            pointer = tensor.untyped_storage().data_ptr()
            if pointer in seen:
                aliases[name] = seen[pointer]
            else:
                seen[pointer] = name
    validate_buffer_encodings(encodings, layouts, aliases)
    result = dict(state)
    for name, record in encodings.items():
        result[name] = state[name].view(torch.complex64).reshape(record['original']['shape'])
    return result
