"""Restore audited raw partitions into an existing, quiescent CPU model.

This is not native load_dict. Failure after copying begins invalidates the whole
destination; no rollback or concurrent source/model modification is supported.
"""
import hashlib
import json
from pathlib import Path

from checkpoint_contract import load_targets
from contract import DTYPE_BYTES, plan_rank
from cpu_tensor_io import iter_cpu_blocks
from native_index import read_index
from runtime_inventory import collect_inventory, require_safe_byte_export
from stream_writer import CHUNK_BYTES
from auxiliary_manifest import validate_auxiliary_receipts
from buffer_codec import encode_cpu_state, validate_buffer_encodings


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
                      destination_auxiliary_receipts=(), buffer_codecs=None):
    directory = Path(directory)
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest mismatch')
    sidecar = json.loads(raw)
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets)
    require_safe_byte_export(inventory)
    named, layouts, encodings = encode_cpu_state(model, buffer_codecs)
    if encodings != sidecar.get('buffer_encodings', {}):
        raise ValueError('source/destination buffer encodings differ')
    validate_buffer_encodings(encodings, layouts)
    receipts = validate_auxiliary_receipts(sidecar.get('auxiliary_receipts', []), layouts, rank)
    validate_auxiliary_receipts(destination_auxiliary_receipts, layouts, rank)
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
    aliases = {n: r['alias_of'] for n, r in inventory['tensors'].items() if 'alias_of' in r}
    if aliases != sidecar.get('alias_of', {}):
        raise ValueError('destination alias topology differs')
    # Verify every source payload before touching destination bytes.
    index = read_index(directory, expected_sidecar_sha256)
    if set(index) != set(layouts):
        raise ValueError('destination tensor coverage differs')
    for name, layout in layouts.items():
        row = sidecar['records'][name]
        if index[name] != [row['offset'], layout['storage_bytes'], layout['shape'],
                           layout['stride'], layout['dtype']] or row['bytes'] != layout['storage_bytes']:
            raise ValueError('index/destination layout mismatch: ' + name)
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
    return dict(rank=rank, phase='before_postprocess', restored_payload_bytes=restored_bytes,
                auxiliary_receipts=receipts,
                buffer_encodings=encodings,
                chunk_bytes=chunk_bytes, alias_of=aliases, cpu_payload_restored=True,
                checkpoint_name_coverage_verified=checkpoint_contract is not None,
                inventory_policy_verified=False, native_loader_verified=False, runtime_admitted=False)
