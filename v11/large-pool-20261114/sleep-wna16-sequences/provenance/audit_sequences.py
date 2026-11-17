import argparse
import json
import os
import statistics
from pathlib import Path

from recompute_sleep import collect, final_release, seconds
from audit_wna16 import audit_layers

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
FIELDS = ('load_or_wake_weight_s', 'kv_allocation_s', 'kv_switch_s',
          'kv_release_to_service_ready_s', 'checkpoint_reload_s')
SEQUENCES = {'S1': 'KGDGKDK', 'S2': 'GDKDGKG', 'S3': 'DKGKDGD'}


def read(path):
    return json.loads(path.read_bytes())


def records(path):
    with path.open() as stream:
        for line in stream:
            yield json.loads(line)


def sample_window(run, start, end, current):
    if current:
        source = run / 'resource-samples.jsonl'
        samples = [(row['ns'], row['cgroup_memory']['stat']) for row in records(source)
                   if start <= row['ns'] <= end and row.get('cgroup_memory', {}).get('stat')]
        paths = [source]
    else:
        paths = sorted(run.glob('kimi-cgroup-samples.*.jsonl'))
        samples = [(row['end_ns'], row['stat']) for path in paths for row in records(path)
                   if start <= row['start_ns'] <= row['end_ns'] <= end and 'stat' in row]
    samples.sort(key=lambda item: item[0])
    if not samples:
        return dict(samples=0, anon_peak_bytes=None, refault_delta_pages=None, source=[])
    peak = max(stat['anon'] for _, stat in samples)
    first, last = samples[0], samples[-1]
    refault = (last[1]['workingset_refault_file'] - first[1]['workingset_refault_file']
               if len(samples) >= 2 else None)
    assert refault is None or refault >= 0
    return dict(samples=len(samples), anon_peak_bytes=peak, refault_delta_pages=refault,
                refault_page_bytes=os.sysconf('SC_PAGE_SIZE'),
                endpoint_offsets_ns=[first[0]-start, last[0]-end],
                source=[str(path) for path in paths])


def trace_rows(path):
    decoder = json.JSONDecoder()
    with path.open(errors='replace') as stream:
        for line in stream:
            payload = line.partition('KIMI_L2_TRACE ')[2]
            if payload.startswith('{'):
                yield decoder.raw_decode(payload)[0]


def kimi_run(run, raw, current=False):
    trace = list(trace_rows(run / 'L2/worker-K/worker.log')) if current else []
    result = {}
    for rec in raw['records']:
        if rec['model'] != 'K' or rec['stage']['kind'] != 'wake':
            continue
        path = run / 'L2/worker-K' / rec['stage']['phases_receipt']
        phase = read(path)
        start, end = (phase[k] for k in ('checkpoint_reload_begin_ns', 'checkpoint_reload_end_ns'))
        window = [row for row in trace if start <= row['ns'] <= end]
        completion = None
        if current:
            rank_counts = audit_layers(window, 'candidate')
            completion = dict(load=sum(x.get('load', 0) for x in rank_counts.values()),
                              finalize=sum(x.get('finalize', 0) for x in rank_counts.values()),
                              layer_events=window)
        result[rec['step']] = dict(checkpoint_reload_s=(end-start)/1e9, phase_receipt=str(path),
                                    layer_completion=completion,
                                    memory=sample_window(run, start, end, current))
    return result


def summarize(values):
    values = [x for x in values if x is not None]
    return dict(n=len(values), median=statistics.median(values) if values else None, values=values)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runs', type=Path)
    args = parser.parse_args()
    output, compare = [], []
    for run in sorted(args.runs.iterdir()):
        if not (run / 'qualification.json').exists():
            continue
        q = read(run / 'qualification.json')
        if not q['passed']:
            output.append(dict(run=run.name, sequence=q['sequence'], passed=False,
                               failures=q.get('failures')))
            continue
        raw, = q['results']
        cfg = read(run / 'campaign-input.json')
        first = raw['models'][0]
        cold = read(run / f'L2/worker-{first}/sf-admission/summary.json')
        assert cold['cache_condition_passed']
        images = {}
        for record in sorted((run/'L2').glob('*-created.json')):
            info = read(record)
            key = record.stem.split('-')[1]
            images[key] = info['inspect']['Image']
        assert len(images) == 3 and set(images.values()) == {cfg['expected_kimi_image']}
        seq = q['sequence']
        assert ''.join(raw['models']) == SEQUENCES[seq]
        assert raw['pid_continuity'] and raw['golds_passed']
        release = final_release(run, q)
        assert release['all_gpu_zero'] and release['meets_60s']
        steps = collect(run, raw)
        assert [x['stage'] for x in steps] == ['load']*3 + ['wake']*4
        imports = {key: read(run / f'L2/worker-{key}/worker-imports.json') for key in 'KGD'}
        assert all({x['rank'] for x in value} == set(range(8)) for value in imports.values())
        for key, receipts in imports.items():
            expected = (
                {row['target']: row['candidate_sha256'] for row in cfg['kimi_overlays']} if key == 'K'
                else {row['destination']: row['sha256'] for row in cfg['expected_v4_overlay']
                      if row['destination'].endswith('deepseek_v4.py')} if key == 'D'
                else {})
            if key == 'D':
                expected['/opt/sllm-v4/lib/python3.12/site-packages/vllm/model_executor/model_loader/reload/layerwise.py'] = cfg['candidate_overlay_sha256']
            for receipt in receipts:
                assert {row['path']: row['sha256'] for row in receipt['imports'].values()} == expected
        current = kimi_run(run, raw, current=True)
        source = (BASE / ('v11-formal-s3-rebind-c3bad7f7' if seq == 'S3'
                          else 'v11-sleep-readiness-20261116-aligned') / 'runs')
        before = {p.name: kimi_run(p, read(p/'qualification.json')['results'][0])
                  for p in sorted(source.glob(f'B2-{seq}-r*'))
                  if read(p/'qualification.json')['passed']}
        for step, row in current.items():
            compare.append(dict(sequence=seq, step=step, run=run.name, candidate=row,
                                baseline={name: val[step] for name, val in before.items()}))
        clean = [{key: step[key] for key in ('step','model','stage',*FIELDS[:-1])}
                 | dict(kv_ready_to_service_s=seconds(step['kv_ready_ns'], step['service_ready_ns']),
                        weight_begin_to_service_s=seconds(step['weight_begin_ns'], step['service_ready_ns']),
                        gold_s=(rec['t4_ns']-step['service_ready_ns'])/1e9,
                        gold_passed=all(check['passed'] for check in rec['checks'].values()),
                        checkpoint_reload_s=(step['wake_phases']['checkpoint_reload_end_ns']
                                              - step['wake_phases']['checkpoint_reload_begin_ns'])/1e9
                        if step['wake_phases'] else None)
                 for step, rec in zip(steps, raw['records'])]
        output.append(dict(run=run.name, sequence=seq, passed=True, steps=clean,
                           imports=imports, images=images, release=release))
    all_steps = [step for item in output if item['passed'] for step in item['steps']]
    stats = {f'{stage}-{key}': {field:summarize([row[field] for row in all_steps
                                               if row['stage']==stage and row['model']==key])
                               for field in FIELDS}
             for stage in ('load','wake') for key in 'KGD'}
    print(json.dumps(dict(rounds=output, stats=stats, kimi=compare), indent=2))


if __name__ == '__main__':
    main()
