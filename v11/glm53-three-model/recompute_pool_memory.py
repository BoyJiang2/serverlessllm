"""Recompute the Store startup and teardown host-memory observations."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path


GIB = 2**30
FIELDS = ('MemAvailable', 'SUnreclaim', 'AnonPages')
TARGETS = {'lmcache': 813 * GIB, 'store': 864 * GIB}


def anon(row, service):
    return (row['services'][service] or {}).get('anon', 0)


def phase(row):
    lmc, store = (anon(row, service) for service in TARGETS)
    if lmc >= TARGETS['lmcache'] * 0.99:
        if store >= TARGETS['store'] * 0.99:
            return 'both_pools'
        if store <= GIB // 2:
            return 'lmcache_only'
    if max(lmc, store) <= GIB // 2:
        return 'no_pool'
    return 'transition'


def summarize(path, through_ns):
    data = hashlib.sha256()
    rows = []
    with path.open('rb') as stream:
        for line in stream:
            if not line.endswith(b'\n'):
                break
            row = json.loads(line)
            if row['end_ns'] > through_ns:
                break
            assert row['start_ns'] <= row['end_ns']
            if rows:
                assert row['start_ns'] > rows[-1]['end_ns']
            data.update(line)
            rows.append(row)
    assert rows and rows[-1]['end_ns'] == through_ns, (path, through_ns)
    groups = {}
    for row in rows:
        groups.setdefault(phase(row), []).append(row)
    intervals = {}
    for name, points in groups.items():
        metrics = {field: [row['host'][field] / GIB for row in points] for field in FIELDS}
        metrics.update({f'{service}_anon': [anon(row, service) / GIB for row in points] for service in TARGETS})
        intervals[name] = dict(samples=len(points), first_ns=points[0]['start_ns'], last_ns=points[-1]['end_ns'],
                               gib={k: dict(min=min(v), median=statistics.median(v), max=max(v))
                                    for k, v in metrics.items()})
    medians = [intervals[name]['gib']['SUnreclaim']['median'] for name in ('both_pools', 'lmcache_only')]
    return dict(source='qjhs003:' + str(path), through_ns=through_ns, prefix_sha256=data.hexdigest(),
                samples=len(rows), phases=intervals, both_minus_lmcache_median_sunreclaim_gib=medians[0] - medians[1])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('s1_trace', type=Path)
    parser.add_argument('s1_last_ns', type=int)
    parser.add_argument('s2_trace', type=Path)
    parser.add_argument('s2_last_ns', type=int)
    args = parser.parse_args()
    print(json.dumps({'S1': summarize(args.s1_trace, args.s1_last_ns),
                      'S2': summarize(args.s2_trace, args.s2_last_ns)}, indent=2))
