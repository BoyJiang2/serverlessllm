"""Recompute the qualified Kimi checkpoint reload and worker memory observations."""

import argparse
import hashlib
import json
import statistics
from pathlib import Path


GIB = 2**30
PHASES = {'before-sleep': 0, 'after-wake': 512, 'after-clear': 0}
FIELDS = ('current_bytes', 'anon_bytes', 'file_bytes', 'shmem_bytes', 'nonreclaimable_bytes')
PATCH_MODULES = {'transpose': 'vllm.model_executor.layers.fused_moe.layer',
                 'meta': 'vllm.model_executor.model_loader.reload.meta'}


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


def expert_reload(path, begin, end):
    marker = 'KIMI_L2_TRACE '
    layers = {f'language_model.model.layers.{layer}.mlp.experts': {} for layer in range(1, 61)}
    for line in path.read_text().splitlines():
        if marker not in line:
            continue
        trace = json.loads(line.split(marker, 1)[1])
        if trace['event'] != 'wna16_layer':
            continue
        rank, ns, layer = trace['rank'], trace['ns'], trace['layer']
        assert begin <= trace['start_ns'] <= ns <= end and rank in range(8)
        assert trace['loaded'] == trace['expected'] > 0 and rank not in layers[layer]
        layers[layer][rank] = dict(end_ns=ns, seconds_from_reload=(ns - begin) / 1e9,
                                   during_finalize=trace['during_finalize'])
    assert all(set(records) == set(range(8)) for records in layers.values())
    return layers


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
    image, = (token for token in launch['worker'] if token.startswith('sha256:'))
    input_paths = ('b2-inputs/kimi-input.json', 'sleep-kv-input.json', 'reference-probe.json')
    comparison = dict(image=image, inputs={path: hashlib.sha256((root / path).read_bytes()).hexdigest()
                                           for path in input_paths})
    (wake_path,) = worker.glob('wake-*-phases.json')
    (sleep_path,) = worker.glob('sleep-*-phases.json')
    wake = read(wake_path)
    sleep = read(sleep_path)
    endpoints = (wake['weight_map_begin_ns'], wake['weight_map_end_ns'], wake['checkpoint_reload_begin_ns'],
                 wake['checkpoint_reload_end_ns'], wake['kv_wake_begin_ns'], wake['kv_ready_ns'])
    assert list(endpoints) == sorted(endpoints)
    imports = read(worker / 'worker-imports.json')
    assert len(imports) == 8 and {row['rank'] for row in imports} == set(range(8))
    if launch['arm'] in PATCH_MODULES:
        module = PATCH_MODULES[launch['arm']]
        assert all(row['imports'][module]['sha256'] == launch['candidate_sha256'] for row in imports)
    wake_ack = read(worker / wake_path.name.replace('wake-', 'ack-').replace('-phases', ''))
    assert wake_ack['op'] == 'wake' and wake_ack['reload_ns'] == endpoints[3] - endpoints[2]
    identities = {row['rank']: row['identity'] for row in wake_ack['receipt']['ranks']}
    assert all(row['pid'] == identities[row['rank']]['pid'] for row in imports)
    events = [json.loads(line) for path in sorted((worker / 'events').glob('*.jsonl'))
              for line in path.read_text().splitlines()]
    load = {}
    for name in ('full_weight_load_begin', 'full_weight_load_end'):
        matched = [event for event in events if event['name'] == name]
        assert len(matched) == 8 and {event['rank'] for event in matched} == set(range(8))
        for event in matched:
            assert event['run_id'] == root.name and event['clock_id'] == 'CLOCK_MONOTONIC'
            assert all(event[key] == identities[event['rank']][key]
                       for key in ('pid', 'process_start_ticks', 'time_namespace', 'boot_id'))
        load[name] = {event['rank']: event['monotonic_ns'] for event in matched}
    cold_begin = min(load['full_weight_load_begin'].values())
    cold_end = max(load['full_weight_load_end'].values())
    assert all(load['full_weight_load_begin'][rank] < load['full_weight_load_end'][rank] for rank in range(8))
    sleep_end = sleep['sleep_end_ns']
    assert cold_begin < cold_end <= sleep['sleep_begin_ns'] <= sleep_end < endpoints[0]
    rows = [json.loads(line) for line in (root / 'worker-memory.jsonl').read_text().splitlines()]
    assert all(current['ns'] < following['ns'] for current, following in zip(rows, rows[1:]))
    assert {row['limit_bytes'] for row in rows} == {1664 * GIB}
    assert all(row['nonreclaimable_bytes'] == max(0, row['current_bytes'] - row['file_without_shmem_bytes'])
               for row in rows)
    assert rows[0]['ns'] < cold_begin and endpoints[3] < rows[-1]['ns']
    cold_rows = [row for row in rows if cold_begin <= row['ns'] <= cold_end]
    sleep_rows = [row for row in rows if sleep_end <= row['ns'] < endpoints[0]]
    reload_rows = [row for row in rows if endpoints[2] <= row['ns'] <= endpoints[3]]
    trace_path = root / 'backend-full.log'
    layers = expert_reload(trace_path, endpoints[2], endpoints[3])
    return dict(run_id=root.name, state='qualified', arm=launch['arm'], sha256=launch['candidate_sha256'],
                comparison=comparison,
                cold_load_s=(cold_end - cold_begin) / 1e9, cold_load_endpoints_ns=load,
                reload_s=(endpoints[3] - endpoints[2]) / 1e9, gpu_kv_wake_s=(endpoints[-1] - endpoints[-2]) / 1e9,
                release_s=result['release_seconds'], wake_endpoints_ns=wake,
                full_memory=measure(rows), cold_load_memory=measure(cold_rows), sleep_memory=measure(sleep_rows),
                reload_memory=measure(reload_rows), expert_layer_completions=layers, responses=responses,
                raw_files=[str(path) for path in (qualification, wake_path, sleep_path, worker / 'events',
                           worker / 'worker-imports.json', root / 'worker-memory.jsonl', trace_path)])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('roots', type=Path, nargs='+')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    runs = [inspect(root) for root in args.roots]
    qualified = [run for run in runs if run['state'] == 'qualified']
    if qualified:
        assert all(run['comparison'] == qualified[0]['comparison'] and run['responses'] == qualified[0]['responses']
                   for run in qualified), 'Image, request fixture or golden output changed across arms'
    result = json.dumps(runs, indent=2) + '\n'
    if args.output is None:
        print(result, end='')
    else:
        args.output.write_text(result, encoding='utf-8')
