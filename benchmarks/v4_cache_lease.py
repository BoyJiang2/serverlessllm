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
from v4_cache_state import check_fd, mincore_visibility, before_deadline, run_cache
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


def split_store_counterpart(inventory):
    """Split the Store counterpart into exclusives and the files shared with the SF set.

    The five shared metadata files are cold before the lock and hot after it; they can
    therefore only ever be asserted cold *before* the lease is taken.
    """
    shared = sorted(inventory['shared_metadata'])
    store = inventory['store_inputs']
    require(set(shared) <= set(store), 'shared metadata missing from the Store input set')
    shared_set = set(shared)
    require(len(shared) == len(shared_set) and shared_set == set(store) & set(inventory['safetensors_inputs']),
            'shared metadata differs from the actual SF/Store intersection')
    require(all(store[path] == inventory['safetensors_inputs'][path] for path in shared),
            'shared metadata identity differs between inventories')
    exclusive = {path: expected for path, expected in store.items() if path not in shared_set}
    require(len(exclusive) == len(store) - len(shared), 'Store exclusive count differs')
    return exclusive, shared


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


def counterpart_scan(files, mode, output, deadline, run_id):
    try:
        return run_cache(files, 'store_inputs', mode, output, deadline, run_id)
    finally:
        # The root helper's write_once JSON is0600; the host evidence writer is UID2147.
        for path in output.rglob('*'):
            if path.is_file() and not path.is_symlink():
                path.chmod(0o644)


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
    # Five metadata files belong to both input sets. Once the SF lease locks all 69 SF
    # inputs those five are pinned, so a simultaneous "Store inputs are cold" claim is
    # unsatisfiable on any filesystem. Reclaim the Store counterpart first and VERIFY it
    # here, before any page is locked; the trial then only observes the Store side and
    # enforces full SF-hot residency. The two states are sequential, never simultaneous.
    prelease = counterpart_scan(inventory['store_inputs'], 'cold',
                         Path('/output/cache-prelease-store-cold'),
                         preparation['container_deadline_ns'], spec['run_id'])
    require(prelease['cache_condition_passed'] is True,
            'Store counterpart is not cold before the SF lease')
    exclusive, shared = split_store_counterpart(inventory)
    with locked_files(files, preparation['container_deadline_ns']) as receipt:
        # Post-lock the five shared metadata files are pinned by this lease, so only the
        # Store-exclusive 1508 can still be asserted cold. Both facts are recorded.
        locked_cold = counterpart_scan(exclusive, 'check',
                                Path('/output/cache-locked-store-exclusive-cold'),
                                preparation['container_deadline_ns'], spec['run_id'])
        require(all(row['resident_pages'] == 0 for row in locked_cold['files']),
                'Store-exclusive inputs are not cold while the SF lease is held')
        receipt.update(run_id=spec['run_id'], state='locked',
                       store_exclusive_files=len(exclusive),
                       store_exclusive_cold=True,
                       store_shared_files=len(shared),
                       store_shared_paths=shared,
                       store_shared_state='hot_after_lock_by_construction',
                       prelease_store_files=len(prelease['files']))
        print(json.dumps(receipt), flush=True)
        require(sys.stdin.buffer.read() == b'', 'cache lease release requires stdin EOF')
    print(json.dumps(dict(state='released', run_id=spec['run_id'], identity=receipt['identity'],
                          released_ns=time.monotonic_ns(), **memory_status())), flush=True)


if __name__ == '__main__':
    main()
