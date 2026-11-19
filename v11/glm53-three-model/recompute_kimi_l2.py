"""Recompute the qualified Kimi checkpoint reload and worker memory observations."""

import argparse
import json
import statistics
from pathlib import Path


GIB = 2**30
PHASES = {'before-sleep': 0, 'after-wake': 512, 'after-clear': 0}
FIELDS = ('current_bytes', 'anon_bytes', 'file_bytes', 'shmem_bytes', 'nonreclaimable_bytes')


def read(path):
    return json.loads(path.read_bytes())


def measure(rows):
    assert rows
    peak = max(rows, key=lambda row: row['current_bytes'])
    timestamps = [row['ns'] for row in rows]
    gaps = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
    return dict(samples=len(rows), begin_ns=timestamps[0], end_ns=timestamps[-1],
                median_gap_s=statistics.median(gaps) / 1e9 if gaps else None,
                max_gap_s=max(gaps) / 1e9 if gaps else None,
                current_peak=dict(ns=peak['ns'], **{k: peak[k] for k in FIELDS}),
                max_bytes={k: max(row[k] for row in rows) for k in FIELDS})


def inspect(root):
    qualification = root / 'qualification.json'
    if not qualification.is_file():
        return dict(run_id=root.name, state='unfinalized')
    result = read(qualification)
    if not result['qualified']:
        return dict(run_id=root.name, state='failed', original=result)
    assert result['level'] == 2 and result['release_seconds'] >= 60 and not result['cleanup_errors']
    cycle = result['cycle']
    assert set(cycle['h2d_tokens_per_rank']) == {str(rank) for rank in range(8)}
    assert set(cycle['h2d_tokens_per_rank'].values()) == {512}
    assert cycle['host_kv_bytes'] > 0 and cycle['host_objects'] >= 2 and cycle['gold_token_ids']
    worker = root / 'L2' / 'worker-K'
    responses = []
    for phase, expected in PHASES.items():
        metrics = read(worker / f'{phase}-lookup-delta.json')
        assert metrics['lookup_hit_tokens_total'] == expected and metrics['event_bus_dropped_events_total'] == 0
        assert metrics['lookup_requested_tokens_total'] == 512
        check = read(worker / f'{phase}-kv_fill-checks.json')
        assert check['passed']
        (choice,) = read(worker / f'{phase}-kv_fill-response.json')['response']['choices']
        tokens = choice['token_ids']
        assert tokens == cycle['gold_token_ids']
        responses.append(dict(phase=phase, hits=expected, token_ids=tokens))
    launch = read(root / 'optimization-launch.json')
    assert launch['arm'] in ('baseline', 'transpose', 'meta')
    assert (launch['candidate_sha256'] is None) == (launch['arm'] == 'baseline')
    (wake_path,) = worker.glob('wake-*-phases.json')
    wake = read(wake_path)
    endpoints = (wake['weight_map_begin_ns'], wake['weight_map_end_ns'], wake['checkpoint_reload_begin_ns'],
                 wake['checkpoint_reload_end_ns'], wake['kv_wake_begin_ns'], wake['kv_ready_ns'])
    assert list(endpoints) == sorted(endpoints)
    begin, end = endpoints[2:4]
    rows = [json.loads(line) for line in (root / 'worker-memory.jsonl').read_text().splitlines()]
    assert all(current['ns'] < following['ns'] for current, following in zip(rows, rows[1:]))
    assert {row['limit_bytes'] for row in rows} == {1664 * GIB}
    assert all(row['nonreclaimable_bytes'] == max(0, row['current_bytes'] - row['file_without_shmem_bytes'])
               for row in rows)
    reload_rows = [row for row in rows if begin <= row['ns'] <= end]
    assert rows[0]['ns'] < begin and end < rows[-1]['ns'] and reload_rows
    return dict(run_id=root.name, state='qualified', arm=launch['arm'], sha256=launch['candidate_sha256'],
                reload_s=(end - begin) / 1e9, gpu_kv_wake_s=(endpoints[-1] - endpoints[-2]) / 1e9,
                release_s=result['release_seconds'], wake_endpoints_ns=wake,
                full_memory=measure(rows), reload_memory=measure(reload_rows), responses=responses,
                raw_files=[str(qualification), str(wake_path), str(root / 'worker-memory.jsonl')])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = json.dumps([inspect(root) for root in args.roots], indent=2) + '\n'
    if args.output is None:
        print(result, end='')
    else:
        args.output.write_text(result, encoding='utf-8')
