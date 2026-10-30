"""Bounded export of a loaded, quiescent rank. Does not construct/load a model."""
from pathlib import Path
from auxiliary_manifest import validate_auxiliary_receipts
from checkpoint_contract import load_targets
from contract import REVISION, plan_rank
from device_state import encode_device_state
from native_index import publish_index
from native_payload import iter_host_byte_blocks
from rank_export import normalize_rank_cpu_auxiliary, normalize_rank_cuda_auxiliary
from runtime_inventory import collect_inventory, require_safe_byte_export
from state_bindings import collect_state_bindings
from stream_writer import CHUNK_BYTES, write_records


def _inputs(layouts):
    return {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
            for n, r in layouts.items()}


def write_device_state(directory, model, *, device, rank, checkpoint_contract=None,
                        auxiliary_receipts=(), buffer_codecs=None, chunk_bytes=CHUNK_BYTES):
    import torch
    if (type(rank) is not int or rank not in range(8)
            or type(chunk_bytes) is not int or not 4 <= chunk_bytes <= CHUNK_BYTES):
        raise ValueError('bounded host block size required')
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets, device=device)
    require_safe_byte_export(inventory)
    bindings = collect_state_bindings(model)
    named, layouts, encodings = encode_device_state(model, device=device, buffer_codecs=buffer_codecs)
    if not layouts:
        raise ValueError('empty rank state')
    receipts = validate_auxiliary_receipts(auxiliary_receipts, layouts, rank)
    aliases, seen = {}, {}
    for name, tensor in named:
        if layouts[name]['storage_bytes']:
            pointer = tensor.data_ptr()
            if pointer in seen:
                aliases[name] = seen[pointer]
            else:
                seen[pointer] = name
    entries = ((name, layouts[name]['storage_bytes'], iter_host_byte_blocks(
                   tensor, chunk_bytes // tensor.element_size() * tensor.element_size()), None)
               for name, tensor in named if name not in aliases)
    records = write_records(directory, entries)
    for name, root in aliases.items():
        records[name] = dict(records[root])
    return records, layouts, dict(alias_of=aliases, buffer_encodings=encodings,
        state_bindings=bindings, auxiliary_receipts=receipts,
        source_device=str(torch.device(device)), source_torch_version=str(torch.__version__),
        host_block_limit_bytes=chunk_bytes,
        element_aligned_host_blocks=True,
        written_payload_bytes=sum(r['bytes'] for n, r in records.items() if n not in aliases),
        checkpoint_name_coverage_verified=checkpoint_contract is not None,
        inventory_policy_verified=False, native_loader_verified=False, runtime_admitted=False)


def export_rank_device(directory, model, *, device, config_raw, checkpoint_contract, rank,
                        profile, vllm_version, torch_version, phase,
                        buffer_codecs=None, chunk_bytes=CHUNK_BYTES):
    """Normalize reviewed scratch, stream raw bits, verify and publish index last.

    Validation failure precedes normalization; later I/O/mutation failure makes
    the model and partial output unusable. Never retry into the same directory.
    """
    import torch
    if phase != 'checkpoint_loaded_before_postprocess':
        raise ValueError('source checkpoint phase required before mutation')
    if type(chunk_bytes) is not int or not 4 <= chunk_bytes <= CHUNK_BYTES:
        raise ValueError('bounded host block size required')
    if Path(directory).exists():
        raise FileExistsError('rank output already exists before normalization')
    named, layouts, _ = encode_device_state(model, device=device, buffer_codecs=buffer_codecs)
    plan_rank(config_raw, rank=rank, tensor_parallel_size=8, revision=REVISION,
        tensors=_inputs(layouts), vllm_version=vllm_version, torch_version=torch_version,
        chunk_bytes=chunk_bytes)
    del named, layouts  # Do not retain pre-normalization scratch during host copies.
    context = dict(checkpoint_contract=checkpoint_contract, buffer_codecs=buffer_codecs,
        rank=rank, profile=profile, config_raw=config_raw, vllm_version=vllm_version, phase=phase)
    if torch.device(device).type == 'cpu':
        receipts = normalize_rank_cpu_auxiliary(model, **context)
    else:
        receipts = normalize_rank_cuda_auxiliary(model, device=device, **context)
    records, layouts, info = write_device_state(directory, model, device=device, rank=rank,
        checkpoint_contract=checkpoint_contract, auxiliary_receipts=receipts,
        buffer_codecs=buffer_codecs, chunk_bytes=chunk_bytes)
    plan = plan_rank(config_raw, rank=rank, tensor_parallel_size=8, revision=REVISION,
        tensors=_inputs(layouts), vllm_version=vllm_version, torch_version=torch_version,
        chunk_bytes=chunk_bytes)
    result = publish_index(directory, plan, records, layouts, config_raw,
        aliases=info['alias_of'], buffer_encodings=info['buffer_encodings'],
        state_bindings=info['state_bindings'], auxiliary_receipts=receipts)
    return dict(archive=result, transfer=info)  # Caller persists this process/version evidence.
