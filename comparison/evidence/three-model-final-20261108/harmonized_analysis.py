"""CPU-only endpoint arithmetic; function-scope admission stays explicit."""
import base64
import collections
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

BASE = Path(__file__).resolve().parent
INPUT = BASE / 'inputs/harmonized'
PHASES = ['weights_pipeline_including_allocation', 'weight_ingestion', 'quantization_postprocess']


def read(path):
    return json.loads(path.read_bytes())


def span(rows):
    if len(rows) != 8 or {r['rank'] for r in rows} != set(range(8)):
        return None, None
    if any(type(r['begin_ns']) is not int or type(r['end_ns']) is not int for r in rows):
        return None, None
    if any(r['end_ns'] < r['begin_ns'] for r in rows):
        raise ValueError('Reversed rank endpoints')
    return ((max(r['end_ns'] for r in rows)-min(r['begin_ns'] for r in rows))/1e9,
            max((r['end_ns']-r['begin_ns'])/1e9 for r in rows))


def write_csv(output, name, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with (output/name).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def export(output, primary, conditions):
    source = read(INPUT/'v4/report-boundaries-20261109.json')
    for item in read(INPUT/'glm/loading-boundary-sources.json')['files']:
        if 'base64' in item:
            assert hashlib.sha256(base64.b64decode(item['base64'])).hexdigest() == item['sha256']
    ranks = []
    attempts = []
    glm_endpoints = list(csv.DictReader((INPUT/'glm/loading-boundary-endpoints.csv').open(encoding='utf-8')))
    for attempt in sorted({r['attempt_id'] for r in glm_endpoints}):
        records = [r for r in glm_endpoints if r['attempt_id'] == attempt]
        for phase in PHASES:
            phase_data = source['glm'][attempt]['phases']['phases'][phase]
            pairs = []
            for record in records:
                rank = int(record['rank'])
                times = phase_data['ranks'][str(rank)]
                pair = dict(rank=rank, begin_ns=times['begin_ns'], end_ns=times['end_ns'])
                if phase == PHASES[0]:
                    assert int(record['begin_ns']) == pair['begin_ns'] and int(record['end_ns']) == pair['end_ns']
                pairs.append(pair)
                ranks.append(dict(model='GLM-5.1-FP8', cohort='C23_B5_B10_historical', attempt=attempt,
                    condition=records[0]['condition'], phase=phase, **pair,
                    host_seconds=(pair['end_ns']-pair['begin_ns'])/1e9,
                    pid=record['pid'], process_start_ticks=None, strict_common_boundary=False,
                    source_path=record['phases_path'], source_sha256=record['phases_sha256']))
            envelope, maximum = span(pairs)
            assert envelope is not None and math.isclose(envelope, phase_data['min_begin_to_max_end_seconds'], abs_tol=1e-8)
            attempts.append(dict(model='GLM-5.1-FP8', cohort='C23_B5_B10_historical', attempt=attempt,
                condition=records[0]['condition'], phase=phase, all_rank_span_s=envelope,
                max_rank_duration_s=maximum, comparison_selected=True, strict_common_boundary=False))
    qualified = {r['attempt_id']: r for r in read(BASE/'inputs/v4/results/qualified-rows.json') if r['success']}
    for attempt, data in source['v4'].items():
        for phase in PHASES:
            pairs = []
            for rank in range(8):
                begin = [e for e in data['events'] if e['resolved_rank'] == rank and e['name'] == phase+'_begin']
                end = [e for e in data['events'] if e['resolved_rank'] == rank and e['name'] == phase+'_end']
                assert len(begin) == len(end) == 1
                a, z = begin[0], end[0]
                for key in ['pid','process_start_ticks','container_hostname','boot_id','time_namespace','clock_id']:
                    assert a[key] == z[key]
                pair = dict(rank=rank, begin_ns=a['monotonic_ns'], end_ns=z['monotonic_ns'])
                pairs.append(pair)
                ranks.append(dict(model='DeepSeek-V4-Pro', cohort='NFS40_historical', attempt=attempt,
                    condition=data['condition'], phase=phase, **pair,
                    host_seconds=(pair['end_ns']-pair['begin_ns'])/1e9, pid=a['pid'],
                    process_start_ticks=a['process_start_ticks'], strict_common_boundary=False,
                    source_path=f'/home/boyjiang/sllm-v4-pro/evidence/{attempt}/output/events/'))
            envelope, maximum = span(pairs)
            assert math.isclose(envelope, qualified[attempt]['metrics']['phase_'+phase+'_span_s'], abs_tol=1e-8)
            attempts.append(dict(model='DeepSeek-V4-Pro', cohort='NFS40_historical', attempt=attempt,
                condition=data['condition'], phase=phase, all_rank_span_s=envelope,
                max_rank_duration_s=maximum, comparison_selected=data['block']>=2, strict_common_boundary=False))
    supplements = []
    early = read(INPUT/'kimi-earlier/results-v1.json')
    assert early['complete_block_n'] == 0
    for case in early['rows']:
        metric = case.get('measurement')
        pairs = ([dict(rank=r,begin_ns=None,end_ns=None) for r in range(8)] if metric is None
                 else [dict(rank=r['tp_rank'], begin_ns=r['begin_ns'], end_ns=r['end_ns']) for r in metric['ranks']])
        envelope, maximum = span(pairs)
        if metric:
            assert math.isclose(envelope,metric['weight_load_host_all_rank_span_s'],abs_tol=1e-8)
        supplements.append(dict(cohort='qjhs003_supplement_v1', condition=case['condition'],
            qualified=case['attempt_qualified'], complete_block_n=0, observed_rank_pairs=sum(r['begin_ns'] is not None for r in pairs),
            all_rank_span_s=envelope,max_rank_duration_s=maximum))
        for pair in pairs:
            ranks.append(dict(model='Kimi-K2.6',cohort='qjhs003_supplement_v1',attempt=case['run_id'],
                condition=case['condition'],phase='model_loader.load_model',**pair,
                host_seconds=None if pair['begin_ns'] is None else (pair['end_ns']-pair['begin_ns'])/1e9,
                strict_common_boundary=envelope is not None))
    for condition in conditions:
        data = read(INPUT/'kimi-formal64'/f'{condition}-endpoints.json')
        pairs = [dict(rank=r['expected_rank'],begin_ns=r.get('begin_ns'),end_ns=r.get('end_ns')) for r in data['rank_slots']]
        envelope, maximum = span(pairs)
        if envelope is not None:
            assert envelope == data['endpoint_formula']['span_s'] and maximum == data['endpoint_formula']['max_rank_duration_s']
        supplements.append(dict(cohort='qjhs000_formal64_supplement',condition=condition,
            qualified=data['condition_qualified'],complete_block_n=0,
            observed_rank_pairs=sum(r['begin_ns'] is not None for r in pairs),
            all_rank_span_s=envelope,max_rank_duration_s=maximum))
        for pair in pairs:
            ranks.append(dict(model='Kimi-K2.6',cohort='qjhs000_formal64_supplement',attempt=data['run_id'],
                condition=condition,phase='model_loader.load_model',**pair,
                host_seconds=None if pair['begin_ns'] is None else (pair['end_ns']-pair['begin_ns'])/1e9,
                strict_common_boundary=envelope is not None))
    write_csv(output,'rank_durations.csv',ranks)
    write_csv(output,'phase_attempts.csv',attempts)
    write_csv(output,'kimi_supplements.csv',supplements)
    phase_summary = []
    for model in ['GLM-5.1-FP8','DeepSeek-V4-Pro']:
        for condition in conditions:
            for phase in PHASES:
                selected = [r for r in attempts if r['model']==model and r['condition']==condition and r['phase']==phase and r['comparison_selected']]
                phase_summary.append(dict(model=model,condition=condition,phase=phase,n=len(selected),
                    observed_span_mean_s=statistics.fmean(r['all_rank_span_s'] for r in selected),
                    observed_max_rank_duration_mean_s=statistics.fmean(r['max_rank_duration_s'] for r in selected),
                    strict_common_valid_n=0,strict_common_mean_s=None))
    write_csv(output,'phase_summary.csv',phase_summary)
    prep = []
    kimi = {r['run_id']: r for r in read(INPUT/'kimi-source/controller-attempts.json')['rows']}
    for model, groups in primary.items():
        for condition, group in groups.items():
            for attempt,total in zip(group['ids'],group['prep_s'],strict=True):
                parts = dict(environment_s=None,pool_initialization_s=None,weight_registration_s=None,
                    target_cache_and_admission_s=None,admission_only_s=None,original_prep_total_s=total)
                if model=='Kimi-K2.6':
                    events = kimi[attempt]['event_monotonic_ns']
                    for key,a,z in [('environment_s','common_environment_begin','common_environment_complete'),
                                    ('pool_initialization_s','store_pool_initialization_begin','store_pool_initialization_complete')]:
                        parts[key]=(events[z]-events[a])/1e9 if events.get(a) is not None and events.get(z) is not None else None
                    parts['target_cache_and_admission_s']=total
                    scope='Environment/pool are outside original Prep; original Prep is combined target construction/admission, not an additive lifecycle decomposition.'
                elif model=='GLM-5.1-FP8':
                    parts['target_cache_and_admission_s']=total
                    scope='Shared environment/pool outside trial; original Prep may include prior-pool unload. No per-trial allocation of shared costs.'
                else:
                    scope='Original Prep combines environment/services/pool/registration/cache/admission; pure components absent.'
                prep.append(dict(model=model,condition=condition,attempt=attempt,**parts,scope=scope))
    write_csv(output,'preparation_components.csv',prep)
    v4_milestones=[]
    for attempt,data in source['v4'].items():
        if data['block']<2:continue
        p=data['preparation'];record=p['output/trial-startup.json']['record']
        points=[record['preparation_begin_ns'],p['output/store-configuration-checked.json']['checked_ns'],p['output/trial-cache-complete.json']['complete_ns'],record['startup_command_ns']]
        assert points==sorted(points)
        values=[(z-a)/1e9 for a,z in zip(points,points[1:])]
        assert math.isclose(sum(values),qualified[attempt]['metrics']['preparation_s'],abs_tol=1e-8)
        v4_milestones.append(dict(attempt=attempt,condition=data['condition'],
            origin_to_stack_pool_config_checked_s=values[0],config_checked_to_cache_complete_s=values[1],
            cache_complete_to_command_s=values[2],original_prep_total_s=sum(values),
            scope='Sequential observed milestones, not pure pool/registration/I-O costs; middle interval combines controls and admission.'))
    write_csv(output,'v4_preparation_milestones.csv',v4_milestones)
    random_rows = list(csv.DictReader((INPUT/'random/switches.csv').open(encoding='utf-8')))
    random_rows = [r for r in random_rows if r['phase']=='random']
    assert len(random_rows)==12
    directions=collections.Counter((r['from_model'],r['to_model']) for r in random_rows)
    assert len(directions)==6 and set(directions.values())=={2} and all(a!=z for a,z in directions)
    write_csv(output,'random_switches.csv',random_rows)
    random_summary=[]
    for direction,state in sorted({(r['from_model']+'->'+r['to_model'],r['effective_cache_state']) for r in random_rows}):
        group=[r for r in random_rows if r['from_model']+'->'+r['to_model']==direction and r['effective_cache_state']==state]
        times=[float(r['activation_to_correct_seconds']) for r in group]
        random_summary.append(dict(direction=direction,cache_state=state,n=len(group),
            effective_hit_payload_bytes=sum(int(r['hit_payload_bytes']) for r in group),
            file_reread_bytes=sum(int(r['store_file_read_bytes']) for r in group),
            allocated_blocks=sum(int(r['allocated_blocks']) for r in group),
            freed_blocks=sum(int(r['freed_blocks']) for r in group),
            median_activation_s=statistics.median(times),min_activation_s=min(times),max_activation_s=max(times)))
    write_csv(output,'random_direction_cache_summary.csv',random_summary)
    result=dict(primary_common_loading={m:{c:dict(n=0,mean_s=None,reason='Historical exact loader-call boundary is missing or not proven equivalent; no substitution from outer Worker or f7.') for c in conditions} for m in primary},
        phase_summary=phase_summary,kimi_supplements=supplements,
        historical_cohorts_unchanged=True,host_timing='No added CUDA synchronization; end is successful host return, not all-device completion.',
        counts=dict(glm_observed_attempts=24,v4_observed_attempts=38,rank_rows=len(ranks),random_switches=len(random_rows)))
    (output/'harmonized_metrics.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
