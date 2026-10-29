"""Publish the pinned save_dict index schema after CPU payload verification."""
import hashlib
import json
import os
from pathlib import Path

from contract import DTYPE_BYTES, plan_rank, validate_config
from stream_writer import verify_records


def _publish(directory, name, data):
    target, temporary = directory / name, directory / (name + '.partial')
    with temporary.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # Atomic no-clobber publication, including on POSIX where rename overwrites.
    os.link(temporary, target)
    try:
        temporary.unlink()
    except OSError:
        # The hardlink is the commit point; cleanup failure is not publication failure.
        return False
    return True


def publish_index(directory, plan, records, layouts, config_raw, aliases=None):
    """Only full, owned contiguous storage is supported; views fail closed."""
    directory = Path(directory)
    validate_config(config_raw)
    if (directory / 'tensor_index.json').exists():
        raise FileExistsError('native index already exists')
    if set(records) != set(layouts) or set(records) != set(plan['tensors']):
        raise ValueError('incomplete tensor inventory')
    aliases = {} if aliases is None else aliases
    if not isinstance(aliases, dict):
        raise ValueError('invalid alias map')
    for name, canonical in aliases.items():
        if (name not in records or not isinstance(canonical, str) or canonical not in records
                or name == canonical or canonical in aliases
                or records[name] != records[canonical] or layouts[name] != layouts[canonical]):
            raise ValueError('alias must identify an identical complete canonical record')
    inputs, index = {}, {}
    for name, layout in layouts.items():
        shape, stride, dtype = layout['shape'], layout['stride'], layout['dtype']
        if dtype not in DTYPE_BYTES or not isinstance(shape, list) or not isinstance(stride, list):
            raise ValueError('unreviewed tensor layout')
        if any(type(x) is not int or x < 0 for x in shape + stride):
            raise ValueError('invalid shape or stride')
        canonical, step = [], 1
        for dim in reversed(shape):
            canonical.insert(0, step)
            step *= max(dim, 1)
        if stride != canonical:
            raise ValueError('noncanonical strides require explicit view support')
        size = DTYPE_BYTES[dtype]
        for dim in shape:
            size *= dim
        if (type(layout.get('storage_offset')) is not int or layout['storage_offset'] != 0
                or type(layout.get('storage_bytes')) is not int or layout['storage_bytes'] != size
                or records[name]['bytes'] != size):
            raise ValueError('partial storage or size mismatch')
        inputs[name] = dict(shape=shape, dtype=dtype, bytes=size, contiguous=True)
        index[name] = [records[name]['offset'], size, shape, stride, dtype]
    expected = plan_rank(config_raw, rank=plan['rank'], tensor_parallel_size=plan['tensor_parallel_size'],
                         revision=plan['source_revision'], tensors=inputs,
                         vllm_version=plan['vllm_version'], torch_version=plan['torch_version'],
                         chunk_bytes=plan['chunk_bytes'])
    if expected != plan:
        raise ValueError('changed or unreviewed rank contract')
    verify_records(directory, records)
    partition_sizes = {}
    for name, row in records.items():
        number = row['partition']
        partition_sizes[number] = (directory / ('tensor.data_' + str(number))).stat().st_size
    if sorted(partition_sizes) != list(range(len(partition_sizes))):
        raise ValueError('missing partition')
    if {p.name for p in directory.glob('tensor.data_*')} != {
            'tensor.data_' + str(n) for n in partition_sizes}:
        raise ValueError('unregistered partition file')
    base, bases = 0, {}
    for number, size in sorted(partition_sizes.items()):
        if size % 4096:
            raise ValueError('unaligned physical partition')
        bases[number] = base
        base += size
    spans = []
    for name, row in records.items():
        offset = row['offset']
        if (type(offset) is not int or offset < 0 or offset % 8
                or offset != bases[row['partition']] + row['local_offset']):
            raise ValueError('global/local offset mismatch')
        if row['bytes'] and name not in aliases:
            spans.append((offset, offset + row['bytes']))
    spans.sort()
    if any(left[1] > right[0] for left, right in zip(spans, spans[1:])):
        raise ValueError('overlapping records need explicit alias support')
    encoded = json.dumps(index).encode('utf-8')
    sidecar = dict(plan, records=records, alias_of=aliases, aliases_deduplicated=bool(aliases),
                   written_payload_bytes=sum(r['bytes'] for n, r in records.items() if n not in aliases),
                   native_index_sha256=hashlib.sha256(encoded).hexdigest(),
                   payload_verified=True, tp_mapping_verified=False,
                   native_loader_verified=False, store_created=False, runtime_admitted=False)
    cleanup = []
    if not _publish(directory, 'config.json', config_raw):
        cleanup.append('config.json.partial')
    if not _publish(directory, 'kimi_raw_state.json', json.dumps(sidecar, indent=2).encode('utf-8')):
        cleanup.append('kimi_raw_state.json.partial')
    # Commit marker for the native schema is last; failure never publishes an index.
    if not _publish(directory, 'tensor_index.json', encoded):
        cleanup.append('tensor_index.json.partial')
    return dict(sidecar, cleanup_pending=cleanup)


def read_index(directory, expected_sidecar_sha256):
    """CPU audit with an external trusted sidecar digest; not load_dict."""
    directory = Path(directory)
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest mismatch')
    sidecar = json.loads(raw)
    config_raw = (directory / 'config.json').read_bytes()
    validate_config(config_raw)
    raw_index = (directory / 'tensor_index.json').read_bytes()
    if hashlib.sha256(raw_index).hexdigest() != sidecar['native_index_sha256']:
        raise ValueError('native index digest mismatch')
    index = json.loads(raw_index)
    if set(index) != set(sidecar['records']):
        raise ValueError('index tensor coverage mismatch')
    verify_records(directory, sidecar['records'])
    return index
