"""Recompute V11 joint Sleep L2 results from one archived run directory."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

from diagnostics import b2_kv_evidence
from diagnostics.b2_kv_evidence import registration, verify_transfer, worker_identity
from recompute_b1 import (GIB, NAMES, RANKS, SEQUENCES, backing_bytes, cold_proof, envelope,
                          exited, l1, lines, paths_for, read, release, require, seconds)


ALLOCATOR = re.compile(r'sleep_allocator phase=gpu_unmap_release tag=kv_cache rank=(\d+) '
                       r'bytes=\d+ start_ns=(\d+) end_ns=(\d+)')
STORE_IO = re.compile(r'\] (?:SLLM_CACHE_(?:LOOKUP|COMPLETE) .+|Loading model \S+ size \d+ to host|'
                      r'Finished loading model \S+ from disk)$')


def completed_lines(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.endswith('\n')]


def signature(keys):
    result = {json.dumps(key, sort_keys=True) for key in keys}
    require(len(result) == len(keys), 'Duplicate Host KV object key')
    return result


def pool(status, ids):
    tier = l1(status)
    require(status['is_healthy'] and status['chunk_size'] == 256 and
            set(status['registered_gpu_ids']) == ids and not status['active_sessions'] and
            not status['active_prefetch_jobs'] and tier['memory_total_bytes'] == 813 * GIB and
            not tier['read_locked_count'] and not tier['write_locked_count'], 'Invalid Host KV state')
    return tier['memory_used_bytes'], tier['total_object_count']


def delete(path, keys, ids):
    entry = read(path)
    before, after = (pool(entry[kind], ids) for kind in ('before', 'after'))
    require(signature(entry['keys']) == signature(keys) and before[1] - after[1] == len(keys) and
            after[0] < before[0] and entry['response']['ok'] and
            entry['response']['deleted'] == len(keys) and entry['response']['skipped'] == 0,
            f'Exact Host KV deletion failed: {path}')
    return dict(keys=len(keys), bytes_before=before[0], bytes_after=after[0],
                objects_before=before[1], objects_after=after[1])


def response(raw):
    if 'response' in raw:
        choice, = raw['response']['choices']
        return choice['message']['content'], choice['token_ids'], choice['finish_reason']
    return raw['text'], raw['token_ids'], raw['finish_reason']


def legacy_gold(raw, request):
    body = raw['response']
    choice, = body['choices']
    ids = choice.get('token_ids')
    if ids is None:
        ids = [int(next(iter(row))) for row in choice['logprobs']]
    if request['payload'].get('logprobs'):
        require(len(choice['logprobs']) == len(ids) and
                all(math.isfinite(row[str(token)]['logprob']) for token, row in zip(ids, choice['logprobs'])),
                'Invalid token logprobs')
    text, finish = choice['message']['content'], choice['finish_reason']
    usage = body['usage']
    if request.get('validation') == 'exact_numeric_answer':
        return (text.strip() == request['expected_content'] and finish == 'stop' and
                usage['completion_tokens'] == len(ids) > 0)
    candidates = [(request['expected'], request['length_only'])]
    if 'expected_native_0201' in request:
        candidates.append((request['expected_native_0201'], False))
    for value, length_only in candidates:
        if (finish == value['finish_reason'] and usage['prompt_tokens'] == value['prompt_tokens'] and
                usage['completion_tokens'] == len(ids) == value['completion_tokens'] and
                (length_only or (text, ids) == (value['content'], value['token_ids']))):
            return True
    return False


def probe(step, worker, key, phase, namespace, ranks, hit, requests, server):
    ack = read(step / f'{phase}-ack.json')
    require(set(ack['checks']) == set(requests) and all(check['passed'] is True for check in ack['checks'].values()),
            f'Gold failed: {phase}')
    arrivals = {}
    for name, request in requests.items():
        raw = read(worker / f'{phase}-{name}-response.json')
        require(read(worker / f'{phase}-{name}-checks.json')['passed'] is True and
                ack['started_ns'] <= raw['started_ns'] < raw['received_ns'] <= ack['finished_ns'],
                f'Gold response evidence invalid: {phase}/{name}')
        if key == 'G':
            text, _, reason = response(raw)
            require(text.rsplit('</think>', 1)[-1].strip() == '42' and reason == 'stop', f'Wrong GLM gold: {phase}')
        else:
            require(legacy_gold(raw, request), f'Wrong legacy gold: {phase}/{name}')
        if name == 'kv_fill':
            kv_response = response(raw)
            require(bool(kv_response[1]), 'Missing KV response token IDs')
            if key == 'G':
                require(raw['num_cached_tokens'] == hit, f'Wrong GLM request hit: {phase}')
        else:
            arrivals[name] = raw['received_ns']
    lookup = read(step / f'{phase}-lookup.json')
    transfer = read(step / f'{phase}-transfers.json')
    direction, event = ('H2D', 'MP_RETRIEVE_END') if hit else ('D2H', 'MP_STORE_END')
    finishes = [row for row in transfer['completions'] if row['event'] == event]
    require(finishes and all(r['request_id'] == lookup['request_id'] and r['model_name'] == namespace
                             for r in finishes) and lookup['model_name'] == namespace and
            not lookup['early_exit_reason'] and lookup['requested_tokens'] == 512 and lookup['hit_tokens'] == hit,
            f'Lookup and transfer sessions differ: {phase}')
    require(lookup in server['lookup-completions'] and
            all(row in server['transfer-completions'] for row in transfer['completions']) and
            all(row in server['group-transfers'] for row in transfer['groups']), 'Original LMCache records differ')
    identities = {f"cuda:{row['rank']}": row['instance_id'] for row in ranks}
    require(all(identities[row['device']] == row['engine_id'] for row in finishes),
            f'Host KV transfer from the wrong Worker: {phase}')
    transfer_keys = {row['transfer_key'] for row in finishes}
    groups = [row for row in transfer['groups'] if row['transfer_key'] in transfer_keys]
    transfer_bytes = verify_transfer(key, groups, finishes, ranks, namespace, direction)
    require(all(transfer_bytes[row['device'].split(':')[1]] == row['total_bytes'] for row in finishes),
            f'GPU copy bytes do not match completion: {phase}')
    ids = {row['instance_id'] for row in ranks}
    before, after = (pool(read(step / f'{phase}-host-{kind}.json'), ids) for kind in ('before', 'after'))
    commits = read(step / f'{phase}-host-commits.json')
    keys = commits['keys']
    events = commits['events']
    require(all(not row['failed'] and row in server['host-commits'] for row in events) and
            signature([k for row in events for k in row['committed']]) == signature(keys) and
            all(k['model_name'] == namespace for k in keys) and len(keys) == after[1] - before[1] and
            (before == after if hit else after[0] > before[0] and bool(keys)), f'Invalid Host KV commit: {phase}')
    summary = read(step / f'{phase}-kv-result.json')
    first, last = min(arrivals.values()), max(arrivals.values())
    require((first, last, hit, transfer_bytes) == (summary['first_business_ns'], summary['all_business_ns'],
                  summary['hit_tokens'], summary['transfer_bytes']), f'Host KV summary differs: {phase}')
    return dict(first_ns=first, all_ns=last, gold_requests=sorted(arrivals), kv_response=kv_response,
                run_started_ns=ack['started_ns'], run_finished_ns=ack['finished_ns'],
                hit_tokens=hit, direction=direction, rank_bytes=transfer_bytes,
                groups_per_rank={str(rank): sum(len(row['groups']) for row in groups if row['device'] == rank)
                                 for rank in sorted(map(int, transfer_bytes))},
                host_bytes_before=before[0], host_bytes_after=after[0], host_objects_after=after[1], keys=keys)


def previous_namespace(step, namespace, other, status, ready, run_start, server):
    raw = read(step / 'cross-model-lookup.json')
    by_id = {row['request_id']: row for row in raw['server']}
    require(len(by_id) == len(raw['server']) == 2 and all(row in server['lookup-completions'] for row in raw['server']),
            'Cross-model lookup sessions missing')
    receipt = raw['receipt']
    digest = hashlib.sha256(json.dumps(other['tokens'], separators=(',', ':')).encode()).hexdigest()
    for name, model, hit in (('previous', other['namespace'], 512), ('active', namespace, 0)):
        arm = receipt[name]
        event = by_id[arm['request_id']]
        require(arm['namespace'] == event['model_name'] == model and arm['token_sha256'] == digest and
                arm['hit_tokens'] == event['hit_tokens'] == hit and event['requested_tokens'] == 512 and
                not event['early_exit_reason'], 'Cross-model key isolation failed')
    intervals = raw.get('intervals')
    if intervals is not None:
        for name, source in (('previous', other['step'] / 'outgoing-namespace-lookup.json'),
                             ('active', step / 'incoming-namespace-lookup.json')):
            arm = receipt[name]
            event = by_id[arm['request_id']]
            require(read(source) == dict(receipt=arm, server=event, **intervals[name]) and
                    intervals[name]['started_ns'] <= event['ns'] <= intervals[name]['finished_ns'],
                    'Cross-model lookup arm differs from its source')
        require(intervals['previous']['finished_ns'] <= other['unregister_ns'] <= other['sleep_end_ns'] <=
                ready <= intervals['active']['started_ns'] <= intervals['active']['finished_ns'] <= run_start,
                'Cross-model lookup was performed outside its worker lifetime')
        require(other['all_gold_ns'] <= intervals['previous']['started_ns'], 'Outgoing lookup preceded gold')
        ids = set(status['registered_gpu_ids'])
        require(tuple(raw['retained_after_sleep']) == tuple(raw['retained_after_lookup']) ==
                other['retained'] == pool(status, ids), 'Host KV bytes or object count changed across switch')
    return dict(previous_hit_tokens=512, active_hit_tokens=0, prefix_sha256=digest, intervals=intervals)


def unregister_before_sleep(worker, ack, identity):
    # A later command can already be writing while replaying an in-flight prefix.
    for path in sorted(worker.glob('ack-*.json')):
        recorded = read(path)
        if recorded['started_ns'] == ack['started_ns']:
            require(recorded == ack, 'Native sleep ACK differs from its source')
            index = int(path.stem.split('-')[1]) - 1
            break
    else:
        raise ValueError('Native sleep ACK source is missing')
    request, detached = (read(worker / f'{name}-{index:04d}.json') for name in ('cmd', 'ack'))
    require(request == {'op': 'kv-action', 'action': 'unregister'} and detached['op'] == 'kv-action' and
            detached['started_ns'] <= detached['finished_ns'] <= ack['started_ns'] and
            worker_identity(detached['receipt']) == identity and
            all(row['bound'] is False for row in detached['receipt']), 'Eight-rank unregister evidence failed')
    return detached['started_ns']


def first_weight(worker, key, activation, ready):
    if key == 'G':
        paths = sorted(worker.glob('weight-load-rank-*.jsonl'))
        if len(paths) != 8:
            return None, None
        intervals = []
        for rank in sorted(RANKS):
            begin, end = lines(worker / f'weight-load-rank-{rank}.jsonl')
            require((begin['event'], end['event'], begin['rank'], end['rank'], begin['pid']) ==
                    ('begin', 'end', rank, rank, end['pid']), 'GLM-5.3 first-load rank or process differs')
            intervals.append(dict(rank=rank, start_ns=begin['ns'], end_ns=end['ns']))
        return envelope(intervals, activation, ready), None
    events = [row for path in (worker / 'events').glob('*.jsonl') for row in completed_lines(path)]
    clock = read(worker / 'constructed.json')['clock_identity']
    results = []
    for name in ('weights_pipeline_including_allocation', 'kv_cache_allocation'):
        sides = [[r for r in events if r['name'] == f'{name}_{suffix}' and
                  activation <= r['monotonic_ns'] <= ready] for suffix in ('begin', 'end')]
        result = None
        if all(len(part) == 8 and {r['rank'] for r in part} == RANKS for part in sides):
            begin, end = ({r['rank']: r for r in part} for part in sides)
            require(all(begin[r]['pid'] == end[r]['pid'] for r in RANKS) and
                    all((row['boot_id'], row['time_namespace'], row['clock_id']) ==
                        (clock['boot_id'], clock['time_namespace'], 'CLOCK_MONOTONIC')
                        for side in sides for row in side), f'{name} rank process or monotonic domain changed')
            result = envelope([dict(rank=r, start_ns=begin[r]['monotonic_ns'], end_ns=end[r]['monotonic_ns'])
                               for r in RANKS], activation, ready)
            result['event'] = name
        results.append(result)
    return results


def allocator_events(root):
    per_model = {}
    for key in 'KD':
        log = root / f'worker-{key}-final.log'
        if log.is_file():
            with log.open(encoding='utf-8', errors='replace') as stream:
                per_model[key] = [tuple(map(int, match.groups())) for line in stream
                                  if (match := ALLOCATOR.search(line))]
    return per_model


def step_result(root, step, index, model, state, previous, protocol, allocator, server, capacity):
    measured = read(step / 'step-result.json')
    worker = root / 'workers' / f'worker-{model}'
    fresh = model not in state
    mode = ('safetensors_disk_cold' if index == 0 else 'store_inplace_cumem') if fresh else 'checkpoint_wake'
    require((measured['index'], measured['model'], measured['mode'], measured['gold']) ==
            (index, model, mode, True), 'Unexpected sequence segment')
    gate = read(step / 'host-admission.json')
    require((gate['model'], gate['mode'], gate['headroom_bytes']) ==
            (model, mode, capacity['backend_headroom_bytes'][mode][model]) and
            set(gate['sleepers']) == set(state) and gate['available_bytes'] >= gate['required_bytes'] ==
            capacity['runtime_floor_bytes'] + gate['headroom_bytes'], 'Stage host admission differs')
    status = read(step / 'registered.json')
    ranks = status['ranks']
    identity = worker_identity(ranks)
    namespace = registration(status['status'], model, ranks, bound=True)
    require(all(row['asleep'] is False for row in ranks), 'KV storage was asleep after registration')
    original = state.get(model)
    if original:
        require((identity, namespace) == (original['identity'], original['namespace']), 'Worker changed on wake')
    active, ready, native = (measured[key] for key in ('activation_ns', 'ready_ns', 'native_ready_ns'))
    require(gate['ns'] <= active <= native <= ready and (ready, native) == (status['ready_ns'], status['native_ready_ns']),
            'Bad activation or readiness ordering')
    inputs = read(root / 'b2-inputs' / f'{NAMES[model]}-input.json')['requests'] if model != 'G' else []
    requests = {item['name']: item for item in inputs} if inputs else {'business': None, 'kv_fill': None}
    kv = probe(step, worker, model, f"{index:02d}-{'first' if fresh else 'wake'}", namespace, ranks,
               0 if fresh else 512, requests, server)
    require((kv['first_ns'], kv['all_ns'], kv['hit_tokens']) ==
            (measured['first_business_ns'], measured['all_business_ns'], measured['host_hit_tokens']) and
            ready <= kv['first_ns'], 'Correct response or hit endpoint differs')
    cross = previous_namespace(step, namespace, previous, status['status'], ready,
                               kv['run_started_ns'], server) if previous else None
    aftermath = wake_stages = None
    weight_prep_s = reload_s = kv_allocation_s = kv_ready = old_to_new_s = None
    weight = first_alloc = None
    if fresh:
        weight, first_alloc = first_weight(worker, model, active, native)
        if weight:
            weight_prep_s = weight['seconds']
        if first_alloc:
            kv_allocation_s = first_alloc['seconds']
            kv_ready = first_alloc['end_ns']
    else:
        require(kv['kv_response'] == original['reference'], 'Same prefix returned different tokens after wake')
        wake = read(step / 'wake.json')
        w = wake['stages']
        require(active <= w['weight_map_begin_ns'] <= w['weight_map_end_ns'] <= w['checkpoint_reload_begin_ns'] <
                w['checkpoint_reload_end_ns'] <= w['kv_wake_begin_ns'] < w['kv_ready_ns'] == native and
                native <= wake['ack']['finished_ns'] and wake['ack']['sleeping'] is False, 'Wake endpoints invalid')
        wake_stages = w
        weight_prep_s = seconds(w['weight_map_begin_ns'], w['checkpoint_reload_end_ns'])
        reload_s = seconds(w['checkpoint_reload_begin_ns'], w['checkpoint_reload_end_ns'])
        kv_allocation_s = seconds(w['kv_wake_begin_ns'], w['kv_ready_ns'])
        kv_ready = w['kv_ready_ns']
        if previous['unmap_begin_ns'] is not None:
            old_to_new_s = seconds(previous['unmap_begin_ns'], native)
        cleared = delete(step / 'after-wake-clear.json', original['keys'], {row['instance_id'] for row in ranks})
        aftermath = probe(step, worker, model, f'{index:02d}-after-clear', namespace, ranks, 0, requests, server)
        require(aftermath['kv_response'] == original['reference'], 'Cleared-prefix control returned different tokens')
        aftermath['deleted'] = cleared
    cache = measured['store']
    if mode == 'store_inplace_cumem':
        store = backing_bytes(cache, protocol, model, 1)
    else:
        require(not cache['events'] and cache['file_read_bytes'] == cache['hit_bytes'] == 0 and
                cache['cache_state'] == ('safetensors_disk_cold' if fresh else 'not_used'), 'Unexpected Store use')
        store = dict(cache_state=cache['cache_state'], file_read_bytes=0, hit_bytes=0,
                     allocated_blocks=0, freed_blocks=0, evicted_paths=0)
    store_entry = None
    if (capacity.get('applicable_sequence') == 'S2' and model == 'K'
            and mode == 'store_inplace_cumem'):
        store_entry = read(step / 'store-entry.json')
        prior_store = read(previous['step'] / 'step-result.json')['store']['pool_after']
        require(store_entry['ns'] <= gate['ns'] and set(store_entry['sleepers']) == set(gate['sleepers']) == {'G', 'D'}
                and all(store_entry[field] == value for field, value in prior_store.items())
                and set(store_entry['resident_paths']) == paths_for(protocol, 'D'),
                'S2 Kimi admission does not match the live V4 Store snapshot')
        require(store['evicted_paths'] > 0 and store['freed_blocks'] > 0,
                'S2 Kimi Store load did not demonstrate the expected eviction')
    cold = cold_proof(step, active, root.name) if index == 0 else None
    sleep = read(step / 'sleep.json')
    start, end = (sleep['stages'][key] for key in ('sleep_begin_ns', 'sleep_end_ns'))
    require(kv['all_ns'] <= start < end == measured['sleep_end_ns'] and sleep['ack']['sleeping'] is True and
            worker_identity(sleep['ranks']) == identity and
            all(row['asleep'] is True and row['bound'] is False for row in sleep['ranks']) and
            len(sleep['hbm_mib_before']) == len(sleep['hbm_mib_after']) == 8 and
            all(new < old for old, new in zip(sleep['hbm_mib_before'], sleep['hbm_mib_after'], strict=True)),
            'Bad sleep or HBM release receipt')
    unregister_ns = unregister_before_sleep(worker, sleep['ack'], identity)
    old_unmap = [(rank, t0, t1) for rank, t0, t1 in allocator.get(model, []) if start <= t0 < t1 <= end]
    unmap_begin = min(t0 for _, t0, _ in old_unmap) if {row[0] for row in old_unmap} == RANKS else None
    tokens = (read(worker / 'kv-probe.json')['prompt_token_ids'] if model == 'G' else
              next(item['payload']['input_tokens'] for item in inputs if item['name'] == 'kv_fill'))[:512]
    require(len(tokens) == 512, 'Frozen KV prefix missing')
    latest = aftermath if aftermath else kv
    keys = latest['keys']
    retained = (latest['host_bytes_after'], latest['host_objects_after'])
    tail = None
    if index == 6:
        tail = delete(step / 'last-use-clear.json', keys, {row['instance_id'] for row in ranks})
        keys = []
        retained = (tail['bytes_after'], tail['objects_after'])
    sleep_host = pool(sleep['host_status'], set()) if 'host_status' in sleep else None
    require(sleep_host is None or sleep_host == retained, 'Post-sleep Host KV state differs')
    state[model] = dict(identity=identity, namespace=namespace, reference=kv['kv_response'], tokens=tokens,
                        keys=keys, unmap_begin_ns=unmap_begin, step=step, unregister_ns=unregister_ns,
                        sleep_end_ns=end, retained=retained, all_gold_ns=kv['all_ns'])
    if fresh and first_alloc and previous and previous['unmap_begin_ns'] is not None:
        old_to_new_s = seconds(previous['unmap_begin_ns'], first_alloc['end_ns'])
    return dict(index=index, model=model, mode=mode, equivalent_workers=fresh is False,
                evidence=step.name, precheck_ns=gate['ns'], ram_at_admission_bytes=gate['available_bytes'],
                activation_ns=active, native_ready_ns=native, ready_ns=ready,
                activation_to_serve_s=seconds(active, ready), activation_to_first_gold_s=seconds(active, kv['first_ns']),
                activation_to_all_gold_s=seconds(active, kv['all_ns']), full_first_weight_load=weight,
                weight_preparation_s=weight_prep_s, checkpoint_reload_s=reload_s, wake_stages=wake_stages,
                new_gpu_kv_s=kv_allocation_s, new_gpu_kv_ready_ns=kv_ready, first_gpu_kv_allocation=first_alloc,
                old_gpu_kv_release_to_new_kv_ready_s=old_to_new_s, unregister_ns=unregister_ns,
                sleep_begin_ns=start, sleep_end_ns=end,
                old_kv_unmap_begin_ns=unmap_begin, hbm_mib_after_sleep=sleep['hbm_mib_after'],
                host_kv_after_sleep_bytes=sleep_host[0] if sleep_host is not None else None,
                gold=True, store=store, store_entry=store_entry, sf_cold=cold, kv=kv, after_clear=aftermath,
                cross_model=cross, tail_delete=tail)


def finish(root, terminal, state, rows, gpus):
    require(not terminal.get('error') and not terminal.get('cleanup_errors') and len(rows) == 7,
            'Terminal result differs from complete raw evidence')
    raw = [read(root / row['evidence'] / 'step-result.json') for row in rows]
    sequence = read(root / 'joint-sequence.json')
    require(raw == terminal['steps'] == sequence['steps'], 'Sequence summaries disagree')
    # Shutdown may emit additional frees. The primary I/O log coverage must be exact.
    events = [event for row in raw for event in row['store']['events']]
    with (root / 'store-final.log').open(encoding='utf-8', errors='replace') as stream:
        observed = [raw for line in stream if STORE_IO.search(raw := line.rstrip('\r\n'))]
    require(observed == [event['raw'] for event in events if STORE_IO.search(event['raw'])],
            'Store final log lookup/completion/read events differ')
    types = tuple(f'worker-{key}' for key in 'KGD') + ('lmcache', 'store')
    for role in types:
        exited(root / f'{role}-exit.json')
    shutdown = []
    for key in 'KGD':
        require(read(root / 'workers' / f'worker-{key}' / 'normal-backend-shutdown.json')['ns'] > 0,
                'Worker normal shutdown receipt missing')
        last = read(sorted((root / 'workers' / f'worker-{key}').glob('ack-*.json'))[-1])
        require(last['op'] == 'shutdown' and last['started_ns'] <= last['finished_ns'] <= sequence['finished_ns'],
                'Worker shutdown ACK invalid')
        shutdown.append(last['finished_ns'])
    require('LMCache HTTP server stopped' in (root / 'lmcache-final.log').read_text(encoding='utf-8'),
            'LMCache normal shutdown missing')
    ending = [delete(root / f'final-worker-{key}-clear.json', state[key]['keys'], set())
              for key in state if state[key]['keys']]
    require(ending and ending[-1]['bytes_after'] == ending[-1]['objects_after'] == 0, 'Tail Host KV was not zero')
    empty_path = root / 'lmcache-final-empty.json'
    empty = read(empty_path) if empty_path.is_file() else None
    if empty is not None:
        require(pool(empty['status'], set()) == (0, 0) and max(shutdown) <= empty['ns'] <= sequence['finished_ns'],
                'Final empty Host KV snapshot differs')
    observed = release(root, gpus)
    require(observed['seconds'] == terminal['release_seconds'], 'Release summary differs from raw samples')
    return ending, observed, empty['ns'] if empty is not None else None


def inspect(root):
    terminal_path = root / 'result.json'
    terminal = read(terminal_path) if terminal_path.is_file() else None
    out = dict(run_id=root.name, status='failed' if terminal else 'partial', qualified=False, segments=[],
               pool_init=None, tail_delete=None, gpu_release=None, final_host_empty_ns=None, evidence_errors=[],
               terminal_error=(terminal or {}).get('error'))
    try:
        if not (root / 'b2-plan.json').is_file() and not (terminal or {}).get('qualified'):
            return out
        plan = read(root / 'b2-plan.json')
        seq = list(SEQUENCES[plan['sequence']])
        out['sequence'] = plan['sequence']
        require(seq == plan['steps'] and plan['level'] == 2 and
                (plan['weight_store_gib'], plan['host_kv_pool_gib'], plan['gpu_kv_bytes_per_rank']) ==
                (864, 813, 16 * GIB), 'Frozen protocol differs')
        require((plan['first_load'], plan['subsequent_first_load'], plan['wake']) ==
                ('safetensors_disk_cold', 'store_inplace_cumem', 'native_checkpoint_same_worker'),
                'Frozen load or wake path differs')
        live = read(root / 'live-admission.json')
        booking = read(root / 'admission.json')
        window = booking['booking']
        require(window['node'] == 'qjhs003' and window['cluster_id'] == 'h20' and window['id'] and
                window['start_ms'] <= booking['checked_at_wall_ns'] // 10**6 < window['end_ms'], 'Booking invalid')
        gpus = live['gpus']
        require(len(gpus) == len(set(gpus)) == 8 and live['device_mib'] == [0] * 8 and
                not live['compute_pids'] and not live['occupied_ports'] and
                live['available_bytes'] >= live['joint_required_bytes'], 'Initial resource admission failed')
        # The V4 worker opens COMPLETE.json in the export root. Store requests
        # refer to the data paths under its groups mount.
        artifacts = plan['store_artifacts']
        k_store, d_groups = map(PurePosixPath, plan['legacy_daemon_mounts'])
        require(k_store == PurePosixPath(artifacts['K']) and
                d_groups == PurePosixPath(artifacts['D']) / 'groups', 'Store daemon mounts differ')
        protocol = dict(store_artifacts=[artifacts['K'], str(d_groups), artifacts['G']],
                        store_artifact_bytes=plan['store_artifact_bytes'], store_pool_bytes=864 * GIB)
        timing_path = root / 'pool-init-timing.json'
        if timing_path.is_file():
            timing = read(timing_path)
            kv_start, kv_ready, store_start, store_ready = (timing[key] for key in (
                'lmcache_started_ns', 'lmcache_ready_ns', 'store_started_ns', 'store_ready_ns'))
            require(kv_start < kv_ready <= store_start < store_ready, 'Dual-pool timing order differs')
            out['pool_init'] = dict(kv_init_s=seconds(kv_start, kv_ready),
                                    store_init_s=seconds(store_start, store_ready),
                                    both_ready_s=seconds(kv_start, store_ready), **timing)
        capacity = read(root / 'capacity.json')
        require(capacity['runtime_stop_bytes'] >= capacity['runtime_floor_bytes'] >= 50 * GIB,
                'Measured capacity floor is below 50 GiB')
        observer = Path(b2_kv_evidence.__file__)
        require(hashlib.sha256(observer.read_bytes()).hexdigest() ==
                read(root / 'controller-source.json')['b2_kv_evidence.py'], 'B2 group verifier source differs')
        state = {}
        previous = None
        allocator = allocator_events(root)
        server = {name: completed_lines(path) if (path := root / 'lmcache' / f'{name}.jsonl').is_file() else []
                  for name in ('lookup-completions', 'transfer-completions', 'group-transfers', 'host-commits')}
        for index, key in enumerate(seq):
            path = root / f'{index:02d}-{key}-{NAMES[key]}'
            if not (path / 'step-result.json').is_file():
                break
            out['segments'].append(step_result(root, path, index, key, state, previous, protocol, allocator, server, capacity))
            previous = state[key]
        if terminal and terminal.get('qualified'):
            require(out['pool_init'] is not None, 'Dual-pool timing is missing')
            out['tail_delete'], out['gpu_release'], out['final_host_empty_ns'] = finish(
                root, terminal, state, out['segments'], gpus)
            out.update(status='qualified', qualified=True)
        elif terminal and (root / 'release.jsonl').is_file() and terminal.get('release_seconds', 0) >= 60:
            out['gpu_release'] = release(root, gpus)
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        out['evidence_errors'].append(repr(error))
        if terminal and terminal.get('qualified'):
            out['status'] = 'evidence_mismatch'
    return out


def add_host_memory(run, result, path):
    """Use complete monitor samples from the same resident pool containers."""
    source = path.read_bytes()
    prefix = source[:source.rfind(b'\n') + 1]
    ids = {role: read(run / f'{role}-created.json')['Id'] for role in ('lmcache', 'store')}
    samples = []
    for line in prefix.splitlines():
        sample = json.loads(line)
        if all((sample['services'].get(role) or {}).get('container') == cid for role, cid in ids.items()):
            samples.append(sample)
    require(samples, 'Host memory trace does not contain both run-specific pools')
    result['host_monitor'] = dict(path=str(path), read_bytes=len(prefix), sha256=hashlib.sha256(prefix).hexdigest())
    for step in result['segments']:
        begin, end = step['precheck_ns'], step['sleep_end_ns']
        window = [s for s in samples if begin <= s['start_ns'] <= s['end_ns'] <= end]
        step['host_memory'] = None
        if not window:
            continue
        low = min(window, key=lambda s: s['host']['MemAvailable'])
        gap = max((right['start_ns'] - left['start_ns'] for left, right in zip(window, window[1:])), default=0)
        minimum = low['host']['MemAvailable']
        step['host_memory'] = dict(sample_count=len(window), min_available_bytes=minimum,
                                   max_drop_from_admission_bytes=step['ram_at_admission_bytes'] - minimum,
                                   min_sample_ns=low['start_ns'], max_gap_ns=gap,
                                   covers_window_to_3s=max(window[0]['start_ns'] - begin,
                                                           end - window[-1]['end_ns'], gap) <= 3 * 10**9,
                                   anon_bytes_at_min={role: state['anon'] for role, state in low['services'].items()
                                                      if state is not None})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--host-memory', type=Path, help='same-run observe_pool_memory JSONL')
    args = parser.parse_args()
    result = inspect(args.run)
    if args.host_memory:
        add_host_memory(args.run, result, args.host_memory)
    data = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(data, encoding='utf-8')
    else:
        print(data, end='')
    if result['status'] == 'evidence_mismatch':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
