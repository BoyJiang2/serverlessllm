"""CPU-only post-load slot installer; never constructs models or runs kernels.

All declared state is checked before mutation. A failure during mutation makes
the entire target invalid; callers must not infer or publish it. No rollback.
"""
import hashlib
import json
from pathlib import Path

from auxiliary_manifest import validate_auxiliary_receipts
from buffer_codec import CODEC, VISION_CACHE, decode_cpu_buffers, encode_cpu_state
from checkpoint_contract import load_targets
from cpu_tensor_io import iter_cpu_blocks, tensor_layout
from native_index import read_index
from runtime_inventory import collect_inventory, require_safe_byte_export
from state_bindings import collect_state_bindings, validate_state_bindings


def install_cpu_loaded_state(directory, model, state, expected_sidecar_sha256, *, rank,
                             checkpoint_contract=None, destination_auxiliary_receipts=()):
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
    if bindings is None or set(state) != set(index):
        raise ValueError('complete loaded state and binding manifest required')
    layouts = {n: tensor_layout(t) for n, t in state.items()}
    owners = {}
    aliases = sidecar.get('alias_of', {})
    for name, tensor in state.items():
        row, layout = sidecar['records'][name], layouts[name]
        if index[name] != [row['offset'], layout['storage_bytes'], layout['shape'], layout['stride'], layout['dtype']]:
            raise ValueError('loaded layout differs: ' + name)
        digest = hashlib.sha256()
        for block in iter_cpu_blocks(tensor):
            digest.update(block)
            del block
        if digest.hexdigest() != row['sha256']:
            raise ValueError('loaded CPU payload differs: ' + name)
        if row['bytes']:
            root = aliases.get(name, name)
            pointer = tensor.untyped_storage().data_ptr()
            if pointer != state[root].untyped_storage().data_ptr():
                raise ValueError('loaded alias differs')
            if pointer in owners and owners[pointer] != root:
                raise ValueError('undeclared loaded storage sharing')
            owners[pointer] = root
    decoded = decode_cpu_buffers(state, encodings)
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    require_safe_byte_export(collect_inventory(model, targets))
    target_bindings = collect_state_bindings(model)
    missing = set(bindings) - target_bindings.keys()
    if (set(target_bindings) - bindings.keys() or missing - {VISION_CACHE}
            or (missing and (VISION_CACHE not in encodings
                or bindings[VISION_CACHE] != dict(kind='buffer', persistent=False, requires_grad=False)))):
        raise ValueError('target state coverage differs')
    codecs = {n: CODEC for n in encodings if n in target_bindings}
    target_named, target_layouts, target_encodings = encode_cpu_state(model, codecs)
    if target_encodings != {n: record for n, record in encodings.items() if n in target_bindings}:
        raise ValueError('target original buffer encoding differs')
    validate_auxiliary_receipts(destination_auxiliary_receipts, target_layouts, rank)
    target_objects = dict(model.named_parameters(remove_duplicate=False))
    target_objects.update(model.named_buffers(remove_duplicate=False))
    seen_objects = {}
    for name, binding in target_bindings.items():
        if binding != bindings[name] or target_layouts[name] != layouts[name]:
            raise ValueError('target binding/layout differs: ' + name)
        tensor = target_objects[name]
        root = aliases.get(name, name)
        if id(tensor) in seen_objects and seen_objects[id(tensor)] != root:
            raise ValueError('tied target object has distinct source records')
        seen_objects[id(tensor)] = root
    for name in missing:
        prefix, _, leaf = name.rpartition('.')
        owner = model.get_submodule(prefix)
        if hasattr(owner, leaf):
            raise ValueError('lazy buffer name already occupied')
    del target_named  # Do not keep pre-install complex storage views alive.
    for name, tensor in decoded.items():
        if bindings[name]['kind'] == 'parameter':
            target_objects[name].data = tensor  # Keep native Parameter subclass/loader attributes.
        else:
            prefix, _, leaf = name.rpartition('.')
            owner = model.get_submodule(prefix) if prefix else model
            buffer = tensor.detach().requires_grad_(bindings[name]['requires_grad'])
            owner.register_buffer(leaf, buffer, persistent=bindings[name]['persistent'])
    return dict(rank=rank, loaded_cpu_payload_verified=True, state_bindings_verified=True,
                created_lazy_buffers=sorted(missing), checkpoint_name_coverage_verified=checkpoint_contract is not None,
                postprocessing_performed=False, native_loader_verified=False, runtime_admitted=False)
