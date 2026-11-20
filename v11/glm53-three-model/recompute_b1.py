"""Recompute qualified V11 GLM-5.3 B1 segments from their archived receipts."""

import argparse
import csv
import gzip
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from statistics import median


GIB = 2**30
CHUNK = 32 * 2**20
RANKS = set(range(8))
SEQUENCES = {'S1': 'KGDGKDK', 'S2': 'GDKDGKG', 'S3': 'DKGKDGD'}
NAMES = {'K': 'kimi', 'G': 'glm53', 'D': 'v4'}
S3_R1 = 'B1-GLM53-S3-20261119T175803Z'
SUMMARY_TIMINGS = ('activate_to_first_s', 'full_weight_load_s', 'activate_to_ready_s')
PIN_FAILURE = re.compile(r'pin_memory failed for chunk at ptr=0x[0-9a-fA-F]+ size=(\d+)')
METRICS = {'lookups': 'lmcache_mp_lookups_requests_total',
           'requested': 'lmcache_mp_lookup_requested_tokens_total',
           'hits': 'lmcache_mp_lookup_hit_tokens_total',
           'dropped': 'lmcache_mp_event_bus_dropped_events_total'}
CSV_FIELDS = ('run_id', 'sequence', 'segment', 'source_model', 'model', 'cache_state', 'file_read_bytes',
              'hit_bytes', 'allocated_blocks', 'freed_blocks', 'evicted_paths', 'activation_ns', 'ready_ns',
              'activate_to_ready_s', 'first_response_ns', 'activate_to_first_s', 'first_business_request',
              'full_weight_load_s', 'full_weight_max_rank_s', 'kv_fill_started_ns', 'kv_fill_received_ns',
              'kv_fill_response_s', 'host_write_confirmed_ns', 'host_confirm_s_from_request',
              'host_committed_tokens', 'host_used_bytes_after_write', 'host_used_bytes_before_clear',
              'host_used_bytes_after_clear',
              'host_lookup_hit_tokens', 'host_clear_begin_ns', 'host_clear_end_ns', 'evidence')


def require(ok, error):
    if not ok:
        raise ValueError(error)


def read(path):
    return json.loads(path.read_bytes())


def lines(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream]


def seconds(start, end):
    require(type(start) is type(end) is int and 0 < start < end, 'Invalid monotonic interval')
    return (end - start) / 1e9


def exited(path):
    doc = read(path)
    require(doc['Status'] == 'exited' and not doc['Running'] and not doc['OOMKilled']
            and doc['Pid'] == doc['ExitCode'] == 0 and not doc['Error'], f'Unclean exit: {path.name}')


def admission(root, protocol):
    booking = read(root / 'admission.json')
    window = booking['booking']
    require(window['node'] == 'qjhs003' and window['cluster_id'] == 'h20' and window['id']
            and window['start_ms'] <= booking['checked_at_wall_ns'] // 10**6 < window['end_ms'], 'Booking invalid')
    live = read(root / 'live-admission.json')
    require(len(live['gpus']) == len(set(live['gpus'])) == 8 and live['device_mib'] == [0] * 8
            and not live['compute_pids'] and not live['occupied_ports']
            and live['available_bytes'] >= live['required_bytes'] > 0, 'Live admission invalid')
    require((protocol['store_pool_bytes'], protocol['host_kv_pool_bytes'], protocol['store_chunk_bytes'],
             protocol['host_kv_chunk_tokens'], protocol['host_kv_fill_tokens'], protocol['lmcache'],
             protocol['first'], protocol['subsequent'], protocol['final_release_seconds']) == (
                864 * GIB, 813 * GIB, CHUNK, 256, 512, '0.5.5', 'safetensors_disk_cold', 'store', 60),
            'Frozen B1 protocol differs')
    return live['gpus']


def cold_proof(step, activation, run_id):
    path = step / 'sf-admission/summary.json'
    doc = read(path)
    require(doc['cache_condition_passed'] is True and doc['kind'] == 'safetensors_inputs'
            and doc['mode'] == 'cold' and doc['identity']['run_id'] == run_id
            and doc['identity']['clock_id'] == 'CLOCK_MONOTONIC'
            and 0 < doc['verification_begin_ns'] < doc['verification_complete_ns']
            <= doc['total_cache_admission_end_ns'] <= activation, 'SF cold-page admission failed')
    files = doc['files']
    require(files and len({row['file'] for row in files}) == len(files), 'SF admission inventory missing or duplicate')
    for row in files:
        vector = row['vector']
        require(Path(vector).name == vector and row['resident_pages'] == 0 and row['pages'] > 0
                and row['pages'] == (row['bytes'] + row['page_size'] - 1) // row['page_size'], 'SF page census invalid')
        raw = path.with_name(vector).read_bytes()
        pages = gzip.decompress(raw)
        require(hashlib.sha256(pages).hexdigest() == row['vector_sha256'] and len(pages) == row['pages']
                and pages.count(0) == len(pages), f'SF raw vector differs: {vector}')
    return dict(files=len(files), pages=sum(row['pages'] for row in files), resident_pages=0,
                evidence=path.relative_to(step.parent).as_posix())


def paths_for(protocol, key):
    roots = dict(zip('KDG', protocol['store_artifacts'], strict=True))
    root = PurePosixPath(roots[key]).relative_to('/data')
    groups = ['remainder', *(f'expert_{layer:04d}' for layer in range(61))] if key == 'D' else ['']
    return {str(root / group / f'rank_{rank}') for rank in RANKS for group in groups}


def backing_bytes(cache, protocol, key, index):
    # 首段直接读 SafeTensors，后续段按 Store 路径核对命中、重读和块分配。
    expected = paths_for(protocol, key) if index else set()
    events = cache['events']
    lookups = {row['path']: row for row in events if row['kind'] == 'lookup'}
    require(Counter(row['path'] for row in events if row['kind'] == 'lookup') == Counter(expected)
            and Counter(row['path'] for row in events if row['kind'] == 'complete') == Counter(expected),
            'Wrong Store lookup/completion coverage')
    hits = {path for path, row in lookups.items() if row['hit']}
    misses = expected - hits
    for kind in ('read_begin', 'read_end'):
        found = [row for row in events if row['kind'] == kind]
        require(Counter(row['path'] for row in found) == Counter(misses)
                and all(row['bytes'] == lookups[row['path']]['bytes'] for row in found), f'Wrong {kind} bytes')
    require(all(type(row['bytes']) is int and row['bytes'] > 0 for row in lookups.values()), 'Invalid Store path bytes')
    read_bytes = sum(lookups[path]['bytes'] for path in misses)
    hit_bytes = sum(lookups[path]['bytes'] for path in hits)
    require(read_bytes + hit_bytes == (protocol['store_artifact_bytes'][key] if index else 0)
            and (read_bytes, hit_bytes) == (cache['file_read_bytes'], cache['hit_bytes'])
            and set(cache['hit_paths']) == hits and set(cache['miss_paths']) == misses, 'Store byte totals differ')
    allocated = Counter()
    for row in events:
        if row['kind'] == 'allocate':
            allocated[row['path']] += row['blocks']
    freed = sum(row['blocks'] for row in events if row['kind'] == 'free')
    evicted = [row['path'] for row in events if row['kind'] == 'evict']
    state = ('safetensors_disk_cold' if not index else 'full_hit' if not misses else
             'partial_hit' if hits else 'full_miss')
    view = cache['pool_after']
    require(allocated == {path: (lookups[path]['bytes'] + CHUNK - 1) // CHUNK for path in misses}
            and (sum(allocated.values()), freed, evicted, state) == (
                cache['allocated_blocks'], cache['freed_blocks'], cache['evicted_paths'], cache['cache_state'])
            and view['free_blocks'] + view['used_blocks'] == protocol['store_pool_bytes'] // CHUNK
            and expected.issubset(view['resident_paths']) and (index or not view['resident_paths']),
            'Store block ledger differs')
    return dict(cache_state=state, file_read_bytes=read_bytes, hit_bytes=hit_bytes,
                allocated_blocks=sum(allocated.values()), freed_blocks=freed, evicted_paths=len(evicted))


def l1(status):
    manager = status['storage_manager']
    return manager['l1_managers']['_default'] if 'l1_managers' in manager else manager['l1_manager']


def host_kv(step, result, fill, expected_bytes):
    written, clear = read(step / 'kv-write.json'), read(step / 'kv-clear.json')
    current, before, after = written['status'], clear['before'], clear['after']
    pool = l1(current)
    require(all(l1(state)['memory_total_bytes'] == expected_bytes and state['chunk_size'] == 256
                for state in (current, before, after)), 'Host KV capacity changed')
    committed = sum(map(int, re.findall(r'Stored (\d+) tokens in',
                    (step / 'lmcache-store.log').read_text(encoding='utf-8'))))
    require(committed == written['stored_tokens'] >= 512 and pool['memory_used_bytes'] > 0
            and pool['total_object_count'] > 0 and pool['write_locked_count'] == 0
            and len(current['cache_context_meta']) == len(current['registered_gpu_ids']) == 8
            and set(current['cache_context_meta']) == set(map(str, current['registered_gpu_ids'])),
            'Host KV commit missing or unverified')
    require(all(s['active_sessions'] == 0 and not s['registered_gpu_ids'] and not s['cache_context_meta']
                for s in (before, after)) and clear['response']['status'] == 'ok'
            and clear['response']['cleared']['tier'] == 'l1'
            and l1(after)['memory_used_bytes'] == l1(after)['total_object_count'] == 0
            and fill['received_ns'] <= written['ns'] < clear['begin_ns'] < clear['end_ns'],
            'Host KV unregister/clear receipt invalid')
    metric_views = [(step / f'lookup-{label}.prom').read_text(encoding='utf-8').splitlines()
                    for label in ('before', 'after')]
    delta = {}
    for metric, name in METRICS.items():
        old, new = [[float(row.split()[-1]) for row in view if row.startswith((name + ' ', name + '{'))]
                    for view in metric_views]
        require(bool(new), f'Missing LMCache counter: {name}')
        delta[metric] = sum(new) - sum(old)
    require(delta == result['lookup'] and delta['lookups'] > 0 and delta['requested'] >= 512
            and delta['hits'] == delta['dropped'] == 0, 'Stale Host KV lookup or dropped telemetry')
    return dict(kv_fill_started_ns=fill['started_ns'], kv_fill_received_ns=fill['received_ns'],
                kv_fill_response_s=seconds(fill['started_ns'], fill['received_ns']),
                host_write_confirmed_ns=written['ns'], host_confirm_s_from_request=seconds(fill['started_ns'], written['ns']),
                host_committed_tokens=committed, host_used_bytes_after_write=pool['memory_used_bytes'],
                host_used_bytes_before_clear=l1(before)['memory_used_bytes'], host_used_bytes_after_clear=0,
                host_lookup_hit_tokens=delta['hits'],
                host_clear_begin_ns=clear['begin_ns'], host_clear_end_ns=clear['end_ns'])


def envelope(intervals, activation, upper):
    # 八 rank 包络取该阶段最早起点至最晚终点，同时保留最慢 rank 的耗时。
    require({row['rank'] for row in intervals} == RANKS and len(intervals) == 8, 'Expected eight rank intervals')
    for row in intervals:
        require(activation <= row['start_ns'] < row['end_ns'] <= upper, 'Rank interval outside activation')
        row['seconds'] = seconds(row['start_ns'], row['end_ns'])
    first = min(row['start_ns'] for row in intervals)
    last = max(row['end_ns'] for row in intervals)
    return dict(start_ns=first, end_ns=last, seconds=seconds(first, last),
                max_rank_s=max(row['seconds'] for row in intervals), ranks=sorted(intervals, key=lambda row: row['rank']))


def timings(step, key, measured):
    activation, response = measured['activation_ns'], measured['response_ns']
    ready = read(step / 'ready.json')['ns']
    phases = {}
    if key == 'G':
        names, fill_path = ['business'], 'fill-response.json'
        intervals = []
        for rank in sorted(RANKS):
            begin, end = lines(step / f'weight-load-rank-{rank}.jsonl')
            require((begin['rank'], end['rank'], begin['event'], end['event'], begin['pid']) == (
                rank, rank, 'begin', 'end', end['pid']), 'GLM-5.3 weight trace differs')
            intervals.append(dict(rank=rank, start_ns=begin['ns'], end_ns=end['ns']))
        require(intervals == measured['ranks'], 'GLM-5.3 rank summary differs')
        phases['full_weight_load'] = envelope(intervals, activation, ready)
        phases['full_weight_load']['evidence'] = 'weight-load-rank-*.jsonl'
    else:
        inputs = read(step / f'{NAMES[key]}-input.json')['requests']
        names, fill_path = [f"before-{item['name']}" for item in inputs if item['name'] != 'kv_fill'], 'before-kv_fill-response.json'
        require(all(read(step / f"before-{item['name']}-checks.json")['passed'] is True for item in inputs)
                and measured['passed'] and measured['gold'] and measured['backend_exit'] == 0,
                'Legacy B1 gold receipt invalid')
        identity = read(step / 'constructed.json')['clock_identity']
        domain = (identity['boot_id'], identity['time_namespace'], 'CLOCK_MONOTONIC')
        trace_run = f'{step.parent.name}-{step.name}'
        traces = [row for path in (step / 'events').glob('*.jsonl') for row in lines(path)]
        for phase in ('full_weight_load', 'weight_ingestion', 'quantization_postprocess', 'restore_postprocess', 'kv_cache_allocation'):
            paired = {end: [row for row in traces if row['name'] == f'{phase}_{end}'] for end in ('begin', 'end')}
            if any(len(rows) != 8 for rows in paired.values()):
                # 缺少完整八 rank 接点的历史阶段保持缺测，避免用部分跨度代替。
                continue
            require({(r['boot_id'], r['time_namespace'], r['clock_id'], r['run_id'])
                     for rows in paired.values() for r in rows} == {(*domain, trace_run)}, 'Rank clock domain differs')
            begin, last = ({row['rank']: row for row in paired[end]} for end in ('begin', 'end'))
            require(set(begin) == set(last) == RANKS and all(begin[r]['pid'] == last[r]['pid'] for r in RANKS),
                    'Legacy phase rank or process changed')
            phases[phase] = envelope([dict(rank=rank, start_ns=begin[rank]['monotonic_ns'],
                                           end_ns=last[rank]['monotonic_ns']) for rank in sorted(RANKS)], activation, ready)
            phases[phase]['evidence'] = f'events/*.jsonl {phase}_begin/end'
    gold = []
    for name in names:
        require(read(step / f'{name}-checks.json')['passed'] is True, f'Gold failed: {name}')
        raw = read(step / f'{name}-response.json')
        require(ready <= raw['started_ns'] < raw['received_ns'], 'Response clock order differs')
        gold.append((raw['received_ns'], name.removeprefix('before-')))
    require(min(gold)[0] == response and math.isclose(seconds(activation, response), measured['seconds'],
            rel_tol=0, abs_tol=1e-9), 'First correct complete response timestamp differs')
    fill = read(step / fill_path)
    require(max(gold)[0] <= fill['started_ns'], 'KV probe preceded completion of the business requests')
    full = phases.get('full_weight_load', {})
    return dict(activation_ns=activation, ready_ns=ready, activate_to_ready_s=seconds(activation, ready),
                first_response_ns=response, activate_to_first_s=seconds(activation, response),
                first_business_request=min(gold)[1], full_weight_load_s=full.get('seconds'),
                full_weight_max_rank_s=full.get('max_rank_s'), observed_phases=phases), fill


def release(root, gpus):
    rows = lines(root / 'release.jsonl')
    tail = []
    last = 0
    for row in rows:
        require(row['ns'] > last, 'Non-monotonic GPU release evidence')
        last = row['ns']
        if row['uuids'] == gpus and row['memory_mib'] == [0] * 8 and not row['apps']:
            tail.append(last)
        else:
            tail.clear()
    require(len(tail) > 1, 'GPU release lacks a sampled zero interval')
    elapsed = seconds(tail[0], tail[-1])
    require(elapsed >= 60, 'GPU release observation is shorter than 60 seconds')
    return dict(seconds=elapsed, samples=len(tail), start_ns=tail[0], end_ns=tail[-1], evidence='release.jsonl')


def host_kv_cuda_pin(root):
    path = root / 'lmcache-final.log'
    try:
        raw = path.read_bytes()
    except OSError:
        return dict(status='unobserved', evidence=None, failures=[])
    failures = [dict(line=number, bytes=int(match[1]))
                for number, line in enumerate(raw.decode('utf-8', errors='replace').splitlines(), 1)
                if (match := PIN_FAILURE.search(line))]
    # LMCache 0.5.5 logs registration failures and has no success receipt for this allocation.
    return dict(status='failed' if failures else 'unobserved', evidence=path.name,
                sha256=hashlib.sha256(raw).hexdigest(), failures=failures)


def segment_statistics(runs):
    grouped = defaultdict(list)
    for run in runs:
        if run['status'] == 'qualified':
            for row in run['steps']:
                grouped[row['sequence'], row['segment'], row['cache_state']].append(row)
    statistics = []
    for (sequence, segment, cache), rows in sorted(grouped.items()):
        metrics = {}
        for field in SUMMARY_TIMINGS:
            values = [row[field] for row in rows if row.get(field) is not None]
            metrics[field] = dict(n=len(values), median=median(values) if values else None,
                                  low=min(values, default=None), high=max(values, default=None))
        statistics.append(dict(sequence=sequence, segment=segment, cache_state=cache,
                               model=rows[0]['model'], run_ids=sorted(row['run_id'] for row in rows),
                               metrics=metrics))
    return statistics


def non_weight_windows(step):
    load = step.get('observed_phases', {}).get('full_weight_load')
    if load is None:
        return None
    activation, response = step['activation_ns'], step['first_response_ns']
    require(activation <= load['start_ns'] < load['end_ns'] <= response,
            'Full-weight envelope outside activation to first response')
    # 只拆分加载前后的实测窗口，避免把剩余耗时直接归因于 KV 操作。
    before = load['start_ns'] - activation
    after = response - load['end_ns']
    return dict(before_s=before / 1e9, after_s=after / 1e9, total_s=(before + after) / 1e9)


def s3_non_weight_comparison(runs):
    first_loads = {run['run_id']: run['steps'][0] for run in runs
                   if run['status'] == 'qualified' and run['sequence'] == 'S3'}
    if S3_R1 not in first_loads:
        return None
    reference = first_loads[S3_R1]
    baseline = non_weight_windows(reference)
    peers = []
    for run_id, step in sorted(first_loads.items()):
        if run_id == S3_R1 or (step['model'], step['cache_state']) != (reference['model'], reference['cache_state']):
            continue
        windows = non_weight_windows(step)
        difference = baseline['total_s'] - windows['total_s'] if baseline and windows else None
        peers.append(dict(run_id=run_id, windows=windows, r1_minus_peer_s=difference))
    return dict(run_id=S3_R1, windows=baseline, peers=peers,
                peer_n=sum(peer['r1_minus_peer_s'] is not None for peer in peers))


def inspect(root, include_partial=False):
    pin = host_kv_cuda_pin(root)
    result_path = root / 'result.json'
    if result_path.is_file():
        terminal = read(result_path)
        if terminal.get('qualified') is False:
            return dict(run_id=root.name, status='failed', evidence=str(result_path), raw_result=terminal, host_kv_cuda_pin=pin,
                        release_evidence='release.jsonl' if (root / 'release.jsonl').is_file() else None)
        require(terminal.get('qualified') is True and 'error' not in terminal and not terminal.get('cleanup_errors'),
                'Invalid terminal qualification')
    else:
        terminal = None
        if not include_partial or not (root / 'pool-init-timing.json').is_file():
            return dict(run_id=root.name, status='unfinalized', evidence=str(root), host_kv_cuda_pin=pin)
    protocol = read(root / 'protocol.json')
    sequence = protocol['sequence']
    frozen = SEQUENCES[sequence]
    require(re.fullmatch(rf'B1-GLM53-{sequence}-\d{{8}}T\d{{6}}Z', root.name)
            and protocol['models'] == [NAMES[key] for key in frozen], 'Frozen B1 sequence or run ID differs')
    gpus = admission(root, protocol)
    pools = read(root / 'pool-init-timing.json')
    initial = l1(read(root / 'lmcache-ready.json'))
    # 两池依次准备，先核对真实接点顺序再计算初始化耗时。
    require(pools['kv_ready_ns'] <= pools['store_begin_ns']
            and initial['memory_total_bytes'] == protocol['host_kv_pool_bytes']
            and initial['memory_used_bytes'] == 0, 'Pool order, capacity or initial occupancy differs')
    pool_timing = dict(kv_init_s=seconds(pools['kv_begin_ns'], pools['kv_ready_ns']),
                       store_init_s=seconds(pools['store_begin_ns'], pools['store_ready_ns']),
                       both_ready_s=seconds(pools['kv_begin_ns'], pools['store_ready_ns']),
                       host_used_bytes_at_kv_ready=initial['memory_used_bytes'], **pools)
    rows, previous_end = [], pools['store_ready_ns']
    dirs = [root / f'{index:02d}-{key}-{NAMES[key]}' for index, key in enumerate(frozen)]
    require({p for p in root.glob('??-*-*') if p.is_dir()}.issubset(dirs), 'Unexpected B1 segment directory')
    for index, (key, step) in enumerate(zip(frozen, dirs, strict=True)):
        if terminal is None and not (step / 'step-result.json').is_file():
            require(not any((later / 'step-result.json').is_file() for later in dirs[index + 1:]), 'Noncontiguous B1 receipts')
            break
        result = read(step / 'step-result.json')
        host, vram = read(step / 'host-admission.json'), read(step / 'vram-gate.json')
        require(result['model'] == NAMES[key] and host['available_bytes'] >= host['required_bytes'] > 0
                and vram['model'] == key and vram['passed'] is True and vram['attempts'][-1]['passed'] is True,
                f'Failed segment admission: {step.name}')
        exited(step / 'container-exit.json')
        timing, fill = timings(step, key, result['timing'])
        require(previous_end < timing['activation_ns'] and fill['received_ns'] <= read(step / 'normal-backend-shutdown.json')['ns'],
                'Segment activation or shutdown order differs')
        sf = cold_proof(step, timing['activation_ns'], root.name) if index == 0 else None
        backing = backing_bytes(result['cache'], protocol, key, index)
        host_kv_row = host_kv(step, result, fill, protocol['host_kv_pool_bytes'])
        rows.append(dict(run_id=root.name, sequence=sequence, segment=index, model=NAMES[key],
                         source_model=NAMES[frozen[index - 1]] if index else None, evidence=step.name,
                         sf_cold=sf, **timing, **host_kv_row, **backing))
        previous_end = host_kv_row['host_clear_end_ns']
    if terminal is None:
        return dict(run_id=root.name, status='unfinalized', sequence=sequence, pool_init=pool_timing,
                    host_kv_cuda_pin=pin, observed_steps=rows)
    require(terminal['sequence'] == sequence and len(terminal['steps']) == len(rows) == 7, 'Terminal segment count differs')
    for observed, declared in zip(rows, terminal['steps'], strict=True):
        require(declared == dict(model=observed['model'], gold=True, seconds=observed['activate_to_first_s'],
                                cache=observed['cache_state'], reread_bytes=observed['file_read_bytes'],
                                hit_bytes=observed['hit_bytes']), 'Terminal step summary differs from raw receipts')
    for name in ('lmcache', 'store'):
        exited(root / f'{name}-exit.json')
    released = release(root, gpus)
    require(previous_end < released['start_ns'] and math.isclose(terminal['release_seconds'], released['seconds'],
            rel_tol=0, abs_tol=1e-9), 'Terminal GPU release differs from sampled evidence')
    return dict(run_id=root.name, status='qualified', sequence=sequence, evidence=str(root),
                pool_init=pool_timing, host_kv_cuda_pin=pin, release=released, steps=rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('runs', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--include-partial', action='store_true', help='Show observations from unfinalized runs in JSON only')
    args = parser.parse_args()
    roots = [path.resolve() for path in args.runs]
    if len(roots) != len({path.name for path in roots}):
        parser.error('Pass each run only once')
    runs = []
    for root in roots:
        try:
            runs.append(inspect(root, args.include_partial))
        except (OSError, ValueError, TypeError, KeyError) as error:
            runs.append(dict(run_id=root.name, status='invalid_evidence', evidence=str(root), error=str(error)))
    args.output.mkdir(parents=True, exist_ok=True)
    status_counts = Counter(run['status'] for run in runs)
    summary = dict(counts=status_counts, runs=runs, segment_statistics=segment_statistics(runs),
                   s3_non_weight_comparison=s3_non_weight_comparison(runs))
    (args.output / 'b1-recomputed.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    with (args.output / 'b1-segments.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(step for run in runs if run['status'] == 'qualified' for step in run['steps'])
    print(json.dumps(status_counts, sort_keys=True))
    return int(bool(status_counts['invalid_evidence']))


if __name__ == '__main__':
    raise SystemExit(main())
