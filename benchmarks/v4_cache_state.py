"""File-specific Linux cache admission using the existing GLM-compatible receipts."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_evidence import require


READ_BYTES = 64 * 1024**2
READ_WORKERS = 4


def source_stat(path):
    require(not path.is_symlink() and path.is_file(), 'cache input must be a regular non-link file')
    stat = path.stat()
    require(stat.st_size > 0, 'empty cache input')
    return dict(bytes=stat.st_size, device=stat.st_dev, inode=stat.st_ino, mtime_ns=stat.st_mtime_ns)


def check_fd(descriptor, expected):
    stat = os.fstat(descriptor)
    actual = dict(bytes=stat.st_size, device=stat.st_dev, inode=stat.st_ino, mtime_ns=stat.st_mtime_ns)
    require(actual == expected, 'cache input identity changed')


def before_deadline(deadline):
    if time.monotonic_ns() >= deadline:
        raise TimeoutError('cache admission deadline exhausted')


def prepare_file(path, expected, mode, deadline):
    before_deadline(deadline)
    begin = time.monotonic_ns()
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    count = 0
    try:
        check_fd(descriptor, expected)
        if mode == 'cold':
            os.posix_fadvise(descriptor, 0, expected['bytes'], os.POSIX_FADV_DONTNEED)
        elif mode == 'hot':
            while True:
                before_deadline(deadline)
                data = os.read(descriptor, READ_BYTES)
                if not data:
                    break
                count += len(data)
            require(count == expected['bytes'], 'short hot preparation read')
        check_fd(descriptor, expected)
    finally:
        os.close(descriptor)
    return dict(source_path=str(path), mode=mode, bytes_read=count,
                begin_ns=begin, complete_ns=time.monotonic_ns())


def hot_file_on_cpu(path, expected, deadline, cpu):
    affinity = os.sched_getaffinity(0)
    require(cpu in affinity, 'hot reader CPU is outside the inherited cpuset')
    try:
        os.sched_setaffinity(0, {cpu})
        result = prepare_file(path, expected, 'hot', deadline)
        return dict(result, read_cpu=cpu)
    finally:
        os.sched_setaffinity(0, affinity)


def mincore_identity():
    caps = next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                if line.startswith('CapEff:'))
    return dict(euid=os.geteuid(), cap_effective=caps,
                user_namespace=os.readlink('/proc/self/ns/user'))


def mincore_visibility(path):
    identity = mincore_identity()
    owner = path.stat().st_uid
    require(identity['euid'] == owner or int(identity['cap_effective'], 16) & (1 << 3),
            'mincore visibility denied: requires inode owner or effective CAP_FOWNER; all-one vectors are untrusted')
    return dict(identity, owner_uid=owner)


def mincore_file(path, expected):
    """PROT_NONE mapping observes residency without touching the input pages."""
    mincore_visibility(path)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.restype = ctypes.c_void_p
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                          ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]
    libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    page_size = os.sysconf('SC_PAGE_SIZE')
    size = expected['bytes']
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        check_fd(descriptor, expected)
        address = libc.mmap(None, size, 0, 2, descriptor, 0)
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        try:
            vector = (ctypes.c_ubyte * ((size + page_size - 1) // page_size))()
            if libc.mincore(address, size, vector) != 0:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
            flags = bytes(vector)
        finally:
            if libc.munmap(address, size) != 0:
                raise OSError(ctypes.get_errno(), os.strerror(ctypes.get_errno()))
        check_fd(descriptor, expected)
        return page_size, flags
    finally:
        os.close(descriptor)


def run_cache(files, kind, mode, output, deadline, run_id, reader_cpus=None):
    require(not sys.flags.optimize and not os.environ.get('PYTHONOPTIMIZE'), 'optimized Python forbidden')
    require(kind in ('store_inputs', 'safetensors_inputs') and mode in ('cold', 'hot', 'check'),
            'invalid cache condition')
    require(files and run_id, 'cache inventory and run identity required')
    require(mode != 'hot' or kind == 'safetensors_inputs', 'Store pool residency is not page-cache hot')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    begin = time.monotonic_ns()
    try:
        paths = [Path(path) for path in sorted(files)]
        require(all(path.is_absolute() for path in paths), 'absolute input paths required')
        for path in paths:
            mincore_visibility(path)
        if mode == 'hot':
            with ThreadPoolExecutor(max_workers=READ_WORKERS) as executor:
                if reader_cpus is not None:
                    require(len(reader_cpus) == READ_WORKERS and len(set(reader_cpus)) == READ_WORKERS
                            and all(type(cpu) is int and cpu in os.sched_getaffinity(0) for cpu in reader_cpus),
                            'hot reader affinity differs from inherited CPUs')
                    futures = [executor.submit(hot_file_on_cpu, path, files[str(path)], deadline,
                               reader_cpus[index % READ_WORKERS]) for index, path in enumerate(paths)]
                else:
                    futures = [executor.submit(prepare_file, path, files[str(path)], mode, deadline) for path in paths]
                preparation = [future.result() for future in futures]
        else:
            preparation = [prepare_file(path, files[str(path)], mode, deadline) for path in paths]
        prepared = time.monotonic_ns()
        write_once(output / 'preparation.json', dict(files=preparation, read_workers=READ_WORKERS if mode == 'hot' else 1,
                   read_cpus=reader_cpus,
                   read_buffer_bytes=READ_BYTES, preparation_begin_ns=begin, preparation_complete_ns=prepared))
        # A separate full-list pass is required: later warming may evict earlier files.
        verification_begin = time.monotonic_ns()
        rows = []
        for index, path in enumerate(paths):
            before_deadline(deadline)
            page, flags = mincore_file(path, files[str(path)])
            name = f'{index:05d}.mincore.gz'
            with (output / name).open('xb') as stream:
                stream.write(gzip.compress(flags, compresslevel=1, mtime=0))
            rows.append(dict(file=path.name, source_path=str(path), bytes=files[str(path)]['bytes'],
                mincore_visibility=mincore_visibility(path),
                page_size=page, pages=len(flags), resident_pages=flags.translate(bytes(x & 1 for x in range(256))).count(b'\x01'),
                vector=name, vector_sha256=hashlib.sha256(flags).hexdigest()))
        verified = time.monotonic_ns()
        for path in paths:
            require(source_stat(path) == files[str(path)], 'cache inventory changed during scan')
        passed = None if mode == 'check' else all(row['resident_pages'] == (0 if mode == 'cold' else row['pages']) for row in rows)
        result = dict(kind=kind, mode=mode, files=rows, cache_condition_passed=passed,
            identity=dict(process_identity(), run_id=run_id), preparation_begin_ns=begin,
            preparation_complete_ns=prepared, verification_begin_ns=verification_begin,
            verification_complete_ns=verified, total_cache_admission_end_ns=time.monotonic_ns(),
            scope='Linux client page cache for the frozen inputs only; not storage-server, hardware, CUDA or JIT cache')
        write_once(output / 'summary.json', result)
        before_deadline(deadline)
        require(passed is not False, 'declared cache condition failed; raw vectors retained')
        return result
    except BaseException as error:
        write_once(output / 'failure.json', dict(error=repr(error), preparation_begin_ns=begin, failed_ns=time.monotonic_ns(),
                   kind=kind, mode=mode, run_id=run_id))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--kind', choices=('store_inputs', 'safetensors_inputs'), required=True)
    parser.add_argument('--mode', choices=('cold', 'hot', 'check'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--deadline-ns', type=int, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    data = args.inventory.read_bytes()
    require(hashlib.sha256(data).hexdigest() == args.inventory_sha256, 'frozen cache inventory SHA differs')
    result = run_cache(json.loads(data)[args.kind], args.kind, args.mode, args.output, args.deadline_ns, args.run_id)
    print(json.dumps(dict(files=len(result['files']), cache_condition_passed=result['cache_condition_passed'])))


if __name__ == '__main__':
    main()
