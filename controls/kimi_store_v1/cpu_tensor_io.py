"""Real PyTorch CPU state adapter; deliberately rejects CUDA and storage views."""
import ctypes

from contract import DTYPE_BYTES
from stream_writer import CHUNK_BYTES, write_records
from runtime_inventory import collect_inventory, require_safe_byte_export
from checkpoint_contract import load_targets
from auxiliary_manifest import validate_auxiliary_receipts
from buffer_codec import encode_cpu_state


def tensor_layout(tensor):
    import torch
    if not isinstance(tensor, torch.Tensor) or tensor.device.type != 'cpu':
        raise ValueError('this entry only accepts real CPU tensors')
    dtype = str(tensor.dtype)
    if dtype not in DTYPE_BYTES or not tensor.is_contiguous():
        raise ValueError('unreviewed dtype or noncontiguous tensor')
    if tensor.is_neg() or tensor.is_conj():
        raise ValueError('lazy negative or conjugate view needs explicit handling')
    size = tensor.numel() * tensor.element_size()
    storage = tensor.untyped_storage()
    if tensor.storage_offset() != 0 or storage.nbytes() != size:
        raise ValueError('tensor does not own its full storage range')
    return dict(shape=list(tensor.shape), stride=list(tensor.stride()), dtype=dtype,
                storage_offset=0, storage_bytes=size)


def iter_cpu_blocks(tensor, chunk_bytes=CHUNK_BYTES):
    layout = tensor_layout(tensor)
    element = DTYPE_BYTES[layout['dtype']]
    if type(chunk_bytes) is not int or not element <= chunk_bytes <= CHUNK_BYTES:
        raise ValueError('invalid CPU byte block bound')
    step = chunk_bytes // element * element
    # ctypes exposes existing CPU storage without dtype casts or CPU clones.
    # Keep tensor alive across each yield; caller must consume synchronously.
    for offset in range(0, layout['storage_bytes'], step):
        count = min(step, layout['storage_bytes'] - offset)
        buffer = (ctypes.c_ubyte * count).from_address(tensor.data_ptr() + offset)
        buffer._tensor_owner = tensor  # The exported memoryview owns this ctypes object.
        view = memoryview(buffer).cast('B')
        yield view
        del view, buffer


def write_cpu_state(directory, model, chunk_bytes=CHUNK_BYTES, *, checkpoint_contract=None,
                    auxiliary_receipts=(), rank=None, buffer_codecs=None):
    import torch
    targets = () if checkpoint_contract is None else load_targets(checkpoint_contract)
    inventory = collect_inventory(model, checkpoint_targets=targets)
    require_safe_byte_export(inventory)
    named, layouts, encodings = encode_cpu_state(model, buffer_codecs)
    if not layouts:
        raise ValueError('empty state')
    receipts = validate_auxiliary_receipts(auxiliary_receipts, layouts, rank)
    if (type(chunk_bytes) is not int or chunk_bytes > CHUNK_BYTES
            or chunk_bytes < max(DTYPE_BYTES[r['dtype']] for r in layouts.values())):
        raise ValueError('invalid CPU byte block bound')
    aliases, seen = {}, {}
    for name, tensor in named:
        if layouts[name]['storage_bytes']:
            pointer = tensor.untyped_storage().data_ptr()
            if pointer in seen:
                prior = seen[pointer]
                if layouts[name] != layouts[prior]:
                    raise ValueError('shared storage with different tensor views needs review')
                aliases[name] = prior
            else:
                seen[pointer] = name
    entries = ((name, layouts[name]['storage_bytes'],
                iter_cpu_blocks(tensor, chunk_bytes), None) for name, tensor in named if name not in aliases)
    records = write_records(directory, entries)
    for name, canonical in aliases.items():
        records[name] = dict(records[canonical])
    return records, layouts, dict(source_torch_version=torch.__version__, source_device='cpu',
                                  auxiliary_receipts=receipts,
                                  buffer_encodings=encodings,
                                  alias_of=aliases, aliases_serialized_separately=False,
                                  inventory=inventory, inventory_policy_verified=False,
                                  checkpoint_name_coverage_verified=checkpoint_contract is not None,
                                  written_payload_bytes=sum(r['bytes'] for n, r in records.items() if n not in aliases),
                                  buffer_names=[name for name, _ in model.named_buffers(remove_duplicate=False)],
                                  chunk_bytes=chunk_bytes, native_loader_verified=False,
                                  runtime_admitted=False)
