"""Metadata preflight for one native allocation; no I/O, mutation or readiness."""
import math

from contract import DTYPE_BYTES


def retain_native_allocation(model, state):
    """Hold every returned Tensor until model disposal, including the C++ owner.

    Call only after span/payload validation. This is ownership retention, not
    allocator verification; deleting/replacing the private anchor is unsupported.
    """
    import torch
    name = '_kimi_native_allocation_tensors'
    if (not isinstance(model, torch.nn.Module) or hasattr(model, name)
            or not isinstance(state, dict) or not state
            or any(not isinstance(t, torch.Tensor) for t in state.values())):
        raise ValueError('fresh model and complete native Tensor dictionary required')
    setattr(model, name, tuple(state.values()))


def validate_native_spans(index, state, *, device, base_address, allocation_bytes,
                          device_offsets, alias_of):
    """Allocation/offset inputs must come from the same actual native call.

    Returning this receipt does not establish allocator ownership or completion.
    CPU fixtures exercise the interval checks without invoking native CUDA code.
    """
    import torch
    expected_device = torch.device(device)
    if (expected_device.type not in ('cpu', 'cuda')
            or (expected_device.type == 'cuda' and expected_device.index is None)
            or type(base_address) is not int or base_address <= 0
            or type(allocation_bytes) is not int or allocation_bytes < 0):
        raise ValueError('explicit device and bounded native allocation required')
    if (not isinstance(index, dict) or not index or not isinstance(state, dict)
            or set(index) != set(state) or set(index) != set(device_offsets)
            or not isinstance(alias_of, dict) or not set(alias_of) <= set(index)):
        raise ValueError('native tensor coverage differs')
    spans = {}
    for name, row in index.items():
        if not isinstance(row, (list, tuple)) or len(row) != 5:
            raise ValueError('invalid native index row')
        source_offset, size, shape, stride, dtype = row
        if (type(source_offset) is not int or source_offset < 0
                or type(size) is not int or size < 0 or dtype not in DTYPE_BYTES
                or not isinstance(shape, list) or not isinstance(stride, list)
                or any(type(d) is not int or d < 0 for d in shape)):
            raise ValueError('invalid native layout')
        canonical, step = [], 1
        for dim in reversed(shape):
            canonical.insert(0, step)
            step *= max(dim, 1)
        if (any(type(s) is not int for s in stride) or stride != canonical
                or size != math.prod(shape) * DTYPE_BYTES[dtype]):
            raise ValueError('noncanonical native layout')
        offset = device_offsets[name]
        if (type(offset) is not int or offset < 0 or offset % DTYPE_BYTES[dtype]
                or (base_address + offset) % DTYPE_BYTES[dtype]
                or offset + size > allocation_bytes):
            raise ValueError('native interval outside allocation or misaligned')
        tensor = state[name]
        if (not isinstance(tensor, torch.Tensor) or tensor.device != expected_device
                or str(tensor.dtype) != dtype or list(tensor.shape) != shape
                or list(tensor.stride()) != stride or tensor.is_conj() or tensor.is_neg()):
            raise ValueError('native tensor metadata differs')
        if size and tensor.data_ptr() != base_address + offset:
            raise ValueError('native tensor address differs')
        spans[name] = (offset, offset + size)
    for name, root in alias_of.items():
        if (root not in index or root == name or root in alias_of
                or index[name] != index[root] or spans[name] != spans[root]):
            raise ValueError('native alias identity differs')
    intervals = sorted((start, end, name) for name, (start, end) in spans.items()
                       if end > start and name not in alias_of)
    previous_end = 0
    for start, end, name in intervals:
        if start < previous_end:
            raise ValueError('undeclared native overlap: ' + name)
        previous_end = end
    return dict(tensor_count=len(state), unique_payload_bytes=sum(b-a for a, b, _ in intervals),
                allocation_bytes=allocation_bytes, metadata_intervals_verified=True,
                payload_verified=False, allocation_ownership_verified=False,
                native_loader_verified=False, runtime_admitted=False)
