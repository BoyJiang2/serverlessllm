"""Restore audited raw partitions into an existing, quiescent CPU model.

This is not native load_dict. Failure after copying begins invalidates the whole
destination; no rollback or concurrent source/model modification is supported.
"""
import hashlib
import json
from pathlib import Path

from checkpoint_contract import load_targets
from contract import DTYPE_BYTES, plan_rank
from cpu_tensor_io import iter_cpu_blocks, tensor_layout
from native_index import read_index
from runtime_inventory import collect_inventory, require_safe_byte_export
from stream_writer import CHUNK_BYTES
from auxiliary_manifest import validate_auxiliary_receipts
from buffer_codec import CODEC, VISION_CACHE, encode_cpu_state, validate_buffer_encodings
from state_bindings import collect_state_bindings, validate_state_bindings


def _restore_tensor(stream, tensor, chunk_bytes):
    digest = hashlib.sha256()
    for block in iter_cpu_blocks(tensor, chunk_bytes):
        used = 0
        while used < len(block):
            count = stream.readinto(block[used:])
            if not count:
                raise ValueError('truncated payload during restore; destination invalid')
            used += count
        digest.update(block)
        del block
    return digest.hexdigest()


def restore_cpu_state(directory, model, expected_sidecar_sha256, *, rank,
                      checkpoint_contract=None, chunk_bytes=CHUNK_BYTES,
                      destination_auxiliary_receipts=(), buffer_codecs=None,
                      create_lazy_visual_cache=False):
    if type(create_lazy_visual_cache) is not bool:
        raise ValueError('lazy buffer creation must be explicit')
    directory = Path(directory)
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest mismatch')
    sidecar = json.loads(raw)
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets)
    require_safe_byte_export(inventory)
    source_encodings = sidecar.get('buffer_encodings', {})
    bindings = validate_state_bindings(sidecar.get('state_bindings'), sidecar['records'], source_encodings)
    target_bindings = collect_state_bindings(model)
    missing = set(sidecar['records']) - target_bindings.keys()
    if (missing and (not create_lazy_visual_cache or missing != {VISION_CACHE}
            or bindings is None or VISION_CACHE not in source_encodings
            or bindings[VISION_CACHE] != dict(kind='buffer', persistent=False, requires_grad=False))):
        raise ValueError('unsupported missing target state')
    if bindings is not None and {n: r for n, r in bindings.items() if n not in missing} != target_bindings:
        raise ValueError('source/destination state binding roles differ')
    codecs = buffer_codecs
    if codecs is None and create_lazy_visual_cache:
        codecs = {n: CODEC for n in source_encodings}
    if missing:
        prefix, _, leaf = VISION_CACHE.rpartition('.')
        owner = model.get_submodule(prefix)
        if hasattr(owner, leaf):
            raise ValueError('lazy buffer name already occupied')
    existing_codecs = None if codecs is None else {n: c for n, c in codecs.items() if n not in missing}
    named, layouts, encodings = encode_cpu_state(model, existing_codecs)
    if missing:
        layouts[VISION_CACHE] = source_encodings[VISION_CACHE]['encoded']
        encodings[VISION_CACHE] = source_encodings[VISION_CACHE]
        if codecs is None or codecs.get(VISION_CACHE) != CODEC:
            raise ValueError('missing explicit lazy cache codec')
    if encodings != source_encodings:
        raise ValueError('source/destination buffer encodings differ')
    validate_buffer_encodings(encodings, layouts)
    receipts = validate_auxiliary_receipts(sidecar.get('auxiliary_receipts', []), layouts, rank)
    validate_auxiliary_receipts(destination_auxiliary_receipts, layouts, rank,
        phases=('checkpoint_loaded_before_postprocess', 'target_initialized_before_restore'))
    if not layouts or (type(chunk_bytes) is not int or chunk_bytes > CHUNK_BYTES
            or chunk_bytes < max(DTYPE_BYTES[r['dtype']] for r in layouts.values())):
        raise ValueError('empty state or invalid block bound')
    seen = {}
    for name, tensor in named:
        if layouts[name]['storage_bytes']:
            pointer = tensor.untyped_storage().data_ptr()
            if pointer in seen and layouts[name] != layouts[seen[pointer]]:
                raise ValueError('shared destination storage with different layouts')
            seen[pointer] = name
    inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
              for n, r in layouts.items()}
    expected = plan_rank((directory / 'config.json').read_bytes(), rank=rank,
                        tensor_parallel_size=8, revision=sidecar['source_revision'],
                        tensors=inputs, vllm_version=sidecar['vllm_version'],
                        torch_version=sidecar['torch_version'], chunk_bytes=sidecar['chunk_bytes'])
    for key in ('format', 'model', 'source_revision', 'config_sha256', 'rank',
                'tensor_parallel_size', 'state', 'vllm_version', 'torch_version',
                'chunk_bytes', 'planned_transfer_bytes', 'tensors'):
        if sidecar.get(key) != expected[key]:
            raise ValueError('source/destination contract mismatch: ' + key)
    aliases = sidecar.get('alias_of', {})

    def check_aliases():
        roots, pointers = {}, {}
        for name, tensor in named:
            if layouts[name]['storage_bytes']:
                root = aliases.get(name, name)
                pointer = tensor.untyped_storage().data_ptr()
                if ((root in roots and roots[root] != pointer)
                        or (pointer in pointers and pointers[pointer] != root)):
                    raise ValueError('destination alias topology differs')
                roots[root], pointers[pointer] = pointer, root

    check_aliases()
    # Verify every source payload before touching destination bytes.
    index = read_index(directory, expected_sidecar_sha256)
    if set(index) != set(layouts):
        raise ValueError('destination tensor coverage differs')
    for name, layout in layouts.items():
        row = sidecar['records'][name]
        if index[name] != [row['offset'], layout['storage_bytes'], layout['shape'],
                           layout['stride'], layout['dtype']] or row['bytes'] != layout['storage_bytes']:
            raise ValueError('index/destination layout mismatch: ' + name)
    new_buffer = None
    if missing:
        import torch
        root = aliases.get(VISION_CACHE, VISION_CACHE)
        sibling = next((n for n, _ in named if aliases.get(n, n) == root), None)
        if sibling is None:
            new_buffer = torch.empty(encodings[VISION_CACHE]['original']['shape'], dtype=torch.complex64, device='cpu')
        else:
            buffers = dict(model.named_buffers(remove_duplicate=False))
            new_buffer = buffers[sibling].detach().requires_grad_(False)
        view = new_buffer.reshape(-1).view(torch.int32)
        if tensor_layout(view) != layouts[VISION_CACHE]:
            raise ValueError('allocated lazy cache layout differs')
        named.append((VISION_CACHE, view))
        check_aliases()
    restored_bytes = 0
    for name, tensor in named:
        if name in aliases:
            continue
        row = sidecar['records'][name]
        with (directory / ('tensor.data_' + str(row['partition']))).open('rb', buffering=0) as stream:
            stream.seek(row['local_offset'])
            digest = _restore_tensor(stream, tensor, chunk_bytes)
        if digest != row['sha256']:
            raise ValueError('payload changed during restore; destination invalid: ' + name)
        restored_bytes += row['bytes']
    if new_buffer is not None:
        owner.register_buffer(leaf, new_buffer, persistent=False)
    return dict(rank=rank, phase='before_postprocess', restored_payload_bytes=restored_bytes,
                created_lazy_buffers=sorted(missing),
                auxiliary_receipts=receipts,
                buffer_encodings=encodings,
                state_bindings_verified=bindings is not None,
                chunk_bytes=chunk_bytes, alias_of=aliases, cpu_payload_restored=True,
                checkpoint_name_coverage_verified=checkpoint_contract is not None,
                inventory_policy_verified=False, native_loader_verified=False, runtime_admitted=False)
