"""Recompute V11 joint Sleep L2 results from one archived run directory."""

import argparse
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath

from diagnostics import b2_kv_evidence
from diagnostics.b2_kv_evidence import registration, verify_transfer, worker_identity
from index_evidence import indexed_file
from recompute_b1 import (GIB, NAMES, RANKS, SEQUENCES, backing_bytes, cold_proof, envelope,
                          exited, l1, lines, paths_for, read, release, require, seconds)
from recompute_kimi_l2 import expert_reload


ALLOCATOR = re.compile(r'sleep_allocator phase=gpu_unmap_release tag=kv_cache rank=(\d+) '
                       r'bytes=\d+ start_ns=(\d+) end_ns=(\d+)')
STORE_IO = re.compile(r'\] (?:SLLM_CACHE_(?:LOOKUP|COMPLETE) .+|Loading model \S+ size \d+ to host|'
                      r'Finished loading model \S+ from disk)$')
PIN_FAILURE = re.compile(r'pin_memory failed|cudaHostRegister\b.*\b(?:failed|error)\b', re.IGNORECASE)
PIN_DISABLED = re.compile(r'(?:disabl\w+.*(?:cuda host register|pin_memory)|'
                          r'(?:cuda host register|pin_memory).*disabl\w+)', re.IGNORECASE)
POOL_CONFIGS = {(864, 813), (0, 813), (0, 384)}
KIMI_REFERENCE_PROBE_SHA = 'd1954a790ffc9ef8e437da91f977ce6fff9c06fffe8da445029bfc9d5cab0b8d'
KIMI_META_MODULE = 'vllm.model_executor.model_loader.reload.meta'
KIMI_META_SHA = '94052a2faa6ed9b4afc02a73d44b5df2c41720f249f0d40569f32d30f87df8de'
KIMI_WNA16_MODULE = ('vllm.model_executor.layers.quantization.compressed_tensors.compressed_tensors_moe.'
                     'compressed_tensors_moe_wna16_marlin')
KIMI_WNA16_SHA = 'fe798dacd88b16efa4335af278057d1d97a7d367ac04aa07f3ad3383f5bdedff'


def completed_lines(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.endswith('\n')]


def signature(keys):
    result = {json.dumps(key, sort_keys=True) for key in keys}
    require(len(result) == len(keys), 'Duplicate Host KV object key')
    return result


def pool(status, ids, capacity_bytes):
    tier = l1(status)
    require(status['is_healthy'] and status['chunk_size'] == 256 and
            set(status['registered_gpu_ids']) == ids and not status['active_sessions'] and
            not status['active_prefetch_jobs'] and tier['memory_total_bytes'] == capacity_bytes and
            not tier['read_locked_count'] and not tier['write_locked_count'], 'Invalid Host KV state')
    used, objects = tier['memory_used_bytes'], tier['total_object_count']
    require(0 <= used <= capacity_bytes and objects >= 0 and (used == 0) == (objects == 0),
            'Host KV occupancy is inconsistent with pool capacity')
    return used, objects


def delete(path, keys, ids, capacity_bytes):
    entry = read(path)
    before, after = (pool(entry[kind], ids, capacity_bytes) for kind in ('before', 'after'))
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


def probe(step, worker, key, phase, namespace, ranks, hit, requests, server, capacity_bytes):
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
    # 命中只证明查找成功，实际传输还需核对八 rank 的分组与完成字节数。
    transfer_bytes = verify_transfer(key, groups, finishes, ranks, namespace, direction)
    require(all(transfer_bytes[row['device'].split(':')[1]] == row['total_bytes'] for row in finishes),
            f'GPU copy bytes do not match completion: {phase}')
    ids = {row['instance_id'] for row in ranks}
    before, after = (pool(read(step / f'{phase}-host-{kind}.json'), ids, capacity_bytes) for kind in ('before', 'after'))
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


def previous_namespace(step, namespace, other, status, ready, run_start, server, capacity_bytes):
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
        # 旧模型须在注销前查找，新模型在注册后查找，才能验证跨模型隔离。
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
                other['retained'] == pool(status, ids, capacity_bytes), 'Host KV bytes or object count changed across switch')
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


def kimi_imports(worker, ranks):
    expected = read(worker / 'legacy-launch-plan.json')['imports']
    require(expected[KIMI_META_MODULE]['sha256'] == KIMI_META_SHA and
            expected[KIMI_WNA16_MODULE]['sha256'] == KIMI_WNA16_SHA, 'Kimi reload pins differ')
    actual = read(worker / 'worker-imports.json')
    pids = {row['rank']: row['pid'] for row in ranks}
    require(len(actual) == 8 and {row['rank'] for row in actual} == RANKS and
            all(row['pid'] == pids[row['rank']] and row['imports'] == expected for row in actual),
            'Kimi actual import identities or SHA-256 differ')
    return dict(ranks=len(actual), meta=expected[KIMI_META_MODULE], wna16=expected[KIMI_WNA16_MODULE])


def step_result(root, step, index, model, state, previous, protocol, allocator, server, capacity):
    measured = read(step / 'step-result.json')
    worker = root / 'workers' / f'worker-{model}'
    capacity_bytes = protocol['host_kv_bytes']
    fresh = model not in state
    first_load = protocol.get('first_load', 'safetensors_disk_cold')
    subsequent_first_load = protocol.get('subsequent_first_load', 'store_inplace_cumem')
    mode = (first_load if index == 0 else subsequent_first_load) if fresh else 'checkpoint_wake'
    require((measured['index'], measured['model'], measured['mode'], measured['gold']) ==
            (index, model, mode, True), 'Unexpected sequence segment')
    gate = read(step / 'host-admission.json')
    require((gate['model'], gate['mode'], gate['headroom_bytes']) ==
            (model, mode, capacity['backend_headroom_bytes'][mode][model]) and
            set(gate['sleepers']) == set(state) and gate['available_bytes'] >= gate['required_bytes'] ==
            capacity['runtime_floor_bytes'] + gate['headroom_bytes'], 'Stage host admission differs')
    settle_path = step / 'host-admission-settle.json'
    settle = read(settle_path) if settle_path.is_file() else None
    if settle:
        samples = settle['samples']
        required, floor = gate['required_bytes'], capacity['runtime_stop_bytes']
        require((model, mode) == ('D', 'checkpoint_wake') and settle['qualified'] is True and
                settle['required_consecutive'] == 3 and settle['required_bytes'] == required and
                settle['runtime_stop_bytes'] == floor and len(samples) >= 4 and
                floor <= samples[0]['available_bytes'] < required and
                all(sample['available_bytes'] >= floor for sample in samples) and
                all(sample['available_bytes'] >= required for sample in samples[-3:]) and
                all(b['ns'] - a['ns'] >= 2 * 10**9 for a, b in zip(samples, samples[1:])) and
                samples[-1]['ns'] < settle['cutoff_ns'] <= samples[0]['ns'] + 30 * 10**9 and
                samples[-1] == {field: gate[field] for field in ('available_bytes', 'ns')},
                'V4 wake headroom settling evidence differs')
    status = read(step / 'registered.json')
    ranks = status['ranks']
    identity = worker_identity(ranks)
    namespace = registration(status['status'], model, ranks, bound=True)
    ids = {row['instance_id'] for row in ranks}
    pool(status['status'], ids, capacity_bytes)
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
               0 if fresh else 512, requests, server, capacity_bytes)
    require((kv['first_ns'], kv['all_ns'], kv['hit_tokens']) ==
            (measured['first_business_ns'], measured['all_business_ns'], measured['host_hit_tokens']) and
            ready <= kv['first_ns'], 'Correct response or hit endpoint differs')
    cross = previous_namespace(step, namespace, previous, status['status'], ready,
                               kv['run_started_ns'], server, capacity_bytes) if previous else None
    aftermath = wake_stages = None
    weight_prep_s = reload_s = kv_allocation_s = kv_ready = old_to_new_s = None
    weight = first_alloc = None
    # 首载取权重加载调用的边界，回访从原 Worker 重映射权重开始计时。
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
        cleared = delete(step / 'after-wake-clear.json', original['keys'], ids, capacity_bytes)
        aftermath = probe(step, worker, model, f'{index:02d}-after-clear', namespace, ranks, 0, requests, server,
                          capacity_bytes)
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
    cold = cold_proof(step, active, root.name) if mode == 'safetensors_disk_cold' else None
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
        tail = delete(step / 'last-use-clear.json', keys, ids, capacity_bytes)
        keys = []
        retained = (tail['bytes_after'], tail['objects_after'])
    # 无 Store 的两种池容量都须由休眠后的实际快照证明驻留状态。
    require(protocol['store_pool_bytes'] > 0 or 'host_status' in sleep, 'Missing post-sleep Host KV status')
    sleep_host = pool(sleep['host_status'], set(), capacity_bytes) if 'host_status' in sleep else None
    require(sleep_host is None or sleep_host == retained, 'Post-sleep Host KV state differs')
    state[model] = dict(identity=identity, namespace=namespace, reference=kv['kv_response'], tokens=tokens,
                        keys=keys, unmap_begin_ns=unmap_begin, step=step, unregister_ns=unregister_ns,
                        sleep_end_ns=end, retained=retained, all_gold_ns=kv['all_ns'])
    if fresh and first_alloc and previous and previous['unmap_begin_ns'] is not None:
        old_to_new_s = seconds(previous['unmap_begin_ns'], first_alloc['end_ns'])
    return dict(index=index, model=model, mode=mode, equivalent_workers=fresh is False,
                evidence=step.name, precheck_ns=gate['ns'], ram_at_admission_bytes=gate['available_bytes'],
                host_admission_settle=settle,
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


def finish(root, terminal, state, rows, gpus, capacity_bytes, *, has_store):
    require(not terminal.get('error') and not terminal.get('cleanup_errors') and len(rows) == 7,
            'Terminal result differs from complete raw evidence')
    raw = [read(root / row['evidence'] / 'step-result.json') for row in rows]
    sequence = read(root / 'joint-sequence.json')
    require(raw == terminal['steps'] == sequence['steps'], 'Sequence summaries disagree')
    # Shutdown may emit additional frees. The primary I/O log coverage must be exact.
    if has_store:
        events = [event for row in raw for event in row['store']['events']]
        with (root / 'store-final.log').open(encoding='utf-8', errors='replace') as stream:
            observed = [raw for line in stream if STORE_IO.search(raw := line.rstrip('\r\n'))]
        require(observed == [event['raw'] for event in events if STORE_IO.search(event['raw'])],
                'Store final log lookup/completion/read events differ')
        types = tuple(f'worker-{key}' for key in 'KGD') + ('lmcache', 'store')
    else:
        types = tuple(f'worker-{key}' for key in 'KGD') + ('lmcache',)
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
    ending = [delete(root / f'final-worker-{key}-clear.json', state[key]['keys'], set(), capacity_bytes)
              for key in state if state[key]['keys']]
    require(ending and ending[-1]['bytes_after'] == ending[-1]['objects_after'] == 0, 'Tail Host KV was not zero')
    empty_path = root / 'lmcache-final-empty.json'
    empty = read(empty_path) if empty_path.is_file() else None
    require(has_store or empty is not None, 'Final empty Host KV snapshot is missing')
    if empty is not None:
        require(pool(empty['status'], set(), capacity_bytes) == (0, 0) and not empty['status']['cache_context_meta'] and
                max(shutdown) <= empty['ns'] <= sequence['finished_ns'],
                'Final empty Host KV snapshot differs')
    observed = release(root, gpus)
    require(observed['seconds'] == terminal['release_seconds'], 'Release summary differs from raw samples')
    return ending, observed, empty['ns'] if empty is not None else None


def pin_evidence(path, expected_bytes):
    if not path.is_file():
        return None
    matches = []
    failure = disabled = registered = False
    with path.open(encoding='utf-8', errors='replace') as stream:
        for number, row in enumerate(stream, 1):
            failed = bool(PIN_FAILURE.search(row))
            off = bool(PIN_DISABLED.search(row))
            failure |= failed
            disabled |= off
            prefix = 'V11_L1_PIN '
            if row.startswith(prefix):
                try:
                    record = json.loads(row[len(prefix):])
                except json.JSONDecodeError:
                    record = None
                registered |= (isinstance(record, dict) and
                               record.get('configured_bytes') == expected_bytes and
                               record.get('registered_ranges') == [[0, expected_bytes]])
            if failed or off or 'CudaPinMemoryBackend:' in row or row.startswith(prefix):
                matches.append(dict(line=number, message=row.strip()))
    if failure:
        state = 'failure_observed'
    elif disabled:
        state = 'disabled_reported'
    else:
        state = 'success_confirmed' if registered else 'unverified'
    return dict(state=state, evidence=path.name, lines=matches)


def inspect(root):
    terminal_path = root / 'result.json'
    terminal = read(terminal_path) if terminal_path.is_file() else None
    out = dict(run_id=root.name, status='failed' if terminal else 'partial', qualified=False, segments=[],
               pool_init=None, tail_delete=None, gpu_release=None, final_host_empty_ns=None, evidence_errors=[],
               terminal_error=(terminal or {}).get('error'), host_pin=None, host_kv_peak_bytes=None)
    try:
        if not (root / 'b2-plan.json').is_file() and not (terminal or {}).get('qualified'):
            return out
        plan = read(root / 'b2-plan.json')
        seq = list(SEQUENCES[plan['sequence']])
        out['sequence'] = plan['sequence']
        store_gib, host_gib = (plan[key] for key in ('weight_store_gib', 'host_kv_pool_gib'))
        out.update(weight_store_gib=store_gib, host_kv_pool_gib=host_gib)
        require(seq == plan['steps'] and plan['level'] == 2 and (store_gib, host_gib) in POOL_CONFIGS and
                plan['gpu_kv_bytes_per_rank'] == 16 * GIB, 'Frozen protocol differs')
        has_store = store_gib > 0
        if (reference_sha := plan.get('source_sha256', {}).get('kimi-reference-probe.json')) is not None:
            launch = read(root / 'workers' / 'worker-K' / 'legacy-launch-plan.json')
            worker_input = root / 'b2-inputs' / 'kimi-input.json'
            require(reference_sha == launch['probe_source_sha256'] == KIMI_REFERENCE_PROBE_SHA and
                    (launch['prompt_tokens'], launch['cached_tokens']) == (560, 512) and
                    launch['input_sha256'] == hashlib.sha256(worker_input.read_bytes()).hexdigest(),
                    'Kimi reference probe differs from the frozen worker input')
            out['kimi_reference_probe_sha256'] = reference_sha
        capacity_bytes = host_gib * GIB
        capacity = read(root / 'capacity.json')
        require(capacity['pool_gib'] == {'weights': store_gib, 'host_kv': host_gib} and
                capacity['pool_budget_bytes'] >= (store_gib + host_gib) * GIB,
                'Capacity receipt disagrees with frozen pools')
        require(capacity['runtime_stop_bytes'] >= capacity['runtime_floor_bytes'] >= 50 * GIB,
                'Measured capacity floor is below 50 GiB')
        expected_subsequent = 'store_inplace_cumem' if has_store else 'safetensors_disk_cold'
        require((plan['first_load'], plan['subsequent_first_load'], plan['wake']) ==
                ('safetensors_disk_cold', expected_subsequent, 'native_checkpoint_same_worker'),
                'Frozen load or wake path differs')
        live = read(root / 'live-admission.json')
        booking = read(root / 'admission.json')
        if booking.get('mode') == 'user_authorized':
            window = booking
            handoff = booking['handoff_booking']
            require(booking['id'] == f'handoff-{root.name}' and
                    booking['authorization'] == 'current_holder_confirmed_by_operator' and
                    booking['resource_id'] == handoff['resource_id'] == 'h20/qjhs003' and
                    handoff['node'] == 'qjhs003' and handoff['cluster_id'] == 'h20' and
                    handoff['id'] and handoff['user'] and handoff['user'] != 'boyjiang' and
                    handoff['start_ms'] <= window['start_ms'] < window['end_ms'] <= handoff['end_ms'] and
                    window['end_ms'] - window['start_ms'] == 150 * 60 * 1000,
                    'Authorized handoff window differs from the recorded booking')
            out['admission_mode'] = 'user_authorized'
            out['handoff_booking_id'] = handoff['id']
        else:
            window = booking['booking']
        require(window['node'] == 'qjhs003' and window['cluster_id'] == 'h20' and window['id'] and
                window['start_ms'] <= booking['checked_at_wall_ns'] // 10**6 < window['end_ms'], 'Booking invalid')
        gpus = live['gpus']
        require(len(gpus) == len(set(gpus)) == 8 and live['device_mib'] == [0] * 8 and
                not live['compute_pids'] and not live['occupied_ports'] and
                live['available_bytes'] >= live['joint_required_bytes'], 'Initial resource admission failed')
        if has_store:
            # The V4 worker opens COMPLETE.json in the export root. Store requests
            # refer to the data paths under its groups mount.
            artifacts = plan['store_artifacts']
            k_store, d_groups = map(PurePosixPath, plan['legacy_daemon_mounts'])
            require(k_store == PurePosixPath(artifacts['K']) and
                    d_groups == PurePosixPath(artifacts['D']) / 'groups', 'Store daemon mounts differ')
            protocol = dict(store_artifacts=[artifacts['K'], str(d_groups), artifacts['G']],
                            store_artifact_bytes=plan['store_artifact_bytes'], store_pool_bytes=store_gib * GIB,
                            first_load=plan['first_load'], subsequent_first_load=plan['subsequent_first_load'])
        else:
            require(not any((root / f'store-{suffix}').exists() for suffix in ('created.json', 'exit.json', 'final.log')),
                    'Store service appeared in a Host-KV-only run')
            require(not any(plan.get(field) for field in ('store_artifacts', 'store_artifact_bytes',
                                                          'legacy_daemon_mounts')),
                    'Store artifacts present in a Host-KV-only plan')
            protocol = dict(store_artifacts=[], store_artifact_bytes={}, store_pool_bytes=0,
                            first_load=plan['first_load'], subsequent_first_load=plan['subsequent_first_load'])
        protocol['host_kv_bytes'] = capacity_bytes
        initial = root / 'lmcache-empty.json'
        if initial.is_file():
            status = read(initial)
            require(pool(status, set(), capacity_bytes) == (0, 0) and not status['cache_context_meta'],
                    'Initial Host KV pool is not empty')
        timing_path = root / 'pool-init-timing.json'
        if timing_path.is_file():
            timing = read(timing_path)
            kv_start, kv_ready, store_start, store_ready = (timing[key] for key in (
                'lmcache_started_ns', 'lmcache_ready_ns', 'store_started_ns', 'store_ready_ns'))
            if has_store:
                require(kv_start < kv_ready <= store_start < store_ready, 'Dual-pool timing order differs')
                out['pool_init'] = dict(kv_init_s=seconds(kv_start, kv_ready),
                                        store_init_s=seconds(store_start, store_ready),
                                        both_ready_s=seconds(kv_start, store_ready), **timing)
            else:
                require(kv_start < kv_ready and store_start is None and store_ready is None,
                        'Single-pool timing order differs')
                out['pool_init'] = dict(kv_init_s=seconds(kv_start, kv_ready),
                                        store_init_s=None,
                                        both_ready_s=seconds(kv_start, kv_ready), **timing)
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
        out['host_kv_peak_bytes'] = max(
            (probe[field] for row in out['segments'] for probe in (row['kv'], row['after_clear']) if probe
             for field in ('host_bytes_before', 'host_bytes_after')), default=None)
        out['host_pin'] = pin_evidence(root / 'lmcache-final.log', capacity_bytes) if terminal else None
        if terminal and terminal.get('qualified'):
            require(out['pool_init'] is not None, 'Pool timing is missing')
            out['tail_delete'], out['gpu_release'], out['final_host_empty_ns'] = finish(
                root, terminal, state, out['segments'], gpus, capacity_bytes, has_store=has_store)
            if not has_store:
                require(initial.is_file(), 'Initial empty Host KV snapshot is missing')
                first_kimi = next(row for row in out['segments'] if row['model'] == 'K')
                ranks = read(root / first_kimi['evidence'] / 'registered.json')['ranks']
                out['kimi_worker_imports'] = kimi_imports(root / 'workers' / 'worker-K', ranks)
                add_kimi_trace(out, root / 'worker-K-final.log')
                require(all((row.get('kimi_experts') or {}).get('layer_rank_completions') == 480
                            for row in out['segments'] if row['model'] == 'K' and row['mode'] == 'checkpoint_wake'),
                        'Kimi checkpoint reload lacks 60 expert layers × 8 ranks')
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
    has_store = result.get('weight_store_gib', (run / 'store-created.json').is_file())
    pool_roles = ('lmcache', 'store') if has_store else ('lmcache',)
    ids = {role: read(run / f'{role}-created.json')['Id'] for role in pool_roles}
    samples = []
    for line in prefix.splitlines():
        sample = json.loads(line)
        if all((sample['services'].get(role) or {}).get('container') == cid for role, cid in ids.items()):
            require('store' in ids or sample['services'].get('store') is None, 'Unexpected Store in host trace')
            samples.append(sample)
    require(samples, 'Host memory trace does not contain the run-specific pools')
    live_since = (result.get('pool_init') or {}).get('lmcache_ready_ns')
    live = [sample for sample in samples if live_since is None or sample['start_ns'] >= live_since]
    require(live, 'Host memory trace lacks samples after pool readiness')
    # 最低可用内存及当时的匿名内存取同一个样本，匿名峰值另计。
    low = min(live, key=lambda sample: sample['host']['MemAvailable'])
    result['host_monitor'] = dict(path=str(path), read_bytes=len(prefix), sha256=hashlib.sha256(prefix).hexdigest(),
                                  sample_count=len(live), min_available_bytes=low['host']['MemAvailable'],
                                  min_available_ns=low['start_ns'],
                                  lmcache_anon_at_min_bytes=low['services']['lmcache']['anon'],
                                  lmcache_anon_peak_bytes=max(s['services']['lmcache']['anon'] for s in live))
    kimi_receipt = run / 'worker-K-created.json'
    kimi_id = read(kimi_receipt)['Id'] if kimi_receipt.is_file() else None
    for step in result['segments']:
        if step['model'] == 'K' and step['mode'] == 'checkpoint_wake':
            step['kimi_reload_memory'] = kimi_reload_memory(samples, kimi_id, step['wake_stages'])
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


def kimi_reload_memory(samples, container, wake):
    begin, end = wake['checkpoint_reload_begin_ns'], wake['checkpoint_reload_end_ns']
    window = [(sample, state) for sample in samples
              if begin <= sample['start_ns'] <= sample['end_ns'] <= end
              and (state := sample['services'].get('worker-K'))
              and state['container'] == container]
    if not window:
        return None
    first, last = window[0][0], window[-1][0]
    gap = max((b[0]['start_ns'] - a[0]['start_ns'] for a, b in zip(window, window[1:])), default=0)
    peak, peak_state = max(window, key=lambda pair: pair[1]['anon'])
    refaults = [state.get('workingset_refault_file') for _, state in window]
    delta = None
    if len(refaults) > 1 and None not in refaults and all(a <= b for a, b in zip(refaults, refaults[1:])):
        delta = refaults[-1] - refaults[0]
    return dict(container=container, samples=len(window), first_sample_ns=first['start_ns'],
                last_sample_ns=last['end_ns'], max_gap_ns=gap,
                covers_window_to_3s=max(first['start_ns'] - begin, end - last['end_ns'], gap) <= 3 * 10**9,
                anon_peak_bytes=peak_state['anon'], anon_peak_sample_ns=peak['start_ns'],
                refault_pages_delta=delta)


def add_kimi_trace(result, path):
    result['kimi_trace'] = indexed_file(path.parent, path)
    result['kimi_trace']['path'] = str(path)
    for step in result['segments']:
        if step['model'] != 'K' or step['mode'] != 'checkpoint_wake':
            continue
        wake = step['wake_stages']
        # 一份日志包含多次唤醒，只在本段重载窗口内检查专家层完成记录。
        try:
            layers = expert_reload(path, wake['checkpoint_reload_begin_ns'], wake['checkpoint_reload_end_ns'],
                                   multiple=True)
        except (AssertionError, KeyError, ValueError) as error:
            step['kimi_experts'] = None
            step['kimi_trace_error'] = repr(error) if error.args else 'Expected 60 expert layers × 8 complete ranks'
            continue
        records = [record for ranks in layers.values() for record in ranks.values()]
        step['kimi_experts'] = dict(layer_rank_completions=len(records),
                                   completed_during_finalize=sum(record['during_finalize'] for record in records),
                                   last_expert_s=max(record['seconds_from_reload'] for record in records),
                                   layers=layers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--host-memory', type=Path, help='same-run observe_pool_memory JSONL')
    parser.add_argument('--kimi-log', type=Path, help='same-run complete Kimi Worker log')
    args = parser.parse_args()
    result = inspect(args.run)
    if args.host_memory:
        add_host_memory(args.run, result, args.host_memory)
    if args.kimi_log and 'kimi_trace' not in result:
        add_kimi_trace(result, args.kimi_log)
    data = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(data, encoding='utf-8')
    else:
        print(data, end='')
    if result['status'] == 'evidence_mismatch':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
