"""Reconcile C23 costs without adding overlapping phase intervals or erasing idle pools."""
import hashlib
import json
from pathlib import Path

from final_statistics import ROOT, RUN, PACKET, checked_json, describe, load_rows


def counters(text):
    return {key.rstrip(':'): int(value.split()[0]) for key, value in
            (line.split(None, 1) for line in text.splitlines() if line.strip() and not line.endswith('[rollup]'))}


def span(begin, end):
    assert type(begin) is int and type(end) is int and end >= begin
    return (end-begin)/1e9


def historical_monitor(stream, windows):
    """Stream archived telemetry only; peaks are sampled lower bounds, never GPU work."""
    import hashlib
    import json

    def bucket():
        return dict(samples=0, gpu_samples=0, missing_gpu=0, gpu_peak_MiB=[None]*8,
                    memory_current_peak_bytes=None, first_ns=None, last_ns=None,
                    max_gap_s=0, foreign_gpu_samples=0)

    targets = [dict(window, summary=bucket()) for window in windows]
    for w in targets:
        assert type(w['begin_ns']) is int and w['end_ns'] >= w['begin_ns']
    whole = bucket()
    digest = hashlib.sha256()
    size = 0
    identity = None
    malformed = []
    for number, raw in enumerate(stream, 1):
        digest.update(raw)
        size += len(raw)
        try:
            row = json.loads(raw)
        except ValueError:
            malformed.append(number)
            continue
        t = row['monotonic_ns']
        assert type(t) is int
        values = None
        if row.get('gpu'):
            parts = [line.split(',') for line in row['gpu'].splitlines()]
            assert len(parts) == 8 and [int(p[0]) for p in parts] == list(range(8))
            ids = [p[1].strip() for p in parts]
            if identity is None:
                identity = ids
            assert identity == ids
            values = [int(p[3].strip().split()[0]) for p in parts]
            assert all(v >= 0 for v in values)
        memory = row.get('cgroup_memory_current')
        assert memory is None or type(memory) is int and memory >= 0
        for b in [whole]+[w['summary'] for w in targets if w['begin_ns'] <= t <= w['end_ns']]:
            if b['last_ns'] is not None:
                assert t >= b['last_ns']
                b['max_gap_s'] = max(b['max_gap_s'], (t-b['last_ns'])/1e9)
            b['first_ns'] = t if b['first_ns'] is None else b['first_ns']
            b['last_ns'] = t
            b['samples'] += 1
            b['foreign_gpu_samples'] += bool(row.get('foreign_gpu_pids'))
            if memory is not None:
                b['memory_current_peak_bytes'] = max(b['memory_current_peak_bytes'] or 0, memory)
            if values is None:
                b['missing_gpu'] += 1
            else:
                b['gpu_samples'] += 1
                b['gpu_peak_MiB'] = [max(a if a is not None else 0, v)
                                     for a, v in zip(b['gpu_peak_MiB'], values)]
    return dict(bytes=size, sha256=digest.hexdigest(), gpu_uuids=identity,
                malformed_line_numbers=malformed, whole=whole, windows=targets,
                scope='Archived device-wide NVML observations and owned-slice memory.current; '
                      'non-simultaneous per-device peaks, no GPU query or model execution; '
                      'missing observations are not zero; memory.current includes file cache.')


def pss_summary(snapshot):
    fields = ('Rss', 'Pss', 'Locked', 'Pss_Shmem')
    available = [r for r in snapshot['processes'] if r.get('smaps_rollup') is not None]
    totals = {field+'_bytes': sum(counters(r['smaps_rollup'])[field]*1024 for r in available)
              for field in fields} if len(available) == len(snapshot['processes']) else None
    store = [r for r in available if r['pid'] == snapshot['store_pid']]
    assert len(store) <= 1
    return dict(process_count=len(snapshot['processes']), readable_processes=len(available),
                all_readable_sum=totals,
                store={field+'_bytes': counters(store[0]['smaps_rollup'])[field]*1024
                       for field in fields} if store else None,
                scope='Worker-container one snapshot; summed RSS double counts shared mappings. Linux Locked is not complete CUDA pinned-pool accounting.')


def main():
    index_path = PACKET/'formal-exact-index.json'
    index = checked_json(index_path, 'd73b0cd9a01fc320d05054417b5c553553981cd18fe4a768565a03b5db0bf3b0')
    files = {f['path']: f for f in index['files']}
    used = {}
    prefix = 'validation/controls/'+RUN+'/'

    def read(relative, jsonl=False):
        f = files[prefix+relative]
        p = PACKET/f['local_path']
        data = p.read_bytes()
        assert len(data) == f['bytes'] and hashlib.sha256(data).hexdigest() == f['sha256']
        used[f['path']] = dict(local_path=f['local_path'], bytes=f['bytes'], sha256=f['sha256'])
        return [json.loads(line) for line in data.splitlines()] if jsonl else json.loads(data)

    host = read('host-preparation.json')
    shared = read('service-bootstrap-formal13/shared-initialization.json')
    release = read('release60-allstopped.json')
    supervisor = read('formal13-supervisor-result.json')
    head = read('headformal13-supervisor-result.json')
    trials = []
    states = [r for r in load_rows() if r['execution_run_id'] == RUN]
    for state in states:
        base = 'formal-trials/'+state['attempt_id']+'/'
        row = read(base+'result.json')
        idle = read(base+'idle60.jsonl', True)
        pss = read(base+'pss.json')
        duration = span(idle[0]['monotonic_ns'], idle[-1]['monotonic_ns'])
        assert len(idle) == 61 and duration >= 60
        cpu_delta = counters(idle[-1]['cpu_stat'])['usage_usec']-counters(idle[0]['cpu_stat'])['usage_usec']
        assert cpu_delta >= 0
        memory = [counters(r['memory_stat']) for r in idle]
        trials.append(dict(attempt_id=row['attempt_id'], condition=row['condition'],
                           result_sha256=state['result_sha256'],
                           elapsed_preparation_begin_to_complete_s=span(row['preparation_begin_ns'], row['complete_ns']),
                           preparation_s=row['metrics']['preparation_s'],
                           startup_command_to_correct_business_s=row['metrics']['command_to_correct_business_response_s'],
                           delete_request_to_ack_s=span(row['delete_command_ns'], row['delete_complete_ns']),
                           idle_span_s=duration, idle_worker_cpu_seconds=cpu_delta/1e6,
                           idle_worker_average_cpu_cores=cpu_delta/1e6/duration,
                           idle_worker_pids_current_range=[min(int(r['pids_current']) for r in idle), max(int(r['pids_current']) for r in idle)],
                           idle_worker_memory_current_bytes=describe([int(r['memory_current']) for r in idle]),
                           idle_worker_anon_bytes=describe([r['anon'] for r in memory]),
                           idle_worker_shmem_bytes=describe([r['shmem'] for r in memory]),
                           idle_worker_file_bytes=describe([r['file'] for r in memory]),
                           idle_container_shm_used_bytes=describe([int(r['shm_used_bytes']) for r in idle]),
                           post_delete_gpu_MiB=row['post_delete_gpu_MiB'], pss_snapshot=pss_summary(pss)))
    assert len(trials) == 26 and shared['boot_id'] == host['boot_id'] == supervisor['boot_id']
    audit = checked_json(PACKET/'formal-raw-audit-summary.json')
    final_memory = counters(release[-1]['cgroup']['memory.stat'])
    output = dict(schema='sllm-c23-costs-v1', run_id=RUN, code_commit=supervisor['code_commit'],
                  scope='C23 actual costs only. Historical controls, conversion and prior batches remain separate and not zero.',
                  status='Candidate cost output; independent numerical and scope review required.',
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  exact_index_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(), inputs=used,
                  intervals_nonadditive=dict(host_preparation_s=span(host['begin_ns'],host['end_ns']),
                       seed_copy_s=span(host['copy_begin_ns'],host['copy_end_ns']),
                       host_preparation_end_to_formal_head_dispatch_s=span(host['end_ns'],head['dispatch_monotonic_ns']),
                       formal_common_initialization_s=shared['elapsed_seconds'],
                       formal_worker_supervisor_s=span(supervisor['begin_ns'],supervisor['end_ns']),
                       formal_head_supervisor_s=span(head['begin_ns'],head['end_ns']),
                       final_release_observation_s=span(release[0]['monotonic_ns'],release[-1]['monotonic_ns']),
                       host_preparation_begin_to_final_release_s=span(host['begin_ns'],release[-1]['monotonic_ns'])),
                  interval_warning='Overlapping wall intervals; do not sum. Host preparation to release is elapsed wall time, not a claim of continuous GPU occupancy. Pre-formal gap includes normal qualification, review/approval idle and dispatch.',
                  seed_copy=dict(bytes=host['cache_reused_bytes'], files=host['seed_files'], scope=host['compilation_cost_scope']),
                  common_pool=dict(configured_bytes=768799145984, retained_for_all_four_conditions=True, auxiliary_gpu_MiB=shared['auxiliary_gpu_MiB']),
                  sampled_cgroup_peak_bytes=audit['sampled_cgroup_peak_bytes'], kernel_peak_bytes=audit['kernel_peak_bytes'],
                  peak_gpu_MiB=audit['peak_gpu_MiB'], trials=trials,
                  final_release=dict(points=len(release), pids_current=int(release[-1]['cgroup']['pids.current']),
                       memory_current_bytes=int(release[-1]['cgroup']['memory.current']),
                       anon_bytes=final_memory['anon'], shmem_bytes=final_memory['shmem'],
                       file_bytes=final_memory['file'], scope='Owned slice. Reclaimable file cache and disk assets retained; no global drop_caches.'),
                  limitations=['Worker idle cgroup excludes head/host; complete deployment peak and final slice shown separately.',
                      'pids.current counts tasks including threads, not unique process count.',
                      'Linux Locked alone does not establish CUDA pinned bytes. Configured pool and actual pool proof are separate.',
                      'Disk asset bytes are reusable assets, not per-attempt new allocations. Historical cost consolidation pending.'])
    (ROOT/'results/c23-costs.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(dict(trials=len(trials), inputs=len(used), elapsed_wall_s=output['intervals_nonadditive']['host_preparation_begin_to_final_release_s']))


if __name__ == '__main__':
    main()
