"""SF-hot file-page residency lease; held by an owned CPU helper until stdin closes."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

from sllm_store.event_trace import process_identity
from sllm_store.v4_state import write_once
from v4_cache_state import check_fd, mincore_visibility, before_deadline, prepare_file
from v4_evidence import require


def memory_status():
    rows = Path('/proc/self/status').read_text().splitlines()
    return {key: int(next(row.split()[1] for row in rows if row.startswith(key + ':'))) * 1024
            for key in ('VmLck', 'VmRSS')}


@contextmanager
def locked_files(files, deadline):
    lib = ctypes.CDLL(None, use_errno=True)
    lib.mmap.restype = ctypes.c_void_p
    lib.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                        ctypes.c_int, ctypes.c_int, ctypes.c_long]
    for name in ('mlock', 'munlock', 'munmap'):
        getattr(lib, name).argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    mappings = []
    begin = time.monotonic_ns()
    try:
        for name, expected in sorted(files.items()):
            before_deadline(deadline)
            mincore_visibility(Path(name))
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                check_fd(fd, expected)
                address = lib.mmap(None, expected['bytes'], 1, 2, fd, 0)
                if address == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_errno(), 'cache lease mmap failed')
                mappings.append((address, expected['bytes']))
                if lib.mlock(address, expected['bytes']):
                    raise OSError(ctypes.get_errno(), 'cache lease mlock failed')
                check_fd(fd, expected)
            finally:
                os.close(fd)
        before_deadline(deadline)
        yield dict(begin_ns=begin, locked_ns=time.monotonic_ns(), files=len(mappings),
                   bytes=sum(size for _, size in mappings), identity=process_identity(), **memory_status())
    finally:
        errors = []
        for address, size in reversed(mappings):
            for name in ('munlock', 'munmap'):
                if getattr(lib, name)(address, size):
                    errors.append(dict(operation=name, errno=ctypes.get_errno()))
        require(not errors, 'cache lease release failed: ' + repr(errors))


def wait_line(process, deadline):
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while b'\n' not in data:
            before_deadline(deadline)
            if selector.select(.2):
                part = os.read(process.stdout.fileno(), 4096)
                require(part, 'cache lease helper exited before receipt')
                data.extend(part)
                require(len(data) <= 16384, 'oversized cache lease receipt')
    require(data.endswith(b'\n') and data.count(b'\n') == 1, 'unexpected cache lease output')
    return json.loads(data)


@contextmanager
def worker_lease(worker, output, spec, deadline):
    with (output / 'trial-cache-lease.log').open('x') as log:
        process = subprocess.Popen(['docker', 'exec', '-i', '--user', '0:0', '--env',
            'CUDA_VISIBLE_DEVICES=', '--env', 'PYTHONDONTWRITEBYTECODE=1', worker,
            '/opt/sllm-v4/bin/python', '/workspace/benchmarks/v4_cache_lease.py'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log)
        ready = None
        try:
            ready = wait_line(process, deadline)
            require(ready['run_id'] == spec['run_id'] and ready['state'] == 'locked', 'cache lease identity differs')
            write_once(output / 'trial-cache-lease-locked.json', ready)
            yield ready
        finally:
            process.stdin.close()
            if ready is not None:
                released = wait_line(process, deadline)
                write_once(output / 'trial-cache-lease-released.json', released)
                require(released['state'] == 'released' and released['run_id'] == spec['run_id']
                        and released['identity'] == ready['identity'] and released['VmLck'] == 0,
                        'cache lease release receipt differs')
            require(process.wait(timeout=max(.1, (deadline - time.monotonic_ns()) / 1e9)) == 0,
                    'cache lease helper failed')
            process.stdout.close()


def main():
    from v4_trial import checked_json
    preparation = json.loads(Path('/control/preparation.json').read_bytes())
    spec = preparation['trial']
    require(spec['condition'] == 'safetensors_disk_hot' and spec.get('sf_hot_page_cache_lease') is True,
            'file-page lease is only enabled by the SF-hot amendment')
    inventory = checked_json('/control/cache-inventory.json', spec['cache_inventory_sha256'])
    files = inventory['safetensors_inputs']
    # Release unused Store pages before acquiring the SF lease; the ordinary helper verifies them again.
    for name, expected in sorted(inventory['store_inputs'].items()):
        prepare_file(Path(name), expected, 'cold', preparation['container_deadline_ns'])
    with locked_files(files, preparation['container_deadline_ns']) as receipt:
        receipt.update(run_id=spec['run_id'], state='locked')
        print(json.dumps(receipt), flush=True)
        require(sys.stdin.buffer.read() == b'', 'cache lease release requires stdin EOF')
    print(json.dumps(dict(state='released', run_id=spec['run_id'], identity=receipt['identity'],
                          released_ns=time.monotonic_ns(), **memory_status())), flush=True)


if __name__ == '__main__':
    main()
