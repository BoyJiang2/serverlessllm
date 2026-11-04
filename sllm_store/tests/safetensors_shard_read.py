"""One CPU-only safetensors shard: bounded raw read and integer-bit parity."""
import argparse
import hashlib
import json
import math
import os
import pathlib
import struct
import time

CHUNK = 8 << 20
SIZES = {'BF16': 2, 'I32': 4}


def validate_header(header, payload_bytes):
    rows = []
    for name, row in header.items():
        if name == '__metadata__':
            continue
        assert row['dtype'] in SIZES, ('unsupported dtype', name, row['dtype'])
        shape = row['shape']
        assert isinstance(shape, list) and all(type(n) is int and n >= 0 for n in shape)
        offsets = row['data_offsets']
        assert len(offsets) == 2 and all(type(n) is int for n in offsets)
        start, end = offsets
        assert 0 <= start <= end <= payload_bytes
        assert end - start == math.prod(shape) * SIZES[row['dtype']]
        rows.append((start, end, name, row))
    rows.sort()
    cursor = 0
    for start, end, _, _ in rows:
        assert start == cursor, 'gap or overlap'
        cursor = end
    assert rows and cursor == payload_bytes
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=pathlib.Path)
    parser.add_argument('--expected-bytes', type=int, required=True)
    parser.add_argument('--header-sha256', required=True)
    args = parser.parse_args()
    started = time.monotonic()
    assert not list(pathlib.Path('/dev').glob('nvidia*'))
    with args.path.open('rb', buffering=0) as source:
        before = os.fstat(source.fileno())
        assert before.st_size == args.expected_bytes
        length_raw = source.read(8)
        assert len(length_raw) == 8
        length = struct.unpack('<Q', length_raw)[0]
        assert 0 < length <= 32 << 20 and length + 8 <= before.st_size
        raw = source.read(length)
        assert len(raw) == length and hashlib.sha256(raw).hexdigest() == args.header_sha256
        header = json.loads(raw)
        rows = validate_header(header, before.st_size - 8 - length)
        source.seek(0)
        digest = hashlib.sha256()
        buffer = bytearray(CHUNK)
        total = 0
        read_started = time.monotonic()
        while count := source.readinto(buffer):
            digest.update(memoryview(buffer)[:count])
            total += count
        read_seconds = time.monotonic() - read_started
        assert total == before.st_size
        import torch
        from safetensors import safe_open
        assert not torch.cuda.is_initialized()
        tensors = []
        with safe_open(str(args.path), framework='pt', device='cpu') as archive:
            assert set(archive.keys()) == {row[2] for row in rows}
            for start, end, name, row in rows:
                tensor = archive.get_tensor(name)
                assert tensor.device.type == 'cpu' and tensor.is_contiguous()
                assert list(tensor.shape) == row['shape']
                assert tensor.dtype == {'BF16': torch.bfloat16, 'I32': torch.int32}[row['dtype']]
                view = tensor.view(torch.uint8).reshape(-1)
                source.seek(8 + length + start)
                file_hash = hashlib.sha256()
                tensor_hash = hashlib.sha256()
                offset = 0
                while offset < end - start:
                    count = min(CHUNK, end - start - offset)
                    block = source.read(count)
                    assert len(block) == count
                    actual = view[offset:offset + count].numpy().tobytes()
                    assert actual == block, (name, offset)
                    file_hash.update(block)
                    tensor_hash.update(actual)
                    offset += count
                assert file_hash.digest() == tensor_hash.digest()
                width = SIZES[row['dtype']]
                sample = view[:min(16 * width, view.numel())].numpy().tobytes()
                words = [int.from_bytes(sample[i:i + width], 'little', signed=row['dtype'] == 'I32') for i in range(0, len(sample), width)]
                tensors.append({'name': name, 'dtype': row['dtype'], 'shape': row['shape'], 'bytes': end - start, 'raw_sha256': file_hash.hexdigest(), 'first_raw_integer_words': words, 'bit_exact': True})
                del tensor, view
        after = os.fstat(source.fileno())
        assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
        assert not torch.cuda.is_initialized() and not list(pathlib.Path('/dev').glob('nvidia*'))
        print(json.dumps({'status': 'SAFETENSORS_SINGLE_SHARD_CPU_BITEXACT_PASS', 'path': str(args.path), 'file_bytes': total, 'file_sha256': digest.hexdigest(), 'external_fullfile_gold': None, 'header_sha256': args.header_sha256, 'sequential_read_seconds': read_seconds, 'sequential_MiB_per_second': total / (1 << 20) / read_seconds, 'cache_state': 'uncontrolled; no cache drop or cold-read claim', 'elapsed_seconds': time.monotonic() - started, 'tensor_count': len(tensors), 'dtype_counts': {dtype: sum(t['dtype'] == dtype for t in tensors) for dtype in SIZES}, 'tensors': tensors, 'cuda_initialized': False, 'scope': 'single shard CPU read and raw integer-bit parity, no float conversion, model conversion, inference or Store'}), flush=True)


if __name__ == '__main__':
    main()
