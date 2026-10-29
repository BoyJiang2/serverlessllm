"""Install audited native return values; never calls the native loader or kernels.

Source must be quiescent and allocation records authenticated by the caller.
Any mutation failure invalidates the whole model; no rollback or publication.
"""
import hashlib
import json
from pathlib import Path

from auxiliary_manifest import validate_auxiliary_receipts
from buffer_codec import VISION_CACHE
from checkpoint_contract import load_targets
from native_index import read_index
from native_payload import verify_native_payload, verify_independent_payload
from native_spans import retain_native_allocation
from runtime_inventory import collect_inventory, require_safe_byte_export
from state_bindings import collect_state_bindings, validate_state_bindings
from stream_writer import CHUNK_BYTES


def install_native_state(directory, model, state, expected_sidecar_sha256, *, rank,
                         allocation, checkpoint_contract=None, destination_auxiliary_receipts=(),
                         chunk_bytes=CHUNK_BYTES, independent_records=None):
    import torch
    directory = Path(directory)
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest mismatch')
    sidecar = json.loads(raw)
    if (type(rank) is not int or rank not in range(8) or sidecar['rank'] != rank
            or sidecar['state'] != 'before_quantization_postprocessing'):
        raise ValueError('wrong rank or state phase')
    index = read_index(directory, expected_sidecar_sha256)
    encodings = sidecar.get('buffer_encodings', {})
    bindings = validate_state_bindings(sidecar.get('state_bindings'), index, encodings)
    if (bindings is None or allocation.get('alias_of') != sidecar.get('alias_of', {})
            or hasattr(model, '_kimi_native_allocation_tensors')
            or hasattr(model, '_kimi_independent_records_bound')):
        raise ValueError('complete roles, matching aliases and fresh target required')
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets, device=allocation['device'])
    require_safe_byte_export(inventory)
    target_bindings = collect_state_bindings(model)
    missing = set(bindings) - target_bindings.keys()
    if (set(target_bindings) - bindings.keys() or missing - {VISION_CACHE}
            or (missing and (VISION_CACHE not in encodings
                or bindings[VISION_CACHE] != dict(kind='buffer', persistent=False, requires_grad=False)))):
        raise ValueError('target state coverage differs')
    objects = dict(model.named_parameters(remove_duplicate=False))
    objects.update(model.named_buffers(remove_duplicate=False))
    layouts, object_roots = {}, {}
    for name, binding in target_bindings.items():
        row = inventory['tensors'][name]
        expected = (encodings[name]['original'] if name in encodings else
                    dict(shape=index[name][2], stride=index[name][3], dtype=index[name][4],
                         storage_offset=0, storage_bytes=index[name][1]))
        actual = {key: row[key] for key in expected}
        if binding != bindings[name] or actual != expected:
            raise ValueError('target binding/layout differs: ' + name)
        root = allocation['alias_of'].get(name, name)
        if id(objects[name]) in object_roots and object_roots[id(objects[name])] != root:
            raise ValueError('tied target object has distinct source records')
        object_roots[id(objects[name])] = root
        layouts[name] = (encodings[name]['encoded'] if name in encodings else expected)
    for name in missing:
        prefix, _, leaf = name.rpartition('.')
        if hasattr(model.get_submodule(prefix), leaf):
            raise ValueError('lazy buffer name already occupied')
    validate_auxiliary_receipts(destination_auxiliary_receipts, layouts, rank,
        phases=('checkpoint_loaded_before_postprocess', 'target_initialized_before_restore'))
    if independent_records is None:
        audit = verify_native_payload(index, state, sidecar['records'], chunk_bytes=chunk_bytes, **allocation)
    else:
        from record_allocations import IndependentRecordState
        if (not isinstance(independent_records, IndependentRecordState)
                or independent_records.tensors is not state
                or independent_records.device != str(torch.device(allocation['device']))
                or independent_records.plan['alias_of'] != allocation['alias_of']):
            raise ValueError('same-call independent state and aliases required')
        audit = verify_independent_payload(index, independent_records, sidecar['records'], chunk_bytes=chunk_bytes)
    decoded = dict(state)  # References only; no full payload or CPU state copy.
    for name, encoding in encodings.items():
        if state[name].numel() and state[name].data_ptr() % 8:
            raise ValueError('complex buffer address must be eight-byte aligned')
        decoded[name] = state[name].view(torch.complex64).reshape(encoding['original']['shape'])
    if independent_records is None:
        retain_native_allocation(model, state)  # Whole allocation owner must stay alive.
    else:
        model._kimi_independent_records_bound = True  # Consume this target without anchoring old payloads.
    for name, tensor in decoded.items():
        if bindings[name]['kind'] == 'parameter':
            objects[name].data = tensor
        else:
            prefix, _, leaf = name.rpartition('.')
            owner = model.get_submodule(prefix) if prefix else model
            owner.register_buffer(leaf, tensor.detach().requires_grad_(bindings[name]['requires_grad']),
                                  persistent=bindings[name]['persistent'])
    return dict(audit, rank=rank, state_bindings_verified=True, created_lazy_buffers=sorted(missing),
                checkpoint_name_coverage_verified=checkpoint_contract is not None,
                native_tensor_references_retained=independent_records is None,
                canonical_storage_owners_bound=independent_records is not None, inventory_policy_verified=False,
                postprocessing_performed=False)
