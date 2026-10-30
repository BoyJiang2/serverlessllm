"""Per-record native allocation plan and canonical wrappers; no RPC/allocator."""
import json
import math
import hashlib

from contract import DTYPE_BYTES
from native_call import _capsule_address


def _index_digest(index):
    return hashlib.sha256(json.dumps(index, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class IndependentRecordState:
    """Same-call wrapper evidence, not proof of native ABI or CUDA ownership."""
    def __init__(self, tensors, index, plan, addresses, device):
        self.tensors = tensors
        self.index_digest = _index_digest(index)
        self.plan = json.loads(json.dumps(plan))
        self.addresses = dict(addresses)
        self.device = str(device)


def plan_independent_records(index, alias_of):
    """Consume an authenticated native index; aliases share a single allocation.

    Logical file offsets remain unchanged. Each nonempty canonical record gets
    its own IPC handle and copies to offset zero within that allocation.
    """
    if not isinstance(index, dict) or not index or not isinstance(alias_of, dict):
        raise ValueError('complete native index and alias map required')
    if not set(alias_of) <= set(index):
        raise ValueError('unknown alias')
    for name, row in index.items():
        if not isinstance(name, str) or not isinstance(row, (list, tuple)) or len(row) != 5:
            raise ValueError('invalid record identity/layout')
        offset, size, shape, stride, dtype = row
        if (type(offset) is not int or offset < 0 or offset % 8
                or type(size) is not int or size < 0 or dtype not in DTYPE_BYTES
                or not isinstance(shape, list) or any(type(d) is not int or d < 0 for d in shape)):
            raise ValueError('invalid record range/dtype/shape')
        canonical, step = [], 1
        for dim in reversed(shape):
            canonical.insert(0, step)
            step *= max(dim, 1)
        if (not isinstance(stride, list) or any(type(s) is not int for s in stride)
                or stride != canonical or size != math.prod(shape) * DTYPE_BYTES[dtype]):
            raise ValueError('noncanonical record layout')
    for name, root in alias_of.items():
        if (not isinstance(root, str) or root not in index or root == name or root in alias_of
                or index[name] != index[root]):
            raise ValueError('invalid alias root or layout')
    records, chunks, source_spans = [], [], []
    for root, row in index.items():
        if root in alias_of:
            continue
        offset, size, _, _, _ = row
        names = [root] + [n for n, parent in alias_of.items() if parent == root]
        handle = len(chunks) if size else None
        records.append(dict(root=root, names=names, bytes=size, handle_idx=handle))
        if size:
            chunks.append([offset, size, 0, handle])
            source_spans.append((offset, offset + size))
    source_spans.sort()
    if any(a[1] > b[0] for a, b in zip(source_spans, source_spans[1:])):
        raise ValueError('undeclared source record overlap')
    return dict(layout='canonical-record-allocations-v1', records=records, copy_chunks=chunks,
                alias_of=dict(alias_of), allocation_count=len(chunks),
                payload_bytes=sum(r['bytes'] for r in records),
                largest_allocation_bytes=max(r['bytes'] for r in records),
                native_loader_verified=False, allocation_ownership_verified=False, runtime_admitted=False)


def restore_independent_records(index, plan, pointers, *, device_id, expected_device,
                                 transfer_confirmed, restore_tensors, make_empty):
    """Call pinned RestoreTensors once per allocation, canonical name only.

    The admitted caller must supply same-call pointers and confirm completed DMA
    before this operation. This helper neither proves those facts nor frees raw
    pointers on failure. Failed calls invalidate the worker/context.
    """
    import torch
    from device_state import owned_device_layout
    expected = plan_independent_records(index, plan.get('alias_of'))
    if json.dumps(plan, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError('changed independent allocation plan')
    if (type(device_id) is not int or device_id not in range(8) or transfer_confirmed is not True
            or not callable(restore_tensors) or not callable(make_empty)):
        raise ValueError('explicit confirmed transfer and restore callbacks required')
    device = torch.device(expected_device)
    if (device.type not in ('cpu', 'cuda')
            or (device.type == 'cuda' and device.index != device_id)):
        raise ValueError('restore device differs from admitted rank')
    roots = {r['root'] for r in plan['records'] if r['bytes']}
    if not isinstance(pointers, dict) or set(pointers) != roots:
        raise ValueError('same-call pointer coverage differs')
    addresses, spans = {}, []
    for record in plan['records']:
        root, size = record['root'], record['bytes']
        if not size:
            continue
        address = _capsule_address(pointers[root])
        if address % DTYPE_BYTES[index[root][4]]:
            raise ValueError('misaligned record allocation')
        addresses[root] = address
        spans.append((address, address + size))
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('independent allocations overlap')
    state = {}
    for record in plan['records']:
        root = record['root']
        _, size, shape, stride, dtype = index[root]
        if size:
            # One from_blob owner for this base; aliases below share its StorageImpl.
            returned = restore_tensors({root: (shape, stride, dtype)},
                {device_id: pointers[root]}, {device_id: {root: 0}})
            if not isinstance(returned, dict) or set(returned) != {root}:
                raise ValueError('canonical restore result differs')
            tensor = returned[root]
        else:
            tensor = make_empty(shape, dtype, device)
        layout = owned_device_layout(tensor, device)
        if layout != dict(shape=shape, stride=stride, dtype=dtype, storage_offset=0, storage_bytes=size):
            raise ValueError('restored record metadata differs')
        if size and tensor.data_ptr() != addresses[root]:
            raise ValueError('restored record address differs')
        for name in record['names']:
            state[name] = tensor
    return IndependentRecordState(state, index, plan, addresses, device)


def validate_independent_state(result, index):
    """Recheck all metadata/addresses/alias identity before reading any payload."""
    from device_state import owned_device_layout
    if not isinstance(result, IndependentRecordState) or result.index_digest != _index_digest(index):
        raise ValueError('independent result source index differs')
    expected = plan_independent_records(index, result.plan['alias_of'])
    if json.dumps(result.plan, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError('independent result plan changed')
    if set(result.tensors) != set(index):
        raise ValueError('independent result tensor coverage differs')
    if set(result.addresses) != {r['root'] for r in result.plan['records'] if r['bytes']}:
        raise ValueError('independent result pointer coverage differs')
    spans = []
    for record in result.plan['records']:
        root, size = record['root'], record['bytes']
        tensor = result.tensors[root]
        row = index[root]
        layout = owned_device_layout(tensor, result.device)
        if layout != dict(shape=row[2], stride=row[3], dtype=row[4], storage_offset=0, storage_bytes=size):
            raise ValueError('independent result layout differs')
        if size:
            if tensor.data_ptr() != result.addresses.get(root):
                raise ValueError('independent result address differs')
            spans.append((tensor.data_ptr(), tensor.data_ptr() + size))
        if any(result.tensors[n] is not tensor for n in record['names']):
            raise ValueError('aliases must share their canonical owning Tensor')
    spans.sort()
    if any(a[1] > b[0] for a, b in zip(spans, spans[1:])):
        raise ValueError('independent result allocations overlap')
    return result.plan
