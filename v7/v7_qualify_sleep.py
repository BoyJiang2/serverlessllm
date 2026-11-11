"""CPU-only qualification of one full-model native Sleep L1 run."""
import json
import hashlib
from pathlib import Path
import sys
import traceback
import uuid

from v7_backend import check_response, validate_sleep_receipts


def read(path):
    return json.loads(path.read_bytes())


def gpu_window(samples, start, end, clock, uuids, *, container=None, minimum_seconds=0):
    valid, missing = [], []
    for sample in samples:
        if not start <= sample['ns'] <= sample['completed_ns'] <= end:
            continue
        assert (sample['boot_id'], sample['time_namespace']) == clock
        if sample['gpu']['exit'] or sample['apps']['exit']:
            missing.append(sample['ns'])
            continue
        rows = [line.split(',') for line in sample['gpu']['stdout'].splitlines()]
        assert len(rows) == 8
        assert [int(row[0]) for row in rows] == list(range(8))
        assert [row[1].strip() for row in rows] == uuids
        memory = [int(row[2]) for row in rows]
        assert all(n >= 0 for n in memory)
        data = None
        if container is not None:
            key, cid, constraints = container
            data = sample['containers'].get(key, {})
            if 'cgroup' not in data:
                missing.append(sample['ns'])
                continue
            assert data['cid'] == cid and data['state']['Running']
            assert data['constraints'] == constraints, 'Container resource requests changed'
            period = data['cgroup']['cpu.max'].split()
            assert int(period[0])*10**9 == constraints['NanoCpus']*int(period[1])
            events = dict(line.split() for line in data['cgroup']['memory.events'].splitlines())
            assert int(events['oom']) == int(events['oom_kill']) == 0
            app_pids = {line.split(',')[0].strip() for line in sample['apps']['stdout'].splitlines()}
            assert app_pids == set(sample['app_cgroups'])
            if any(not isinstance(group, str) for group in sample['app_cgroups'].values()):
                missing.append(sample['ns'])
                continue
            for group in sample['app_cgroups'].values():
                assert group == data['cgroup_path'] or group.startswith(data['cgroup_path']+'/'), 'Foreign GPU process'
        assert sample['ns'] <= sample['gpu']['started_ns'] <= sample['gpu']['completed_ns'] <= sample['completed_ns']
        valid.append(dict(sample=sample, memory=memory, container=data))
    assert len(valid) >= 2, 'Insufficient valid host observations'
    times = [r['sample']['gpu']['started_ns'] for r in valid]
    assert all(0 < b-a <= 5e9 for a, b in zip(times, times[1:])), 'Host observation gap exceeds 5 seconds'
    span = (times[-1]-valid[0]['sample']['gpu']['completed_ns'])/1e9
    assert span >= minimum_seconds, 'Release observation is shorter than required'
    return dict(valid=valid, missing=missing, seconds=span)


def full_load_spans(output, workers, clock, start, ready):
    events = [json.loads(line) for path in (output/'events').glob('*.jsonl')
              for line in path.read_text().splitlines()]
    full = [e for e in events if e['name'] in ('full_weight_load_begin', 'full_weight_load_end')]
    assert len(full) == 16 and {e['rank'] for e in full} == set(range(8))
    ranks, spans = {row['rank']: row for row in workers}, []
    for rank in range(8):
        begin = [e for e in full if e['rank'] == rank and e['name'].endswith('_begin')]
        end = [e for e in full if e['rank'] == rank and e['name'].endswith('_end')]
        assert len(begin) == len(end) == 1
        begin, end = begin[0], end[0]
        for row in (begin, end):
            assert row['clock_id'] == 'CLOCK_MONOTONIC'
            assert (row['boot_id'], row['time_namespace']) == clock
            for key in ('pid', 'process_start_ticks'):
                assert row[key] == ranks[rank]['identity'][key]
        assert start <= begin['monotonic_ns'] <= end['monotonic_ns'] <= ready
        assert begin['sequence'] < end['sequence']
        spans.append(dict(rank=rank, begin_ns=begin['monotonic_ns'], end_ns=end['monotonic_ns'],
                          seconds=(end['monotonic_ns']-begin['monotonic_ns'])/1e9))
    return spans


def qualify(root):
    data = read(root/'input.json')
    protocol = read(root/'protocol.json')
    assert protocol['vllm_commit'] == '132765e3560659ff63ebd236203672e991b70e08'
    assert hashlib.sha256((root/'input.json').read_bytes()).hexdigest() == protocol['input_sha256']
    assert data['scenario'] == 'sleep_l1'
    config = data['config']
    assert config['load_format'] == 'safetensors' and config['enable_sleep_mode']
    assert config['tensor_parallel_size'] == 8 and config['distributed_executor_backend'] == 'mp'
    output = root/'backend'
    constructed = read(output/'constructed.json')
    assert constructed['vllm'] == '0.20.1' and constructed['torch'] == '2.11.0+cu130'
    assert constructed['clock_identity']['clock_id'] == 'CLOCK_MONOTONIC'
    clock = tuple(constructed['clock_identity'][key] for key in ('boot_id', 'time_namespace'))
    receipts = {phase: read(output/('sleep-'+phase+'-receipt.json')) for phase in
                ('before', 'sleeping', 'sleeping-end', 'after')}
    for receipt in receipts.values():
        assert len(receipt['ranks']) == 8 and {row['rank'] for row in receipt['ranks']} == set(range(8))
        for row in receipt['ranks']:
            assert row['cuda_parameter_count'] > 0 and not row['uncovered_cuda_parameters']
            assert tuple(row['identity'][key] for key in ('boot_id', 'time_namespace')) == clock
    for phase in ('sleeping', 'sleeping-end', 'after'):
        validate_sleep_receipts(receipts['before']['ranks'], receipts[phase]['ranks'], sleeping=phase != 'after')
    checks = {phase: [check_response(item, read(output/(phase+'-'+item['name']+'-response.json'))['response'])
                      for item in data['requests']] for phase in ('before', 'after')}
    assert all(row['passed'] for rows in checks.values() for row in rows)
    passed = read(output/'backend-passed.json')
    assert passed['scenario'] == 'sleep_l1' and passed['normal_shutdown'] and passed['correctness']
    final = read(root/'backend-final.json')
    assert final['State']['ExitCode'] == 0 and not final['State']['Running']
    assert read(root/'backend-guard/result.json')['exit_code'] == 0
    exits = read(root/'workers-exited.json')
    assert exits['all_original_workers_gone'] is True and exits['workers'] == receipts['before']['ranks']
    assert read(root/'observer-complete.json')['reason'] == 'stop_marker'
    host = read(root/'backend-created.json')
    assert host['inspect']['Image'] == final['Image'] == protocol['image_digest']
    constraints = {key: host['inspect']['HostConfig'][key] for key in
                   ('NanoCpus', 'Memory', 'DeviceRequests', 'PidMode', 'PidsLimit', 'CpusetCpus')}
    assert constraints['PidMode'] == 'host'
    uuids = constraints['DeviceRequests'][0]['DeviceIDs']
    assert len(set(uuids)) == 8
    initial = read(root/'initial-resource.json')
    assert initial['store_port_closed'] and not initial['apps']
    initial_gpu = [line.split(',') for line in initial['gpu'].splitlines()]
    assert [row[1].strip() for row in initial_gpu] == uuids and all(
        int(row[2]) <= protocol.get('prelaunch_driver_baseline_mib', 16) for row in initial_gpu)
    ranks_by_uuid = {row['device_uuid']: row for row in receipts['before']['ranks']}
    assert set(ranks_by_uuid) == {str(uuid.UUID(value.removeprefix('GPU-'))) for value in uuids}
    samples = [json.loads(line) for line in (root/'resources.jsonl').read_text().splitlines()]
    sleep, wake = read(output/'sleep-returned.json'), read(output/'wake-returned.json')
    ready, shutdown = read(output/'ready.json')['ns'], read(output/'backend-shutdown-begin.json')['ns']
    load_spans = full_load_spans(output, receipts['before']['ranks'], clock, constructed['ns'], ready)
    begin, end = read(output/'sleep-observation-begin.json')['ns'], read(output/'sleep-observation-end.json')['ns']
    assert ready <= receipts['before']['started_ns'] <= sleep['started_ns'] <= sleep['returned_ns']
    assert sleep['returned_ns'] <= receipts['sleeping']['started_ns'] <= receipts['sleeping']['received_ns'] <= begin
    assert end-begin >= 75e9 and end <= receipts['sleeping-end']['started_ns'] <= receipts['sleeping-end']['received_ns'] <= wake['started_ns']
    assert wake['started_ns'] <= wake['returned_ns'] <= receipts['after']['started_ns'] <= receipts['after']['received_ns'] <= shutdown
    container = ('backend', host['id'], constraints)
    before = gpu_window(samples, ready, sleep['started_ns'], clock, uuids, container=container)
    during = gpu_window(samples, begin, end, clock, uuids, container=container, minimum_seconds=60)
    after = gpu_window(samples, wake['returned_ns'], shutdown, clock, uuids, container=container)
    released = []
    for rank in range(8):
        low_before = min(r['memory'][rank] for r in before['valid'])
        high_sleep = max(r['memory'][rank] for r in during['valid'])
        worker = ranks_by_uuid[str(uuid.UUID(uuids[rank].removeprefix('GPU-')))]
        expected_bytes = sum(row['bytes'] for row in worker['tags'].values())
        assert (low_before-high_sleep)*1024**2 >= expected_bytes*.9, 'Insufficient physical GPU release'
        assert min(r['memory'][rank] for r in after['valid']) > high_sleep
        released.append(low_before-high_sleep)
    release = read(root/'release-window.json')
    final_window = gpu_window(samples, release['begin_ns'], release['end_ns'], clock, uuids, minimum_seconds=60)
    assert all(not any(r['memory']) and not r['sample']['apps']['stdout'].strip() for r in final_window['valid'])
    return dict(qualified=True, model=data['name'], scope='one native full-model Sleep L1 case; not Store eviction',
                sleep_seconds=(sleep['returned_ns']-sleep['started_ns'])/1e9,
                wake_seconds=(wake['returned_ns']-wake['started_ns'])/1e9,
                cpu_backup_bytes=sum(row['tags']['weights']['cpu_backup_bytes'] for row in receipts['sleeping']['ranks']),
                own_store_pool_bytes_during_baseline=0,
                native_load_rank_spans=load_spans,
                released_mib_by_gpu=released, sleep_observed_seconds=during['seconds'], final_release_seconds=final_window['seconds'],
                missing_queries={phase: len(window['missing']) for phase, window in
                                 [('before', before), ('sleeping', during), ('after', after), ('final_release', final_window)]},
                sampled_cgroup_memory_current={phase: [int(r['container']['cgroup']['memory.current']) for r in window['valid']]
                                               for phase, window in [('before', before), ('sleeping', during), ('after', after)]},
                container_gpu_requests_unchanged=True, other_model_schedulability='not tested; native mp has no Ray resource-release result',
                memory_scope='cgroup current includes file cache; backup tensors freed at wake may remain in the host allocator cache',
                checks=checks)


if __name__ == '__main__':
    root = Path(sys.argv[1])
    try:
        result = qualify(root)
    except Exception as error:
        result = dict(qualified=False, error=repr(error), traceback=traceback.format_exc())
    with (root/'qualification.json').open('x') as stream:
        json.dump(result, stream, indent=2)
    raise SystemExit(0 if result['qualified'] else 1)
