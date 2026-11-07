"""One V4 condition through the existing native service and evidence journal."""
import asyncio
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

from sllm_store.event_trace import process_identity, read_event_window
from sllm_store.v4_state import write_once
from v4_cache_state import run_cache
from v4_evidence import cache_scan_checks, cache_timing_checks, require, validate_business_event_order
from v4_native_service import validate_response
from v4_native_reference import inputs
from v4_attempts import frozen_protocol


ROOT = Path(__file__).resolve().parents[1]
CONDITIONS = {
    'safetensors_disk_cold': ('safetensors_inputs', 'cold'),
    'safetensors_disk_hot': ('safetensors_inputs', 'hot'),
    'store_disk_cold': ('store_inputs', 'cold'),
    'store_pool_hit': ('store_inputs', 'check'),
}


def time_left(deadline):
    seconds = (deadline - time.monotonic_ns()) / 1e9
    if seconds <= 0:
        raise TimeoutError('trial deadline exhausted')
    return seconds


def checked_json(path, expected_sha):
    data = Path(path).read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_sha, 'trial input SHA differs')
    return json.loads(data)


def trial_spec(protocol_path, protocol_sha, condition, run_id, attempt_id=None):
    protocol = checked_json(protocol_path, protocol_sha)
    rows = frozen_protocol(protocol_path, protocol_sha)
    require(protocol['schema'] == 2 and condition in CONDITIONS, 'trial requires the new40 protocol')
    require(protocol['engine_source'] == '132765e3560659ff63ebd236203672e991b70e08'
            and protocol['model_revision'] == 'b5968e9190ef611bbf34a7229255be88a0e937c1',
            'trial engine/model pins differ')
    require(protocol['attempt_timeout_seconds'] == 3600, 'trial requires the reviewed3600s window')
    profile = dict(tp=8, ep=False, pp=1, dp=1, max_model_len=2048, max_num_seqs=1,
        max_num_batched_tokens=2048, mtp=False, enforce_eager=True, prefix_cache=False,
        activation_dtype='bfloat16', expert_precision='native MXFP4',
        nonexpert_precision='native FP8 and exclusions', scale_fmt='UE8M0',
        mHC_learned_dtype='float32', seed=20261105, kv_cache_dtype='fp8',
        use_fp4_indexer_cache=False, distributed_executor_backend='mp', chunk_bytes=33554432,
        head_cpu=4, worker_cpu=4, head_memory_bytes=17179869184,
        worker_memory_bytes=1082331758592, pids_limit_per_role=4096,
        post_delete_observation_seconds=60, maximum_monitor_gap_seconds=5,
        request_sha256='707d5d8b6efbfe257ff310466a03d6e19df5dce1b6b205270eb6a3d7e9374876',
        workload32_sha256='75a520893b656a8bc921ed810d910f0b5cf53b5c35f769c9e3c25e085ad88b9d',
        native_gold_sha256='9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1')
    require(all(type(protocol.get(key)) is type(value) and protocol[key] == value
                for key, value in profile.items()), 'trial differs from frozen V4 profile/requests')
    pools = {name: 33554432 if name.startswith('safetensors_') else 890937278464 for name in CONDITIONS}
    declared_pools = protocol['store_pool_bytes_by_condition']
    require(set(declared_pools) == set(pools) and all(type(declared_pools[key]) is int
            and declared_pools[key] == value for key, value in pools.items()), 'unreviewed trial pool capacity')
    if attempt_id is not None:
        require(protocol.get('frozen') is True, 'formal protocol is not frozen')
        require(run_id == attempt_id, 'formal run and attempt identities differ')
        require(any(row['attempt_id'] == attempt_id and row['condition'] == condition for row in rows),
                'unknown formal ID/condition')
    else:
        require(run_id.startswith('v4n40_pilot_'), 'pilot requires a separate nonformal run label')
    inventory = ROOT / protocol.get('cache_inventory_path',
                                   'docs/evidence/v4-formal-cache-entry/cache-inputs-candidate.json')
    inventory.resolve().relative_to(ROOT.resolve())
    checked_json(inventory, protocol['cache_inventory_sha256'])
    proof = ROOT / 'docs/evidence/v4-native-service-success/native-service-review.json'
    require(checked_json(proof, protocol['native_service_review_sha256'])[
                'native_service_two_cycle_lifecycle_qualified'] is True, 'native lifecycle prerequisite missing')
    return dict(condition=condition, run_id=run_id, attempt_id=attempt_id,
                formal_id_consumed=attempt_id is not None, pool_bytes=pools[condition],
                attempt_timeout_seconds=protocol['attempt_timeout_seconds'],
                protocol_sha256=protocol_sha, cache_inventory_sha256=protocol['cache_inventory_sha256'],
                cache_inventory_path=str(inventory))


def target_registration(grouped, condition):
    require(condition in CONDITIONS, 'unknown trial condition')
    config = json.loads(json.dumps(grouped))
    if condition.startswith('safetensors_'):
        config['backend_config']['load_format'] = 'safetensors'
        config['backend_config'].pop('model_loader_extra_config')
    return config


def cache_on_worker(spec, deadline):
    inventory = checked_json('/control/cache-inventory.json', spec['cache_inventory_sha256'])
    target, mode = CONDITIONS[spec['condition']]
    other = 'store_inputs' if target == 'safetensors_inputs' else 'safetensors_inputs'
    for kind, operation, directory in ((other, 'check', 'cache-other'), (target, mode, 'cache-target')):
        run_cache(inventory[kind], kind, operation, Path('/output') / directory, deadline, spec['run_id'])
    return dict(complete_ns=time.monotonic_ns(), target_kind=target, counterpart_kind=other)


async def prepare_service_trial(controller, grouped, preparation, deadline, address):
    """Prepare the existing binding without ever constructing a GPU model."""
    import ray
    spec = preparation['trial']
    await asyncio.wait_for(controller.register.remote(grouped), time_left(deadline))
    validated = time.monotonic_ns()
    await asyncio.wait_for(controller.delete.remote(grouped['model']), time_left(deadline))
    manager = ray.get_actor('store_manager')
    state = dict(store_validation_complete_ns=validated, store_registration_reused=True,
                 fresh_daemon_no_previous_model=True)
    if spec['condition'] == 'store_pool_hit':
        fill_begin = time.monotonic_ns()
        require(await asyncio.wait_for(manager.load_to_host.remote('v4', grouped['model']), time_left(deadline)),
                'full Store pool preparation failed')
        state.update(pool_fill_begin_ns=fill_begin, pool_fill_complete_ns=time.monotonic_ns())
    worker = ray.remote(num_cpus=1, resources={'worker_id_v4': .01})(cache_on_worker)
    state['cache_worker'] = await asyncio.wait_for(worker.remote(spec, deadline - 180_000_000_000), time_left(deadline))
    if spec['condition'] == 'store_pool_hit':
        state['pool_confirmation_begin_ns'] = time.monotonic_ns()
        # An already loaded cohort performs fresh native confirmations for all496 IDs.
        require(await asyncio.wait_for(manager.load_to_host.remote('v4', grouped['model']), time_left(deadline)),
                'fresh complete-cohort pool confirmation failed')
        state.update(pool_confirmation_complete_ns=time.monotonic_ns(), pool_confirmed=True)
        state['pool_native_receipt'] = await asyncio.wait_for(
            manager.get_grouped_host_receipt.remote('v4', grouped['model']), time_left(deadline))
        require(len(state['pool_native_receipt']['receipts']) == 496,
                'missing complete-cohort native CPU submission receipts')
    info = await asyncio.wait_for(manager.get_store_info.remote('v4'), time_left(deadline))
    require(grouped['model'] in info[0] and len(info[0][grouped['model']][0]) == 496,
            'trial Store registration cohort differs')
    require((grouped['model'] in info[1]) == (spec['condition'] == 'store_pool_hit'),
            'trial Store pool state differs')
    require(ray.available_resources().get('GPU') == 8, 'unexpected GPU model during trial preparation')
    state.update(ready_ns=time.monotonic_ns(), address=address, model=grouped['model'],
                 registration=target_registration(grouped, spec['condition']), store_info=info)
    write_once(Path('/output/trial-service-ready.json'), state)
    return state


def post_raw(url, route, payload, path, deadline):
    """The HTTP body and receive time are durable before status/JSON validation."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url + route, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    sent = time.monotonic_ns()
    try:
        try:
            response = opener.open(request, timeout=min(900, time_left(deadline)))
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            status = response.code
        received = time.monotonic_ns()
    except BaseException as error:
        write_once(path, dict(sent_ns=sent, received_ns=time.monotonic_ns(), request=payload,
                             status=None, body=None, transport_error=repr(error)))
        if isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError):
            raise TimeoutError('native trial HTTP operation timed out') from error
        raise
    text = body.decode('utf-8', errors='replace')
    write_once(path, dict(sent_ns=sent, received_ns=received, request=payload, status=status,
                         body=text, body_base64=base64.b64encode(body).decode()))
    require(status == 200, 'native trial HTTP status ' + str(status))
    return json.loads(text), sent, received


def check_abort(output):
    path = output / 'trial-abort.json'
    if path.exists():
        reason = json.loads(path.read_bytes())
        if reason.get('is_timeout'):
            raise TimeoutError(reason['error'])
        raise RuntimeError(reason['error'])


def wait_json(path, deadline):
    while not path.exists():
        check_abort(path.parent)
        time_left(deadline)
        time.sleep(.2)
    check_abort(path.parent)
    return json.loads(path.read_bytes())


def cache_observation(directory, expected):
    summary = json.loads((directory / 'summary.json').read_bytes())
    vectors = {row['vector']: gzip.decompress((directory / row['vector']).read_bytes())
               for row in summary['files']}
    sizes = {name: row['bytes'] for name, row in expected.items()}
    cache_scan_checks(summary, sizes, vectors)
    return summary, sizes, vectors


def _drive_trial(base, preparation, journal=None):
    """Host writer; the existing host monitor and exact-CID guards remain active."""
    output = base / 'output'
    spec = preparation['trial']
    deadline = preparation['container_deadline_ns'] - 120_000_000_000
    ready = wait_json(output / 'trial-service-ready.json', deadline)
    wait_json(output / 'store-pre-model-baseline-checked.json', deadline)
    inventory = checked_json(base / 'cache-inventory.json', spec['cache_inventory_sha256'])
    kind = CONDITIONS[spec['condition']][0]
    other_kind = 'store_inputs' if kind == 'safetensors_inputs' else 'safetensors_inputs'
    target, files, vectors = cache_observation(output / 'cache-target', inventory[kind])
    other, other_files, other_vectors = cache_observation(output / 'cache-other', inventory[other_kind])
    prep = {key: ready[key] for key in ('store_validation_complete_ns', 'store_registration_reused',
            'pool_confirmation_begin_ns', 'pool_confirmation_complete_ns', 'pool_confirmed') if key in ready}
    clock = process_identity()
    identity = {key: target['identity'][key] for key in ('run_id', 'boot_id', 'time_namespace', 'clock_id')}
    require(identity == dict(run_id=spec['run_id'], **{key: clock[key] for key in identity if key != 'run_id'}),
            'cache clock/run identity differs from host')
    require({key: other['identity'][key] for key in identity} == identity, 'counterpart cache clock differs')
    if journal:
        require(journal.identity['run_id'] == spec['run_id'] and journal.record['condition'] == spec['condition'],
                'trial writer/run/condition differs')
        journal.startup(prep, target, files, vectors, other, other_files, other_vectors, identity)
        record = dict(journal.record)
    else:
        now = time.monotonic_ns()
        record = dict(attempt_id=spec['run_id'], condition=spec['condition'],
            preparation_begin_ns=preparation['preparation_begin_ns'], preparation_complete_ns=now,
            startup_command_ns=now, attempt_deadline_ns=preparation['deadline_ns'], **prep)
        cache_timing_checks(record, target, other, spec['condition'], spec['attempt_timeout_seconds'])
    # Vectors stay on disk; release the parent validation buffers before model startup.
    del vectors, other_vectors
    write_once(output / 'trial-startup.json', dict(record=record, cache_identity=identity))
    log_path = output / 'store-daemon.log'
    log_offset = log_path.stat().st_size
    write_once(output / 'trial-native-io-boundary.json', dict(offset=log_offset,
               startup_command_ns=record['startup_command_ns'], observed_ns=time.monotonic_ns()))
    url = 'http://' + ready['address'] + ':8343'
    _, _, registered = post_raw(url, '/register', ready['registration'], output / 'trial-register.raw.json', deadline)
    gold_path = ROOT / 'docs/evidence/v4-native-reference-success/successful-run/native-gold.json'
    gold = checked_json(gold_path, '9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1')['responses']
    responses = {}
    for request in inputs(ROOT):
        purpose = request['purpose']
        request_id = spec['run_id'] + '-' + purpose
        if purpose == 'business':
            if journal:
                journal.trigger(registered, request_id)
                record = dict(journal.record)
            else:
                record.update(register_complete_ns=registered, business_trigger_ns=time.monotonic_ns(),
                              business_request_id=request_id)
        payload = dict(request['request']['sampling'], model=ready['model'], messages=request['request']['messages'],
                       return_token_ids=True, request_id=request_id)
        actual, sent, received = post_raw(url, '/v1/chat/completions', payload,
                                         output / ('trial-' + purpose + '.raw.json'), deadline)
        if journal:
            if purpose == 'business':
                journal.business(actual, gold[purpose], output / 'events', received_ns=received)
            else:
                journal.workload32(actual, gold[purpose], received_ns=received)
        checks = validate_response(actual, gold[purpose], request)
        require(actual['id'] == request_id, 'trial response identity differs')
        if purpose == 'business':
            events = read_event_window(output / 'events', spec['run_id'], record['startup_command_ns'], received,
                boot_id=clock['boot_id'], time_namespace=clock['time_namespace'])
            validate_business_event_order(events, request_id, record['startup_command_ns'],
                                           record['business_trigger_ns'], received)
            if spec['condition'].startswith('store_'):
                groups = {rank: set() for rank in range(8)}
                for event in events:
                    if event['name'] == 'grouped_expert_postprocess_host_end':
                        groups[event['rank']].add(event['group_id'])
                require(all(value == {f'expert_{i:04d}' for i in range(61)} for value in groups.values()),
                        'incomplete rank/group restoration')
        responses[purpose] = dict(checks=checks, sent_ns=sent, received_ns=received)
    with log_path.open('rb') as stream:
        stream.seek(log_offset)
        native_log = stream.read()
    (output / 'trial-native-io.log').write_bytes(native_log)
    lines = native_log.decode('utf-8', errors='replace').splitlines()
    disk_starts = [line for line in lines if 'Loading model ' in line and ' size ' in line and ' to host' in line]
    disk_finishes = [line for line in lines if 'Finished loading model ' in line and ' from disk' in line]
    io = dict(native_log_offset=log_offset, native_log_sha256=hashlib.sha256(native_log).hexdigest(),
              native_disk_load_starts=len(disk_starts), native_disk_load_finishes=len(disk_finishes),
              scope='Existing native ToHost log transitions since startup command; not raw block IO byte accounting')
    write_once(output / 'trial-native-io.json', io)
    expected_disk_loads = 496 if spec['condition'] == 'store_disk_cold' else 0
    require(len(disk_starts) == len(disk_finishes) == expected_disk_loads,
            'native disk-load transitions differ from the declared condition')
    _, sent, deleted = post_raw(url, '/delete', dict(model=ready['model']), output / 'trial-delete.raw.json', deadline)
    if journal:
        journal.delete(sent, received_ns=deleted)
    result = dict(condition=spec['condition'], formal_id_consumed=journal is not None,
                  responses=responses, delete_ack_ns=deleted, runtime_qualified=False)
    write_once(output / 'trial-http-complete.json', result)
    write_once(output / 'trial-host-finished.json', dict(finished_ns=time.monotonic_ns()))
    return result


def drive_trial(base, preparation, journal=None):
    try:
        return _drive_trial(base, preparation, journal)
    except BaseException as error:
        try:
            check_abort(base / 'output')
        except BaseException as abort_error:
            error = abort_error
        if journal and journal.stage != 'result':
            journal.fail(error)
        raise error
