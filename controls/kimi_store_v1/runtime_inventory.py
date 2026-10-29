"""Metadata-only rank inventory; defaults to CPU, with explicit device matching."""
import re
from collections import OrderedDict

MAX_CONTAINER_NODES = 100_000
MAX_CONTAINER_DEPTH = 32


def is_moe_auxiliary(name):
    match = re.fullmatch(r'language_model\.model\.layers\.(\d+)\.mlp\.experts\.'
        r'(w13_weight_g_idx|w2_weight_g_idx|w13_g_idx_sort_indices|w2_g_idx_sort_indices)', name)
    return bool(match and int(match[1]) in range(1, 61))


def collect_inventory(model, checkpoint_targets=(), *, phase='before_postprocess', device='cpu'):
    import torch
    expected_device = torch.device(device)
    if (expected_device.type not in ('cpu', 'cuda')
            or (expected_device.type == 'cuda' and expected_device.index is None)):
        raise ValueError('explicit CPU or indexed CUDA device required')
    if phase not in ('before_postprocess', 'after_postprocess'):
        raise ValueError('unknown inventory phase')
    expected = set(checkpoint_targets)
    rows, seen = {}, {}
    named = [('parameter', n, t, True) for n, t in model.named_parameters(remove_duplicate=False)]
    for name, tensor in model.named_buffers(remove_duplicate=False):
        prefix, _, leaf = name.rpartition('.')
        module = model.get_submodule(prefix) if prefix else model
        named.append(('buffer', name, tensor, leaf not in module._non_persistent_buffers_set))
    opaque, cycles, visited = [], [], 0

    def walk(value, path, active, depth=0):
        nonlocal visited
        visited += 1
        if visited > MAX_CONTAINER_NODES or depth > MAX_CONTAINER_DEPTH:
            raise ValueError('container metadata audit budget exceeded')
        if isinstance(value, torch.Tensor):
            kind = 'unregistered_tensor' if depth == 0 else 'container_tensor'
            named.append((kind, path, value, False))
        elif type(value) in (dict, OrderedDict, list, tuple, set, frozenset):
            if id(value) in active:
                cycles.append(path)
                return
            active = active | {id(value)}
            if type(value) in (dict, OrderedDict):
                for i, (key, item) in enumerate(value.items()):
                    walk(key, path + '.keys[' + str(i) + ']', active, depth + 1)
                    walk(item, path + '.values[' + str(i) + ']', active, depth + 1)
            else:
                for i, item in enumerate(value):
                    walk(item, path + '[' + str(i) + ']', active, depth + 1)
        elif value is not None and type(value) not in (str, bytes, bool, int, float, complex):
            opaque.append(dict(path=path, type=type(value).__module__ + '.' + type(value).__qualname__))

    # Registered state is already enumerated; do not recurse into its registries.
    try:
        for prefix, module in model.named_modules(remove_duplicate=False):
            for leaf, value in vars(module).items():
                if leaf not in ('_parameters', '_buffers', '_modules'):
                    walk(value, (prefix + '.' if prefix else '') + leaf, set())
    finally:
        del walk  # Break the recursive closure cycle, including on budget failure.
    for kind, name, tensor, persistent in named:
        if name in rows or tensor.device != expected_device or tensor.layout != torch.strided:
            raise ValueError('duplicate name or unsupported device/layout in inventory')
        size = tensor.numel() * tensor.element_size()
        storage = tensor.untyped_storage()
        role = 'checkpoint' if name in expected else 'runtime_' + kind
        if is_moe_auxiliary(name):
            if name in expected:
                raise ValueError('auxiliary name cannot be declared checkpoint-backed')
            role = 'uninitialized_aux_candidate' if size else 'empty_auxiliary'
        row = dict(kind=kind, role=role, shape=list(tensor.shape), stride=list(tensor.stride()),
                   dtype=str(tensor.dtype), bytes=size, storage_bytes=storage.nbytes(),
                   storage_offset=tensor.storage_offset(), persistent=persistent,
                   contiguous=tensor.is_contiguous(), lazy_negative=tensor.is_neg(),
                   lazy_conjugate=tensor.is_conj(), requires_policy_review=role != 'checkpoint')
        if size:
            key = (storage.data_ptr(), storage.nbytes(), tensor.storage_offset(),
                   tuple(tensor.shape), tuple(tensor.stride()), tensor.dtype)
            if key in seen:
                row['alias_of'] = seen[key]
            else:
                seen[key] = name
        rows[name] = row
    return dict(phase=phase, tensors=rows, missing_checkpoint_targets=sorted(expected - rows.keys()),
                source_torch_version=str(torch.__version__), payload_read=False,
                builtin_container_audit_complete=True, uninspected_objects=opaque,
                container_cycles=cycles, container_nodes_visited=visited,
                opaque_container_audit_complete=False, runtime_admitted=False)


def require_safe_byte_export(inventory):
    """Reject known unsafe metadata before any bytes are written; not runtime R1."""
    for name, row in inventory['tensors'].items():
        if row['role'] == 'uninitialized_aux_candidate' or row['kind'] in ('unregistered_tensor', 'container_tensor'):
            raise ValueError('explicit initialization/recreation policy required: ' + name)
    if inventory['missing_checkpoint_targets']:
        raise ValueError('incomplete checkpoint-backed state')


def compare_inventory(before, after):
    if before['phase'] != 'before_postprocess' or after['phase'] != 'after_postprocess':
        raise ValueError('wrong phase order')
    a, b = before['tensors'], after['tensors']
    return dict(added=sorted(b.keys() - a.keys()), removed=sorted(a.keys() - b.keys()),
                changed={n: dict(before=a[n], after=b[n]) for n in sorted(a.keys() & b.keys()) if a[n] != b[n]},
                scope='metadata only, not weight values or postprocess correctness',
                payload_verified=False, runtime_admitted=False)
