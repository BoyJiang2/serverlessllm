"""Bounded raw-bit audit of already returned tensors; not a native loader."""
import ctypes
import hashlib
import re

from contract import DTYPE_BYTES
from native_spans import validate_native_spans
from stream_writer import CHUNK_BYTES


def iter_host_byte_blocks(tensor, chunk_bytes=CHUNK_BYTES):
    """One synchronous host copy at a time; consumers must not retain blocks.

    Bound is per payload copy, not process RSS or CUDA allocation. No numerical
    conversion: dtype view is uint8, then copy preserves the original bytes.
    """
    import torch
    if (not isinstance(tensor, torch.Tensor) or str(tensor.dtype) not in DTYPE_BYTES
            or tensor.device.type not in ('cpu', 'cuda') or not tensor.is_contiguous()
            or tensor.is_conj() or tensor.is_neg()
            or type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES):
        raise ValueError('unsupported tensor or host block bound')
    raw = tensor.detach().reshape(-1).view(torch.uint8)
    for offset in range(0, raw.numel(), chunk_bytes):
        count = min(chunk_bytes, raw.numel() - offset)
        host = raw.narrow(0, offset, count).to(device='cpu', non_blocking=False, copy=True)
        buffer = (ctypes.c_ubyte * count).from_address(host.data_ptr())
        buffer._tensor_owner = host
        block = memoryview(buffer).cast('B')
        yield block
        del block, buffer, host


def verify_native_payload(index, state, records, *, chunk_bytes=CHUNK_BYTES, **allocation):
    """Caller must authenticate index/records and quiesce the source beforehand.

    All tensor metadata and record digests are checked before the first copy.
    This audit belongs outside performance timing. It performs no installation.
    """
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES:
        raise ValueError('invalid host block bound')
    receipt = validate_native_spans(index, state, **allocation)
    if not isinstance(records, dict) or set(records) != set(index):
        raise ValueError('complete source records required')
    for name, row in records.items():
        if (type(row.get('bytes')) is not int or row['bytes'] != index[name][1]
                or not isinstance(row.get('sha256'), str)
                or re.fullmatch('[0-9a-f]{64}', row['sha256']) is None):
            raise ValueError('invalid source digest or byte count')
    aliases = allocation['alias_of']
    for name, root in aliases.items():
        if records[name]['sha256'] != records[root]['sha256']:
            raise ValueError('alias payload digests differ')
    digests, transferred = {}, 0
    for name, tensor in state.items():
        if name in aliases:
            continue
        digest = hashlib.sha256()
        for block in iter_host_byte_blocks(tensor, chunk_bytes):
            digest.update(block)
            transferred += len(block)
            del block
        digests[name] = digest.hexdigest()
        if digests[name] != records[name]['sha256']:
            raise ValueError('loaded payload digest differs: ' + name)
    for name, root in aliases.items():
        digests[name] = digests[root]
    return dict(receipt, payload_verified=True, payload_device=str(next(iter(state.values())).device),
                verified_digests=digests, host_payload_copy_bytes=transferred,
                host_block_limit_bytes=chunk_bytes)


def verify_independent_payload(index, result, records, *, chunk_bytes=CHUNK_BYTES):
    from record_allocations import validate_independent_state
    plan = validate_independent_state(result, index)
    if (type(chunk_bytes) is not int or not 1 <= chunk_bytes <= CHUNK_BYTES
            or not isinstance(records, dict) or set(records) != set(index)):
        raise ValueError('complete bounded independent payload audit required')
    for name, row in records.items():
        if (type(row.get('bytes')) is not int or row['bytes'] != index[name][1]
                or not isinstance(row.get('sha256'), str)
                or re.fullmatch('[0-9a-f]{64}', row['sha256']) is None):
            raise ValueError('invalid independent source record')
    for name, root in plan['alias_of'].items():
        if records[name]['sha256'] != records[root]['sha256']:
            raise ValueError('independent alias digests differ')
    digests, transferred = {}, 0
    for record in plan['records']:
        root = record['root']
        digest = hashlib.sha256()
        for block in iter_host_byte_blocks(result.tensors[root], chunk_bytes):
            digest.update(block)
            transferred += len(block)
            del block
        if digest.hexdigest() != records[root]['sha256']:
            raise ValueError('independent payload digest differs: ' + root)
        for name in record['names']:
            digests[name] = digest.hexdigest()
    return dict(payload_verified=True, payload_device=result.device, verified_digests=digests,
                host_payload_copy_bytes=transferred, host_block_limit_bytes=chunk_bytes,
                metadata_intervals_verified=True, allocation_ownership_verified=False,
                native_loader_verified=False, runtime_admitted=False)
