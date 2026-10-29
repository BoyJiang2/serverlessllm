"""Bounded raw CPU fingerprints; compare restored state at the same phase only."""
import hashlib

from checkpoint_contract import load_targets
from cpu_tensor_io import iter_cpu_blocks
from runtime_inventory import collect_inventory, require_safe_byte_export
from stream_writer import CHUNK_BYTES
from buffer_codec import encode_cpu_state


def fingerprint_state(model, *, rank, phase, checkpoint_contract=None, chunk_bytes=CHUNK_BYTES, buffer_codecs=None):
    if type(rank) is not int or rank not in range(8):
        raise ValueError('expected one of eight ranks')
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets, phase=phase)
    require_safe_byte_export(inventory)
    named, layouts, encodings = encode_cpu_state(model, buffer_codecs)
    if not named:
        raise ValueError('empty state')
    rows = {}
    # The caller must quiesce writers; this is not an atomic model snapshot.
    for name, tensor in named:
        digest = hashlib.sha256()
        for block in iter_cpu_blocks(tensor, chunk_bytes):
            digest.update(block)
            del block
        rows[name] = dict(layout=layouts[name], sha256=digest.hexdigest())
    return dict(rank=rank, phase=phase, tensors=rows, inventory=inventory,
                buffer_encodings=encodings,
                checkpoint_name_coverage_verified=checkpoint_contract is not None,
                source_torch_version=inventory['source_torch_version'],
                native_loader_verified=False, runtime_admitted=False)


def compare_restored_state(reference, restored):
    if (reference['rank'] != restored['rank'] or reference['phase'] != restored['phase']
            or reference['source_torch_version'] != restored['source_torch_version']
            or reference.get('buffer_encodings', {}) != restored.get('buffer_encodings', {})
            or reference['checkpoint_name_coverage_verified'] != restored['checkpoint_name_coverage_verified']):
        raise ValueError('state context differs; pre/post transforms need a separate policy')
    a, b = reference['tensors'], restored['tensors']
    added, removed = sorted(b.keys() - a.keys()), sorted(a.keys() - b.keys())
    changed = [name for name in sorted(a.keys() & b.keys()) if a[name] != b[name]]
    inventory_equal = reference['inventory'] == restored['inventory']
    return dict(added=added, removed=removed, changed=changed, inventory_equal=inventory_equal,
                same_phase_cpu_bytes_equal=not (added or removed or changed) and inventory_equal,
                native_loader_verified=False, runtime_admitted=False)
