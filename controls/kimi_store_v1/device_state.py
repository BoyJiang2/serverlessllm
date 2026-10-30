"""Owned rank-state metadata/views on an explicit device; no transfers or kernels."""
from buffer_codec import CODEC, validate_buffer_encodings
from contract import DTYPE_BYTES
from state_devices import state_devices


def _layout(tensor, device, allowed):
    import torch
    expected = torch.device(device)
    if (expected.type not in ('cpu', 'cuda') or (expected.type == 'cuda' and expected.index is None)
            or not isinstance(tensor, torch.Tensor) or tensor.device != expected
            or tensor.layout != torch.strided or str(tensor.dtype) not in allowed
            or not tensor.is_contiguous() or tensor.is_conj() or tensor.is_neg()):
        raise ValueError('unsupported rank-state device/dtype/layout')
    size = tensor.numel() * allowed[str(tensor.dtype)]
    if tensor.storage_offset() != 0 or tensor.untyped_storage().nbytes() != size:
        raise ValueError('rank state does not own its full storage')
    stride, step = [], 1
    for dim in reversed(tensor.shape):
        stride.insert(0, step)
        step *= max(dim, 1)
    if list(tensor.stride()) != stride:
        raise ValueError('noncanonical rank-state stride')
    return dict(shape=list(tensor.shape), stride=stride, dtype=str(tensor.dtype),
                storage_offset=0, storage_bytes=size)


def owned_device_layout(tensor, device):
    return _layout(tensor, device, DTYPE_BYTES)


def encode_device_state(model, *, device, buffer_codecs=None, device_map=None):
    """Physical zero-copy views; CPU codec/entry semantics remain unchanged."""
    import torch
    codecs = {} if buffer_codecs is None else buffer_codecs
    parameters = list(model.named_parameters(remove_duplicate=False))
    buffers = list(model.named_buffers(remove_duplicate=False))
    if (not isinstance(codecs, dict) or not set(codecs) <= {n for n, _ in buffers}
            or set(codecs) & {n for n, _ in parameters}):
        raise ValueError('codec requires explicit registered buffer names')
    expected_devices = state_devices(device, device_map, (n for n, _ in parameters + buffers))
    named, layouts, encodings, owners = [], {}, {}, {}
    for name, original in parameters + buffers:
        expected_device = expected_devices[name]
        tensor = original
        if name in codecs:
            if codecs[name] != CODEC:
                raise ValueError('unknown buffer codec')
            before = _layout(original, expected_device, {'torch.complex64': 8})
            tensor = original.reshape(-1).view(torch.int32)
            encodings[name] = dict(codec=CODEC, original=before, encoded=owned_device_layout(tensor, expected_device))
        layout = owned_device_layout(tensor, expected_device)
        if name in layouts:
            raise ValueError('duplicate rank-state name')
        if layout['storage_bytes']:
            pointer = (str(original.device), original.untyped_storage().data_ptr())
            if pointer in owners:
                prior = owners[pointer]
                if (layout != layouts[prior] or (name in codecs) != (prior in codecs)
                        or (name in codecs and encodings[name] != encodings[prior])):
                    raise ValueError('unreviewed rank-state storage sharing')
            owners[pointer] = name
        named.append((name, tensor))
        layouts[name] = layout
    validate_buffer_encodings(encodings, layouts)
    return named, layouts, encodings
