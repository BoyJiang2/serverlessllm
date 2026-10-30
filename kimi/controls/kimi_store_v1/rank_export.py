"""Compose CPU rank preparation and raw export; never construct a model here.

Only validation failures are guaranteed to precede mutation. Mutation/I/O failure
invalidates the model/export directory; callers must keep failed evidence.
Actual full-model execution is outside the present CPU microtest authorization.
"""
import re

from auxiliary_manifest import validate_auxiliary_receipts
from checkpoint_contract import load_targets
from contract import REVISION, plan_rank
from cpu_tensor_io import write_cpu_state
from marlin_aux import NAMES, _prepare_cpu_auxiliary, _prepare_cuda_auxiliary
from native_index import publish_index
from runtime_inventory import collect_inventory, require_safe_byte_export
from stream_writer import CHUNK_BYTES
from buffer_codec import encode_cpu_state
from state_bindings import collect_state_bindings


def normalize_rank_cpu_auxiliary(model, *, checkpoint_contract, buffer_codecs=None, **context):
    return _normalize_rank_auxiliary(model, checkpoint_contract=checkpoint_contract,
        buffer_codecs=buffer_codecs, device='cpu', describe=encode_cpu_state,
        prepare=_prepare_cpu_auxiliary, context=context)


def normalize_rank_cuda_auxiliary(model, *, device, checkpoint_contract, buffer_codecs=None, **context):
    import torch
    from device_state import encode_device_state
    expected = torch.device(device)
    if expected.type != 'cuda' or expected.index is None:
        raise ValueError('explicit indexed CUDA device required')
    return _normalize_rank_auxiliary(model, checkpoint_contract=checkpoint_contract,
        buffer_codecs=buffer_codecs, device=expected,
        describe=lambda model, codecs: encode_device_state(model, device=expected, buffer_codecs=codecs),
        prepare=lambda layer, **kwargs: _prepare_cuda_auxiliary(layer, device=expected, **kwargs),
        context=context)


def normalize_rank_mixed_auxiliary(model, *, device, checkpoint_contract, buffer_codecs=None, **context):
    from device_state import encode_device_state
    from mixed_device_init import mixed_state_devices
    if context.get('phase') != 'target_initialized_before_restore':
        raise ValueError('mixed preparation only accepts target-before-restore phase')
    devices = mixed_state_devices(model, device)
    return _normalize_rank_auxiliary(model, checkpoint_contract=checkpoint_contract,
        buffer_codecs=buffer_codecs, device='cpu', device_map=devices,
        describe=lambda model, codecs: encode_device_state(model, device='cpu',
            device_map=devices, buffer_codecs=codecs),
        prepare=_prepare_cpu_auxiliary, context=context)


def _normalize_rank_auxiliary(model, *, checkpoint_contract, buffer_codecs, device, describe, prepare,
                              context, device_map=None):
    targets = load_targets(checkpoint_contract)
    inventory = collect_inventory(model, targets, device=device, device_map=device_map)
    collect_state_bindings(model)
    names = {f'language_model.model.layers.{i}.mlp.experts' for i in range(1, 61)}
    modules = dict(model.named_modules(remove_duplicate=False))
    actual = {n for n in modules if re.fullmatch(r'language_model\.model\.layers\.\d+\.mlp\.experts', n)}
    if actual != names or len({id(modules[n]) for n in names}) != 60:
        raise ValueError('expected sixty distinct routed expert modules')
    auxiliary = {n + '.' + leaf for n in names for leaf in NAMES}
    if not auxiliary <= inventory['tensors'].keys():
        raise ValueError('incomplete rank auxiliary state')
    other = dict(inventory, tensors={n: r for n, r in inventory['tensors'].items() if n not in auxiliary})
    require_safe_byte_export(other)
    named, layouts, _ = describe(model, buffer_codecs)
    for layout in layouts.values():
        stride, step = [], 1
        for dim in reversed(layout['shape']):
            stride.insert(0, step)
            step *= max(dim, 1)
        if layout['stride'] != stride:
            raise ValueError('noncanonical stride requires explicit view support')
    seen = {}
    for name, tensor in named:
        if layouts[name]['storage_bytes']:
            pointer = (str(tensor.device), tensor.untyped_storage().data_ptr())
            if pointer in seen:
                prior = seen[pointer]
                if name in auxiliary or prior in auxiliary or layouts[name] != layouts[prior]:
                    raise ValueError('unreviewed rank storage sharing')
            seen[pointer] = name
    prepared, receipts = [], []
    for name in sorted(names, key=lambda n: int(n.split('.')[3])):
        replacements, receipt = prepare(modules[name], name=name, **context)
        prepared.append((modules[name], replacements))
        receipts.append(receipt)
        for leaf, layout in receipt['after'].items():
            layouts[name + '.' + leaf] = layout
    validate_auxiliary_receipts(receipts, layouts, context['rank'], phases=(context['phase'],))
    # All sixty source checks and global metadata checks finished before mutation.
    for layer, replacements in prepared:
        for leaf, tensor in replacements.items():
            setattr(layer, leaf, tensor)
    return receipts


def export_rank_cpu(directory, model, *, config_raw, checkpoint_contract, rank,
                    profile, vllm_version, torch_version, phase, chunk_bytes=CHUNK_BYTES, buffer_codecs=None):
    if phase != 'checkpoint_loaded_before_postprocess':
        raise ValueError('export requires source-after-load phase before any mutation')
    # Validate byte bounds and fixed ABI/config even before changing scratch bindings.
    named, _, _ = encode_cpu_state(model, buffer_codecs)
    source_inputs = {n: dict(shape=list(t.shape), dtype=str(t.dtype),
                            bytes=t.numel() * t.element_size(), contiguous=t.is_contiguous()) for n, t in named}
    del named
    plan_rank(config_raw, rank=rank, tensor_parallel_size=8, revision=REVISION,
              tensors=source_inputs,
              vllm_version=vllm_version, torch_version=torch_version, chunk_bytes=chunk_bytes)
    del source_inputs
    if chunk_bytes > CHUNK_BYTES:
        raise ValueError('CPU adapter block bound exceeded')
    receipts = normalize_rank_cpu_auxiliary(model, checkpoint_contract=checkpoint_contract,
        buffer_codecs=buffer_codecs,
        rank=rank, profile=profile, config_raw=config_raw, vllm_version=vllm_version, phase=phase)
    records, layouts, info = write_cpu_state(directory, model, chunk_bytes,
        checkpoint_contract=checkpoint_contract, auxiliary_receipts=receipts, rank=rank, buffer_codecs=buffer_codecs)
    inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
              for n, r in layouts.items()}
    plan = plan_rank(config_raw, rank=rank, tensor_parallel_size=8, revision=REVISION,
                     tensors=inputs, vllm_version=vllm_version, torch_version=torch_version,
                     chunk_bytes=chunk_bytes)
    return publish_index(directory, plan, records, layouts, config_raw,
                         aliases=info['alias_of'], auxiliary_receipts=info['auxiliary_receipts'],
                         buffer_encodings=info['buffer_encodings'], state_bindings=info['state_bindings'])
