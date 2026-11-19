"""Sample host and service cgroups without modifying the experiment."""

import argparse
import json
import re
import time
from pathlib import Path


FIELDS = {'MemTotal', 'MemFree', 'MemAvailable', 'Cached', 'Shmem', 'AnonPages', 'AnonHugePages', 'SReclaimable',
          'SUnreclaim', 'PageTables', 'KernelStack', 'Unevictable', 'Mlocked', 'SwapTotal'}
CGROUP_FIELDS = {'anon', 'anon_thp', 'file', 'file_thp', 'shmem', 'shmem_thp', 'slab', 'pagetables', 'kernel'}
B2_FIELDS = CGROUP_FIELDS | {'active_file', 'inactive_file', 'file_dirty', 'file_writeback',
                            'slab_unreclaimable', 'workingset_refault_file'}


def sample_host():
    return {name: int(value.split()[0]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
            for name, value in (line.split(':', 1),) if name in FIELDS}


def service_cgroup(run, service, joint=False):
    try:
        container = json.loads((run / f'{service}-created.json').read_bytes())['Id']
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    assert re.fullmatch(r'[0-9a-f]{64}', container)
    directory = Path('/sys/fs/cgroup/system.slice') / f'docker-{container}.scope'
    try:
        current = int((directory / 'memory.current').read_text())
        selected = B2_FIELDS if joint else CGROUP_FIELDS
        fields = {name: int(value) for line in (directory / 'memory.stat').read_text().splitlines()
                  for name, value in (line.split(),) if name in selected}
        if joint:
            fields['events'] = {name: int(value) for line in (directory / 'memory.events').read_text().splitlines()
                                for name, value in (line.split(),)}
    except FileNotFoundError:
        return None
    return dict(container=container, current=current, **fields)


def observe(run, output, max_seconds):
    joint = run.name.startswith('B2-GLM53-')
    names = ('lmcache', 'store', 'worker-K', 'worker-G', 'worker-D') if joint else ('lmcache', 'store')
    stop = time.monotonic() + max_seconds
    terminal = None
    with output.open('x') as stream:
        while time.monotonic() < stop:
            start_ns = time.monotonic_ns()
            host = sample_host()
            services = {name: service_cgroup(run, name, joint) for name in names}
            end_ns = time.monotonic_ns()
            sample = dict(start_ns=start_ns, end_ns=end_ns, wall_ns=time.time_ns(), host=host, services=services)
            stream.write(json.dumps(sample) + '\n')
            stream.flush()
            if terminal is None and (run / 'result.json').is_file():
                terminal = time.monotonic()
            if terminal is not None and time.monotonic() - terminal >= 30:
                return
            time.sleep(1 if joint else 2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, required=True)
    args = parser.parse_args()
    assert args.run.name.startswith(('B1-GLM53-', 'B2-GLM53-')) and 0 < args.max_seconds <= 15000
    observe(args.run, args.output, args.max_seconds)
