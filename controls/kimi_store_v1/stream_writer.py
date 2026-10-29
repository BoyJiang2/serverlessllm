"""CPU candidate for native Store tensor.data_N layout; no native index/loader."""
import hashlib
import os
from pathlib import Path

ALIGNMENT = 4096
PARTITION_BYTES = 10 * 1024**3
CHUNK_BYTES = 8 * 1024**2


def _write_all(stream, block):
    remaining = memoryview(block).cast('B')
    while remaining:
        count = stream.write(remaining)
        if not count:
            raise OSError('short write without progress')
        remaining = remaining[count:]


def write_records(directory, entries, partition_bytes=PARTITION_BYTES):
    """entries: (name, byte_length, iterable_of_CPU_buffers, expected_sha256)."""
    if type(partition_bytes) is not int or partition_bytes < 8 or partition_bytes % 8:
        raise ValueError('partition boundary must be a positive multiple of 8')
    directory = Path(directory)
    directory.mkdir(exist_ok=False)
    stream, used, global_offset, partition = None, 0, 0, -1
    records = {}

    def finish():
        tail = (-used) % ALIGNMENT
        _write_all(stream, bytes(tail))
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        return tail

    try:
        for name, size, chunks, expected in entries:
            if not isinstance(name, str) or not name or name in records:
                raise ValueError('invalid or duplicate tensor name')
            if type(size) is not int or size < 0:
                raise ValueError('invalid tensor byte size')
            if stream is None or used + size > partition_bytes:
                if stream is not None:
                    global_offset += finish()
                partition += 1
                stream = (directory / ('tensor.data_' + str(partition))).open('xb', buffering=0)
                used = 0
            start, local, digest, received = global_offset, used, hashlib.sha256(), 0
            for block in chunks:
                view = memoryview(block).cast('B')
                if len(view) > CHUNK_BYTES or received + len(view) > size:
                    raise ValueError('oversized block or record')
                # FileIO may short-write; never silently hash unwritten bytes.
                _write_all(stream, view)
                digest.update(view)
                received += len(view)
                # Release consumer references before asking a staged producer for its next block.
                del view, block
            if received != size or (expected is not None and digest.hexdigest() != expected):
                raise ValueError('record length or SHA mismatch; incomplete directory retained')
            padding = (-size) % 8
            _write_all(stream, bytes(padding))
            records[name] = dict(offset=start, partition=partition, local_offset=local,
                                 bytes=size, sha256=digest.hexdigest())
            used += size + padding
            global_offset += size + padding
        if stream is not None:
            finish()
    finally:
        if stream is not None and not stream.closed:
            stream.close()
    return records


def verify_records(directory, records):
    """Verify payload digests by bounded reads, without reconstructing tensors."""
    for name, record in records.items():
        if any(type(record.get(key)) is not int or record[key] < 0
               for key in ('bytes', 'partition', 'local_offset')):
            raise ValueError('invalid record range: ' + name)
        digest, remaining = hashlib.sha256(), record['bytes']
        path = Path(directory) / ('tensor.data_' + str(record['partition']))
        with path.open('rb') as stream:
            if record['local_offset'] + remaining > os.fstat(stream.fileno()).st_size:
                raise ValueError('record exceeds file bounds: ' + name)
            stream.seek(record['local_offset'])
            while remaining:
                block = stream.read(min(CHUNK_BYTES, remaining))
                if not block:
                    raise ValueError('truncated record: ' + name)
                digest.update(block)
                remaining -= len(block)
        if digest.hexdigest() != record['sha256']:
            raise ValueError('corrupt record: ' + name)
