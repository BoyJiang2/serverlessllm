#!/usr/bin/env python3
"""Replay qjhs003 B1 host pressure for the exact qualified dual-pool cycles."""
import hashlib
import json
from pathlib import Path

ROOT = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/glm53-four-condition-20261118/formal-b1-runs')
RUNS = ('B1-GLM53-S1-20261119T141542Z', 'B1-GLM53-S2-20261119T154121Z',
        'B1-GLM53-S3-20261119T175803Z')
GIB = 2**30
SECOND = 10**9


def read(path):
    data = path.read_bytes()
    return json.loads(data), dict(path=str(path), sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))


def replay(run_id):
    root = ROOT / run_id
    result, result_ref = read(root / 'result.json')
    assert result['qualified'] and len(result['steps']) == 7 and result['release_seconds'] >= 60
    admission, admission_ref = read(root / 'live-admission.json')
    timing, timing_ref = read(root / 'pool-init-timing.json')
    pool_ready_ns = timing['store_ready_ns']
    raw_path = ROOT / 'memory-observations' / (run_id + '.jsonl')
    data = raw_path.read_bytes()
    samples = [json.loads(line) for line in data.splitlines(keepends=True) if line.endswith(b'\n')]
    observations = [(x['start_ns'], x['host']['MemAvailable']) for x in samples
                    if all(x['services'].get(name) for name in ('lmcache', 'store'))]
    near_ready = [(ns, available) for ns, available in observations if abs(ns - pool_ready_ns) <= 10 * SECOND]
    pool = [dict(sample_ns=ns, offset_ns=ns - pool_ready_ns, available_bytes=available,
                 starting_available_minus_sample_bytes=admission['available_bytes'] - available)
            for ns, available in near_ready]
    steps = []
    for index, path in enumerate(sorted(root.glob('[0-9][0-9]-*/step-result.json'))):
        step, step_ref = read(path)
        entry, entry_ref = read(path.parent / 'host-admission.json')
        clear, clear_ref = read(path.parent / 'kv-clear.json')
        activation = step['timing']['activation_ns']
        begin, end = (pool_ready_ns if index == 0 else activation), clear['end_ns']
        window = [(ns, available) for ns, available in observations if begin <= ns <= end]
        measurement = None
        if window:
            min_ns, min_available = min(window, key=lambda pair: pair[1])
            gap = max((right[0] - left[0] for left, right in zip(window, window[1:])), default=0)
            measurement = dict(n=len(window), first_sample_ns=window[0][0], last_sample_ns=window[-1][0],
                               max_gap_ns=gap, observed_min_available_ns=min_ns,
                               observed_min_available_bytes=min_available,
                               entry_to_min_drop_bytes=entry['available_bytes'] - min_available,
                               covers_window_to_3s=(window[0][0] - begin <= 3 * SECOND
                                                    and end - window[-1][0] <= 3 * SECOND and gap <= 3 * SECOND))
        steps.append(dict(index=index, name=path.parent.name, model=step['model'],
                          cache_state=step['cache']['cache_state'], window_begin_ns=begin, window_end_ns=end,
                          activation_ns=activation, entry_available_bytes=entry['available_bytes'],
                          memory=measurement, evidence=dict(step=step_ref, entry=entry_ref, clear=clear_ref)))
    assert len(steps) == 7
    return dict(run_id=run_id, controller_start_available_bytes=admission['available_bytes'],
                pool_ready_ns=pool_ready_ns, first_monitor_sample_ns=samples[0]['start_ns'],
                both_services_samples_within_10s_of_pool_ready=pool, steps=steps,
                evidence=dict(result=result_ref, admission=admission_ref, timing=timing_ref,
                              monitor=dict(path=str(raw_path), sha256=hashlib.sha256(data).hexdigest(),
                                           bytes=len(data), n=len(samples))))


def main():
    runs = [replay(run_id) for run_id in RUNS]
    peaks = {}
    for run in runs:
        for row in run['steps']:
            observed = row['memory']
            if not observed or not observed['covers_window_to_3s']:
                continue
            model = row['model']
            phase = 'sf_cold' if row['cache_state'] == 'safetensors_disk_cold' else 'store'
            key = f'{model}:{phase}'
            drop = observed['entry_to_min_drop_bytes']
            if key not in peaks or drop > peaks[key]['drop_bytes']:
                peaks[key] = dict(drop_bytes=drop, drop_gib=round(drop / GIB, 6), run_id=run['run_id'],
                                  step=row['name'], cache_state=row['cache_state'])
    print(json.dumps(dict(method=(
        'Drops are host MemAvailable at step admission minus sampled minimum, with both pools present. '
        'Use store_ready_ns through kv-clear.end_ns for the first step to cover cold preparation. '
        'Use step activation_ns through kv-clear.end_ns for other steps. Peak comparison requires samples '
        'within three seconds of both boundaries and internal gaps <=3s. This is ordinary B1 with one active '
        'Backend, no sleepers. Pool pressure compares live-admission MemAvailable to near-ready monitor samples.'),
        runs=runs, observed_max_drops=peaks), indent=2))


if __name__ == '__main__':
    main()
