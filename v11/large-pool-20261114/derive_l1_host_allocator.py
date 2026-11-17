"""Reconcile native L1 backup allocations with cgroup shmem in the first sleep."""

import argparse
import json
from pathlib import Path

from recompute_sleep import allocator_span, read


def first_sleep(run, model):
    level = run / 'L1'
    worker = level / f'worker-{model}'
    phase = f'L1-00-{model}'
    before_path = level / f'{phase}-backup-capacity.json'
    before = read(before_path)
    phases_path = sorted(worker.glob('sleep-*-phases.json'))[0]
    bounds = read(phases_path)
    end = bounds['sleep_end_ns']
    rows = allocator_span(worker / 'worker.log', 'cpu_backup_copy', 'weights',
                          bounds['sleep_begin_ns'], end)['rows']
    assert {row['rank'] for row in rows} == set(range(8))
    measured_path = level / f'{phase}-mem-after-sleep.json'
    if measured_path.is_file():
        after = read(measured_path)['cgroups'][model]
        shmem = after['shmem_bytes']
        before_shmem = before['cgroup']['shmem_bytes']
    else:
        measured_path = run / 'resource-samples.jsonl'
        samples = [json.loads(line) for line in measured_path.read_bytes().splitlines()]
        before_snap = [row for row in samples if row['ns'] <= bounds['sleep_begin_ns']
                       and row.get('cgroup_memory', {}).get('stat')]
        before_shmem = before_snap[-1]['cgroup_memory']['stat']['shmem']
        after = [row for row in samples if row['ns'] >= end and row['phase'] == f'{phase}:observation'
                 and row.get('cgroup_memory', {}).get('stat')]
        shmem = after[-1]['cgroup_memory']['stat']['shmem']
    per_rank = {}
    for rank in range(8):
        sizes = [row['bytes'] for row in rows if row['rank'] == rank]
        assert sizes and min(sizes) > 0
        per_rank[rank] = dict(blocks=len(sizes), logical_bytes=sum(sizes),
                              rounded_bytes=sum(1 << (size - 1).bit_length() for size in sizes))
    logical = sum(row['logical_bytes'] for row in per_rank.values())
    rounded = sum(row['rounded_bytes'] for row in per_rank.values())
    return dict(model=model, run=run.name, rank=per_rank, logical_bytes=logical,
                rounded_bytes=rounded, excess_bytes=rounded-logical,
                shmem_before_bytes=before_shmem, shmem_after_bytes=shmem,
                shmem_delta_bytes=shmem-before_shmem,
                measured_minus_rounded_bytes=shmem-before_shmem-rounded,
                sources=[path.as_posix() for path in (worker/'worker.log', phases_path, before_path, measured_path)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kimi', type=Path)
    parser.add_argument('v4', type=Path, nargs=3)
    args = parser.parse_args()
    print(json.dumps([first_sleep(args.kimi, 'K'), *[first_sleep(run, 'D') for run in args.v4]], indent=2))
