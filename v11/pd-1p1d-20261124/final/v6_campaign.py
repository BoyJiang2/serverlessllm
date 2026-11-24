"""Verify the ordered v6 results and write their report."""
from argparse import ArgumentParser
import csv
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median

from phase1c_transition import check_receipt
from sample_memory import B1_MIN_AVAILABLE, LIMIT, b1_memory_policy, host_limit_ok
from v6_observe import release_report
from v6_plan import BASE, LOCAL, MATRIX, RUN, digest, module, read, sequence_steps
from v6_probe import check_response, metrics_delta, probe_request, validate_transfer
from v6_round import decode_reads, request_route, routes_for, stage_timing
from v6_sidecars import released_store_workers
from v6_switch import switch_metrics

RUNS = LOCAL['P']/'runs'
GIB = 1 << 30
MODELS = {'K':'Kimi-K2.6','G':'GLM-5.3','D':'DeepSeek-V4-Pro'}
ROLES = ('P','D')
TIMES = ('weights_s','checkpoint_reload_s','gpu_kv_alloc_s','worker_start_to_ready_s',
         'weights_rank_max_s','gpu_kv_alloc_rank_max_s')
GEOMETRY = ('max_model_len','kv_cache_memory_bytes','tensor_parallel_size')
FORMAL_MATRIX_SHA = '85a68a7d72a65be45e1291c2889c2c53347ff1cff6d109d70662e37c812c23ef'
ORIGINAL_MATRIX = Path(__file__).resolve().with_name('formal-matrix-original.json')
ORIGINAL_MATRIX_SHA = 'e3bc8c2558306faa35a4a96bf3020af4c419e2333ed811a87c70edc67935c251'
# 仅对这两轮保留实测的原矩阵与回执，正式序列仍按当前矩阵验证。
CLOSURE_PROFILES = {
    'PD-V6-B2-K-20261123T070827Z': dict(model='K', label='frozen-kimi',
        receipts={
            'plan-P.json':'9a9ff37a5f1368649c6463ef8a76811f357c06dea3cd3a03ac21da281a1e967b',
            'plan-D.json':'da28a74db70c6132263a9086b41b459a34964363bf26887ab3c8ce848f7697fc',
            'gpu-admission.json':'a7d3418853661b62a5a2f3c03e7af00192238005c9cdb93c48689da04da5de1a',
            'result.json':'e26af6976fb0c90cb2a44d3847e46ae826514dda5843b0d3877bd7d198cfd6c8',
        }),
    'PD-V6-B2-D-NUMA-4R-LONG-20261123T150838Z': dict(model='D', label='v4-numa-813-four-rail-32k',
        geometry=(32800,18*GIB,8), long_audit='v4-long-32k-post-wake/audit.json',
        receipts={
            'plan-P.json':'39b7ed2afb7ec6975afe805584de8f7e78a8a44124f18d84fd1c02cf870c3ccf',
            'plan-D.json':'e162c292ac5949bcab54c3899d2cdaba9204f05063ed7ca3521fbbd54aea8fb4',
            'gpu-admission.json':'504ea2f915b13469e6a4ce4a2903a3221924f29f76e2a6d1e2218039c2cfb857',
            'result.json':'63d8489456b014c9f6e9132f19cdffc0bac41ddfdc1c28065c6cda9111ce4901',
            'v4-long-32k-post-wake/audit.json':'b76f0fbe4014b70ca7d84edc3ebf3e1075d06ad4bd81dab55fe52fb5b2af8284',
        }),
}


def cases(through):
    if through not in ('B1','B2'):
        raise ValueError('expected B2 or B1')
    return [(baseline,sequence,closure) for baseline in (('B2',) if through=='B2' else ('B2','B1'))
            for sequence,closure in [('S1',m) for m in ('KDG' if baseline=='B2' else 'KGD')]
                                     + [('S1',None),('S2',None),('S3',None)]]


def expect(condition, message):
    if not condition:
        raise ValueError(message)


def file_ref(path):
    return dict(path=str(path),sha256=digest(path))


def contract(run):
    p,d = (read(run/f'plan-{r}.json') for r in ROLES)
    route,_ = routes_for(dict(P=p,D=d))
    expect(digest(MATRIX)==FORMAL_MATRIX_SHA, 'formal verification requires the frozen merged v6 matrix')
    profile = CLOSURE_PROFILES.get(run.name)
    matrix_path, matrix_sha = MATRIX, FORMAL_MATRIX_SHA
    if profile:
        expect((p['baseline'],p['sequence'],p['closure_model'])==('B2','S1',profile['model']),
               'archived closure identity differs')
        matrix_path, matrix_sha = ORIGINAL_MATRIX, ORIGINAL_MATRIX_SHA
        expect(digest(matrix_path)==matrix_sha, 'archived closure matrix changed')
        for relative, sha in profile['receipts'].items():
            expect(digest(run/relative)==sha, f'archived closure receipt changed: {relative}')
    frozen = read(matrix_path)
    expect((p['protocol'],p['run_id'],p['role'],d['role'],p['matrix_sha256'])
           ==('v6',run.name,'P','D',matrix_sha), 'run plan is not bound to its frozen v6 matrix')
    expect(p['baseline']==run.name.split('-')[2] and p['baseline'] in ('B2','B1')
           and p['weight_store_gib']==(0 if p['baseline']=='B2' else 864), 'baseline or weight pool differs')
    steps = sequence_steps(frozen,p['sequence'],p['baseline'],p['closure_model'])
    expect(steps==p['steps'], 'segment order or mode drifted')
    models = {row['model'] for row in steps}
    expect(set(p['workers'])==set(d['workers'])==models, 'worker model set differs from ordered steps')
    gsm_context=re.fullmatch(r'PD-V6-(B1|B2)-G-GSM4096-\d{8}T\d{6}Z',run.name)
    if gsm_context:
        expect((p['baseline'],p['sequence'],p['closure_model'])==(gsm_context.group(1),'S1','G'),
               'extended GSM8K context is scoped to the dedicated GLM closure')
    worker_sets = ('workers','store_workers') if p['baseline']=='B1' else ('workers',)
    if p['baseline']=='B1':
        expect(all(set(plan.get('store_workers',{}))==models for plan in (p,d)),
               'B1 Store worker model set differs from ordered steps')
    geometry = {}
    for model in sorted(models):
        declared = tuple(frozen['runtime'][model]['engine'][key] for key in GEOMETRY)
        expected = profile.get('geometry',declared) if profile else declared
        if gsm_context:
            expected = (4608,*declared[1:])
        for worker_set in worker_sets:
            actual = [tuple(plan[worker_set][model]['engine'][key] for key in GEOMETRY) for plan in (p,d)]
            expect(actual==[expected,expected], f'{model} P/D engine geometry differs from its frozen run scope ({worker_set})')
        geometry[model]=dict(zip(GEOMETRY,expected))
    provenance = dict(matrix=file_ref(matrix_path), geometry=geometry,
                      profile=profile['label'] if profile else 'formal-v6')
    if gsm_context:
        provenance['profile']='glm53-fixed50-context-4608'
        provenance['gsm8k_fixture']=file_ref(BASE/'configs/fixtures/gsm8k-fixed50/manifest.json')
    if profile and 'long_audit' in profile:
        path=run/profile['long_audit']
        raw=read(path)
        expect((raw['passed'],raw['context_passed'],raw['no_d_fallback'],raw['prompt_tokens'],raw['request_id'])
               ==(True,True,True,32768,f'{run.name}-LONG32768'), 'archived V4 long-context audit differs')
        provenance['long_context_audit']=file_ref(path)
    return p,steps,route,provenance


def setup_intervals(run, baseline):
    raw=read(run/'services-start.json')
    clock=read(run/'services-setup-clock.json')
    expect((clock['role'],clock['clock'])==('P','CLOCK_MONOTONIC') and
           0<clock['begin_ns']<clock['end_ns'], 'invalid preparation timing on P')
    roles={}
    expected={'P':['lmcache'],'D':[]}
    if baseline=='B1':
        expected={'P':['lmcache','store'],'D':['store']}
    for role in ROLES:
        response=raw[role]
        services=response['services']
        expect(response['ok'] is True and response['role']==role and set(services)==set(expected[role]),
               f'{role} sidecars do not match {baseline}')
        phases=[(services[k]['started_ns'],services[k]['ready_ns']) for k in expected[role]]
        expect(all(0<a<=b for a,b in phases) and all(a[1]<=b[0] for a,b in zip(phases,phases[1:])),
               f'{role} sidecar initialization order changed')
        if role=='P':
            expect(all(clock['begin_ns']<=a<=b<=clock['end_ns'] for a,b in phases),
                   'P sidecar events lie outside controller preparation')
        roles[role]={k:(t[1]-t[0])/1e9 for k,t in zip(expected[role],phases)}
    setup=dict(wall_s=(clock['end_ns']-clock['begin_ns'])/1e9,roles=roles,
               service_receipt=file_ref(run/'services-start.json'),clock_receipt=file_ref(run/'services-setup-clock.json'))
    if baseline=='B1':
        from v6_sidecars import store_pin_bytes

        stores={role:raw[role]['services']['store'] for role in ROLES}
        setup['weight_store_configured_pool_bytes'] = {role:stores[role]['store_pin']['configured_pool_bytes'] for role in ROLES}
        setup['weight_store_registered_pinned_bytes'] = {role:store_pin_bytes(stores[role],run.name,role) for role in ROLES}
    return setup


def checked_memory_minimum(run_id, row):
    # 已验收的 B2 沿用整机用量上限。B1 按两机各自的 MemAvailable 下限验收。
    if not b1_memory_policy(run_id):
        expect(row['host_peak_bytes'] <= LIMIT, 'invalid historical B2 host memory evidence')
        return None
    minimum = row['min_available_bytes']
    sample = row['min_available_sample']['host_bytes']
    expect(row['min_available_limit_bytes'] == B1_MIN_AVAILABLE
           and type(minimum) is int and sample['MemAvailable'] == minimum
           and host_limit_ok(run_id, sample), 'B1 safety floor evidence differs')
    return minimum


def audit_run(run):
    try:
        return read_run(run)
    except (OSError, KeyError, TypeError, AttributeError, IndexError, ValueError) as error:
        raise ValueError(f'{run.name}: {error}') from error


def checked_b1_interstage(run, stepdir, activation, old_model, adm, first_dispatch):
    release_path=stepdir/'interstage-release.json'
    release=read(release_path)
    expect((release['status'],release['comparison'])==('pass','owned_sidecars_only'),
           'B1 segment lacks the run-owned sidecar release check')
    gpus={r:set(adm['nodes'][r]['gpu_uuids']) for r in ROLES}
    expected_scopes={
        r:{f'/system.slice/docker-{ready["container_id"]}.scope':service
           for service,ready in row['services'].items()}
        for r,row in read(run/'services-start.json').items()
    }
    ownership=release['ownership']
    expect(released_store_workers(ownership,run.name,gpus)
           and all(ownership[r]['allowed_scopes']==expected_scopes[r] for r in ROLES),
           'B1 GPU ownership or LMCache unregister witness differs')
    stop_path=stepdir/'transition/old-stop.json'
    stopped=read(stop_path)
    expect(set(stopped)==set(ROLES), 'B1 segment lacks both old Worker stop receipts')
    ids={r:f'{run.name}-{r}-{old_model}' for r in ROLES}
    old_stop_refs={}
    for role in ROLES:
        raw=stepdir/f'stop-{role}.stdout'
        ack=check_receipt(stopped[role],role,ids,'stopped',0)
        expect(read(raw)==stopped[role] and ack['exit_code']==0 and ack['gone'] is True,
               f'{role} old Worker did not exit cleanly')
        old_stop_refs[role]=file_ref(raw)
    # capture_vram 为双端命令统一用 P 单调时钟打点。D 端退出仅验证身份及其本地端点。
    stopped_p=stopped['P']['receipt']
    expect(activation['router_drained_ns']<=stopped_p['started_ns']<=stopped_p['finished_ns'],
           'B1 P old Worker stop is outside the switch')
    setup_end=read(run/'services-setup-clock.json')['end_ns']
    reference={r:run/f'vram-sidecars-only-{r}.txt' for r in ROLES}
    attempts={}
    for role in ROLES:
        pattern=re.compile(rf'vram-old-release-(0|[1-9]\d*)-{role}\.txt')
        attempts[role]={int(match[1]):path for path in stepdir.glob(f'vram-old-release-*-{role}.txt')
                        if (match:=pattern.fullmatch(path.name))}
    expect(attempts['P'] and set(attempts['P'])==set(attempts['D']),
           'B1 VRAM attempts are missing one role')
    selected=max(attempts['P'])
    observed={r:attempts[r][selected] for r in ROLES}
    compute={r:stepdir/f'old-compute-{selected}-{r}.stdout' for r in ROLES}
    expect(all(read(compute[r])==ownership[r] for r in ROLES),
           'B1 ownership is not the paired last release inventory')
    for field,paths,begin,end in (
            ('reference',reference,setup_end,first_dispatch),
            ('observed',observed,stopped_p['finished_ns'],activation['target_begin_ns'])):
        snapshots=release[field]
        expect(set(snapshots)==set(ROLES), f'B1 {field} VRAM roles differ')
        for role,path in paths.items():
            pairs=[line.split(',',1) for line in path.read_text().splitlines()]
            memory={gpu.strip():int(used) for gpu,used in pairs}
            snap=snapshots[role]
            expect(len(pairs)==len(memory)==8 and set(memory)==gpus[role]
                   and all(used>=0 for used in memory.values()) and memory==snap['memory_used_mib']
                   and begin<=snap['started_ns']<=snap['finished_ns']<=end,
                   f'{role} B1 {field} VRAM or time window differs')
    return dict(receipt=file_ref(release_path),old_stop=file_ref(stop_path),old_stop_raw=old_stop_refs,
                reference_vram={r:file_ref(path) for r,path in reference.items()},
                observed_vram={r:file_ref(path) for r,path in observed.items()},
                ownership={r:file_ref(path) for r,path in compute.items()})


def read_run(run):
    path = run/'result.json'
    result = read(path)
    p,steps,route,provenance = contract(run)
    same = ('protocol','run_id','baseline','sequence','closure_model')
    expect(all(result[k]==p[k] for k in same) and result['passed'] is True and not result['errors']
           and result['expected_segments']==len(result['segments'])==len(steps), 'run did not pass in full')
    admission_path = run/'gpu-admission.json'
    adm = read(admission_path)
    expect((adm['protocol'],adm['run_id'],adm['matrix_sha256'],adm['user_notified_before_gpu'])
           ==('v6',run.name,p['matrix_sha256'],True) and set(adm['nodes'])==set(ROLES),
           'missing binding to the two-node GPU admission')
    expect(adm['handoff'] in ('prism','explicit_user_handoff') and
           (adm.get('handoff_sha256') if adm['handoff']=='prism' else adm.get('handoff_message')), 'missing takeover evidence')
    gpu_ids=set()
    for role in ROLES:
        node=adm['nodes'][role]
        uuids=node['gpu_uuids']
        expect(len(uuids)==len(set(uuids))==8 and not gpu_ids.intersection(uuids) and node['booking_id']
               and node['initial_memory_mib']==[0]*8 and node['initial_compute_pids']==[], 'expected sixteen admitted idle devices')
        gpu_ids.update(uuids)
    memory_path=run/'memory-both.json'
    memory=read(memory_path)
    expect(memory==result['memory'] and memory['passed'] is True and memory['run_id']==run.name
           and memory['host_kv_registered_pinned_bytes']=={'P':0,'D':0}, 'missing 813 GiB pool or memory qualification')
    begin,end=(memory['prefill'][f'window_{k}_wall_ns'] for k in ('begin','end'))
    peaks,minimums={},{}
    for role,key in zip(ROLES,('prefill','decode')):
        row=memory[key]
        expect(row['passed'] is True and row['role']==role and row['run_id']==run.name
               and (row['window_begin_wall_ns'],row['window_end_wall_ns'])==(begin,end)
               and 0<row['host_peak_bytes'], 'invalid host memory evidence')
        minimum=checked_memory_minimum(run.name,row)
        if minimum is not None:
            minimums[role]=minimum/GIB
        peaks[role]=row['host_peak_bytes']/GIB
    release_path=run/'release-both.json'
    release=read(release_path)
    expect(release==result['release'] and set(release['nodes'])==set(ROLES)
           and release==release_report(run,release['nodes'],run.name) and release['passed'] is True,
           'missing accepted 16-card release interval')
    last=max(release['nodes'][r]['after']['wall_ns'] for r in ROLES)
    expect(0<begin<end<=last and all(adm['nodes'][r]['booking_start_wall']*1e9<=begin
                                   and last<=adm['nodes'][r]['booking_end_wall']*1e9 for r in ROLES),
           'observations fall outside the admitted booking')
    setup=setup_intervals(run,p['baseline'])
    if p['baseline']=='B1':
        expect(all(memory[key]==setup[key] for key in ('weight_store_configured_pool_bytes',
                                                      'weight_store_registered_pinned_bytes')),
               'Store registered bytes differ from native ready witnesses')
    seen,segments=set(),[]
    for expected,segment in zip(steps,result['segments']):
        step,model,action=(expected[k] for k in ('step','model','action'))
        stepdir=run/f'{step:02d}-{model}'
        expect(all(segment[k]==v for k,v in expected.items()) and segment==read(stepdir/'segment-result.json'),
               f'segment {step} differs from its accepted receipt')
        transition=stepdir/'transition'
        activation=read(transition/'activation.json')
        expect((activation['target'],activation['previous'],activation['mode'])==(model,expected['old_model'],action),
               f'segment {step} activation identity differs')
        interstage=None
        if p['baseline']=='B1':
            if step==0:
                first_dispatch=activation['dispatch_ns']
            else:
                interstage=checked_b1_interstage(run,stepdir,activation,expected['old_model'],adm,first_dispatch)
        probe_dir=stepdir/'probe'
        probe=read(probe_dir/'probe.json')
        request_id=f'{run.name}-{step:02d}-{model}'
        request,spec=probe_request(model,route[model]['served_model_name'])
        response=read(probe_dir/'response.json')
        expect(probe['model']==model and probe['request_id']==request_id
               and read(probe_dir/'request.json')==dict(request_id=request_id,**request)
               and response['id']==probe['response_id']
               and check_response(model,spec,response)==probe['gold']==segment['gold'],
               f'segment {step} request or gold response differs')
        transfer=metrics_delta((probe_dir/'D-metrics-before.prom').read_text(),
                               (probe_dir/'D-metrics-after.prom').read_text())
        validate_transfer(transfer)
        expect(transfer==probe['transfer_metrics_delta'], f'segment {step} D metrics differ from raw scrapes')
        prefill=route[model]['prefill_engine_id']
        native_id,_=request_route(read(stepdir/'request-route.json'),probe,activation,prefill)
        trace=read(stepdir/'trace-D.stdout')
        rank_path=stepdir/'decode-read-ranks.json'
        # 两个基线都从该请求的 DONE 日志复核 GLM 的 MLA 与 indexer。
        glm_proof=model=='G'
        if glm_proof:
            native_trace=stepdir/'decode-read-native.log'
            expect(native_trace.read_text()==trace['log'], f'segment {step} native GLM READ trace differs')
        expect(trace['ok'] is True and read(rank_path)==
               decode_reads(trace['log'],prefill,request_id,native_id,require_glm_proof=glm_proof),
               f'segment {step} D READ evidence differs')
        host=read(stepdir/'host-kv.json')
        expect(host['passed'] is True and host['request_id']==request_id and host['engine_request_id']==native_id
               and host['host_hit_tokens']==(512 if model in seen else 0)
               and host['namespace']==route[model]['served_model_name'], f'segment {step} Host KV evidence differs')
        if step and expected['old_model']!=model:
            from v6_kv_evidence import FILES, check_lookup, cross_model_namespace, rows
            old=expected['old_model']
            body,_=probe_request(old,route[old]['served_model_name'])
            token_sha=hashlib.sha256(json.dumps(body['prompt'][:512],separators=(',', ':')).encode()).hexdigest()
            isolation=read(stepdir/'cross-model-namespace.json')
            previous,incoming=(isolation[k] for k in ('previous','active'))
            expected_lookups=(('previous',old,True),('active',model,False))
            for key,code,wanted in expected_lookups:
                item=isolation[key]
                receipt=read(stepdir/f'namespace-{code}.stdout')
                expect(item['receipt']==receipt and receipt['token_sha256']==token_sha
                       and receipt['namespace']==route[code]['served_model_name']
                       and re.fullmatch(r'v11-b2-namespace-[0-9a-f]{32}',receipt['request_id']),
                       f'segment {step} namespace client receipt differs')
                native,=[entry for entry in rows(run/'lmcache'/FILES['lookup'][0])
                         if entry['request_id']==receipt['request_id']]
                expect(item['server']==native and item['started_ns']<=native['ns']<=item['finished_ns'],
                       f'segment {step} namespace server event differs')
                check_lookup(native,receipt['namespace'],wanted)
            expect(isolation==cross_model_namespace(previous,incoming)
                   and previous['finished_ns']<=activation['dispatch_ns']<=activation['route_ready_ns']<=incoming['started_ns'],
                   f'segment {step} namespace lookups do not bracket model activation')
        if action=='load_safetensors_cold':
            cold=read(stepdir/'cold-admission.json')
            expect(cold['status']=='pass' and cold['condition']=='cold'
                   and all(cold[r]['run_id']==run.name and cold[r]['segment']==step+1 for r in ROLES),
                   f'segment {step} lacks paired cold page admission')
        timing=switch_metrics(activation,probe)
        role_timing=stage_timing(transition,action)
        expect(segment['timing']==timing and segment['role_stage_timing']==role_timing
               and all(math.isfinite(v) and v>=0 for v in timing.values())
               and all(math.isfinite(v) and v>=0 for role in ROLES for v in role_timing[role].values() if v is not None),
               f'segment {step} timing changed')
        expect(all(role_timing[r]['weights_s'] is not None and role_timing[r]['gpu_kv_alloc_s'] is not None
                   for r in ROLES), f'segment {step} has no complete native weights and KV timing for both nodes')
        seen.add(model)
        segments.append(dict(**expected,host_hit_tokens=host['host_hit_tokens'],rdma_bytes=transfer['rdma_bytes'],
                             decode_prompt_tokens={key:transfer[key] for key in
                                                   ('external_tokens','local_cache_tokens','computed_tokens')},
                             timing=timing,role_stage_timing=role_timing,receipt=file_ref(stepdir/'segment-result.json')))
        if glm_proof:
            segments[-1]['glm_read_evidence']=dict(trace=file_ref(native_trace),ranks=file_ref(rank_path))
        if interstage:
            segments[-1]['interstage_release']=interstage
    score=None
    if p['closure_model']=='G':
        scores=result['gsm8k']
        expect(scores==read(run/'gsm8k/result.json') and scores['total']==len(scores['rows'])==50
               and [row['row'] for row in scores['rows']]==list(range(50))
               and scores['correct']==sum(r['correct'] is True for r in scores['rows']),
               'GLM did not score the frozen 50-question set')
        if provenance['profile']=='glm53-fixed50-context-4608':
            replay_fixed50(run,route['G']['served_model_name'],scores)
        score=scores['correct']
    return dict(run_id=run.name,case=(p['baseline'],p['sequence'],p['closure_model']),
                receipt=file_ref(path),plans={r:file_ref(run/f'plan-{r}.json') for r in ROLES},
                admission=file_ref(admission_path),memory=file_ref(memory_path),release=file_ref(release_path),
                begin_wall_ns=begin,release_end_wall_ns=last,host_peak_gib=peaks,
                common_zero_seconds=release['common_zero_seconds'],handoff_eligible=release['handoff_eligible'],
                setup=setup,segments=segments,gsm8k_correct=score,provenance=provenance,
                **({'host_min_available_gib':minimums} if minimums else {}))


def replay_fixed50(run, model_name, scores):
    scorer=module('v6_fixed50_replay',BASE/'configs/fixtures/gsm8k-fixed50/score_gsm8k.py')
    scorer_fixture=scorer.load_fixture()
    for row,saved in zip(scorer_fixture['rows'],scores['rows'],strict=True):
        i=row['row']
        stem=run/'gsm8k'/f'{i:02d}'
        body=read(stem.with_name(f'{stem.name}-request.json'))
        response=read(stem.with_name(f'{stem.name}-response.json'))
        got=scorer.score(response,row)
        expect(body==scorer.request_for(scorer_fixture,row,model_name), f'GSM8K request {i} drifted')
        expect(got['reason']!='malformed_chat_response'
               and saved==dict(row=i,row_sha256=row['row_sha256'],
                               request_id=f'{run.name}-gsm8k-{i:02d}',**got),
               f'GSM8K score {i} differs from raw response')


def audit_campaign(run_ids, *, through='B2', runs=RUNS):
    expected=cases(through)
    expect(len(run_ids)==len(expected) and len(set(run_ids))==len(expected) and
           all(re.fullmatch(RUN,run_id) for run_id in run_ids), 'provide distinct v6 run IDs in campaign order')
    rows=[]
    last_release=0
    for run_id,key in zip(run_ids,expected):
        row=audit_run(runs/run_id)
        expect(row['case']==key, f'{run_id} is not the ordered {key} round')
        expect(row['begin_wall_ns']>=last_release, f'{run_id} started before the prior paired release completed')
        rows.append(row)
        last_release=row['release_end_wall_ns']
    return dict(protocol='v6',through=through,passed=True,formal_switches=18*(1 if through=='B2' else 2),
                matrix_sha256=digest(MATRIX),runs=rows)


def report(audit, out):
    through=audit['through']
    final=through=='B1'
    baselines=('B2','B1') if final else ('B2',)
    runs=audit['runs']
    expect(audit['passed'] is True and [r['case'] for r in runs]==cases(through)
           and len({r['run_id'] for r in runs})==len(runs)
           and audit['formal_switches']==18*len(baselines),
           'report requires all audited rounds in baseline order')
    title='# 三模型 1P+1D 切换验收' if final else '# B2 三模型 1P+1D 切换验收'
    scope='B2 使用 SafeTensors 冷读和原 Worker Sleep L2 唤醒。'
    if final:
        scope+='B1 首段 SafeTensors 冷读，后六段从双机 Store 恢复。'
    rows=[]
    lines=[title,'',
           'P 为 qjhs003，D 为 qjhs002。两端 TP8，P 端 Host KV 池为 813 GiB。',
           scope,'','## 三模型闭环','',
           '| 基线 | 模型 | Run ID | 实际上下文／GPU KV（GiB/rank） | P／D RAM 峰值（GiB） | 共同归零（秒） | GSM8K |',
           '|---|---|---|---|---:|---:|---:|']
    for run in audit['runs']:
        baseline,seq,closure=run['case']
        if closure:
            g='—' if run['gsm8k_correct'] is None else f'{run["gsm8k_correct"]}/50'
            peaks='／'.join(f'{run["host_peak_gib"][r]:.3f}' for r in ROLES)
            evidence=run['provenance']
            geometry=evidence['geometry'][closure]
            scope=f'{geometry["max_model_len"]}／{geometry["kv_cache_memory_bytes"]/GIB:g}'
            if 'long_context_audit' in evidence:
                scope += '（另验 32,768-token 回读）'
            lines.append(f'| {baseline} | {MODELS[closure]} | {run["run_id"]} | {scope} | {peaks} | {run["common_zero_seconds"]:.3f} | {g} |')
            continue
        for x in run['segments']:
            row=dict(baseline=baseline,sequence=seq,run_id=run['run_id'],segment=x['step'],
                     target=MODELS[x['model']],previous=MODELS.get(x['old_model']),action=x['action'],
                     **x['timing'],host_hit_tokens=x['host_hit_tokens'],rdma_bytes=x['rdma_bytes'],**x['decode_prompt_tokens'],
                     host_P_peak_gib=run['host_peak_gib']['P'],host_D_peak_gib=run['host_peak_gib']['D'],
                     end_common_zero_s=run['common_zero_seconds'],evidence=x['receipt']['path'])
            row.update({f'{r}_{k}':x['role_stage_timing'][r].get(k) for r in ROLES for k in TIMES})
            rows.append(row)
    lines.extend(['','## 常驻池准备',''])
    if final:
        lines.extend(['B1 的 P 端先初始化 LMCache，再初始化 Store。D 端 Store 与 P 端准备并行。'
                      '墙钟取 P 发出准备到双端就绪的实测跨度。','',
                      '| 基线 | 序列 | P LMCache（秒） | P Store（秒） | D Store（秒） | 准备墙钟（秒） |',
                      '|---|---|---:|---:|---:|---:|'])
    else:
        lines.extend(['P 端初始化 813 GiB LMCache。D 端 Host KV 为 0 GiB。墙钟从 P 发出准备计至双端就绪。','',
                      '| 基线 | 序列 | P LMCache（秒） | 准备墙钟（秒） |',
                      '|---|---|---:|---:|'])
    for run in runs:
        baseline,seq,closure=run['case']
        if closure:
            continue
        init=run['setup']
        p,d=(init['roles'][r] for r in ROLES)
        values=[p.get('lmcache')]
        if final:
            values.extend((p.get('store'),d.get('store')))
        values.append(init['wall_s'])
        fields=' | '.join('—' if v is None else f'{v:.3f}' for v in values)
        lines.append(f'| {baseline} | {seq} | {fields} |')
    if final:
        lines.extend(['','## B1 内存准入与实测最低可用内存','',
                      'B1 逐样本要求 P、D 的 MemAvailable 均不低于 50 GiB。'
                      'B2 沿用各节点 MemTotal − MemAvailable ≤ 1,750 GiB 的门槛。','',
                      '| Run ID | P 最低可用（GiB） | D 最低可用（GiB） |',
                      '|---|---:|---:|'])
        for run in runs:
            if run['case'][0]=='B1':
                low=run['host_min_available_gib']
                lines.append(f'| {run["run_id"]} | {low["P"]:.3f} | {low["D"]:.3f} |')
        lines.extend(['','## B1 权重 Store 注册页','',
                      '从固定原生二进制的分块注册和构造成功日志推得成功注册字节。按节点单列，不重复计入 cgroup。',
                      'Host KV 池仍采用独立的匿名页降级口径。','',
                      '| Run ID | P／D 配置（GiB） | P／D 成功注册（GiB） |',
                      '|---|---:|---:|'])
        for run in runs:
            if run['case'][0]=='B1':
                setup=run['setup']
                config,pinned=('／'.join(f'{setup[key][role]/GIB:g}' for role in ROLES)
                               for key in ('weight_store_configured_pool_bytes','weight_store_registered_pinned_bytes'))
                lines.append(f'| {run["run_id"]} | {config} | {pinned} |')
    lines.extend(['','## 首段独立冷启','',
                  '| 基线 | 序列 | 首模型 | T_total（秒） | T_net（秒） |','|---|---|---|---:|---:|'])
    lines.extend(f'| {r["baseline"]} | {r["sequence"]} | {r["target"]} | {r["total_s"]:.3f} | {r["net_s"]:.3f} |'
                 for r in rows if r['segment']==0)
    if final:
        lines.extend(['','B1 每次切换前复核旧 Worker 正常退出。'
                      '当次 GPU 计算进程只归属本轮常驻服务，LMCache GPU KV 注册和活动会话已清空。'
                      '逐段释放证据见索引。'])
    weight_paths='首次启动、原 Worker 回访和 Store 恢复' if final else '首次启动和原 Worker 回访'
    coverage='每个基线各 18 个正式切换段。' if final else 'B2 共 3 个首段和 18 个正式切换段。'
    lines.extend(['','## 正式切换','',
                  'S1 从 Kimi 开始，S2 从 GLM 开始，S3 从 V4 开始。'+coverage,
                  'T_total 从切换发出到正确完整响应。T_net 只扣除实际阻塞后续启动的释放观察等待。',
                  f'{weight_paths}分别记录八 rank 的权重和 GPU KV 张量分配包络及 rank 最大值。这些是主机端接点。P 与 D 的阶段各自计时。仅对适用的路径记录 checkpoint 重载。','',
                  '| 基线 | 序列 | 段 | 源模型 → 目标模型 | T_total（秒） | T_net（秒） | Host KV（token） |',
                  '|---|---|---:|---|---:|---:|---:|'])
    lines.extend(f'| {r["baseline"]} | {r["sequence"]} | {r["segment"]} | {r["previous"]} → {r["target"]} | '
                 f'{r["total_s"]:.3f} | {r["net_s"]:.3f} | {r["host_hit_tokens"]} |' for r in rows if r['segment'])
    for baseline in baselines:
        selected=[r for r in rows if r['baseline']==baseline and r['segment']]
        expect(len(selected)==18 and sum(r['baseline']==baseline and r['segment']==0 for r in rows)==3,
               f'{baseline} formal denominator changed')
        lines.extend(['',f'{baseline} 正式切换中位数。T_total 为 {median(r["total_s"] for r in selected):.3f} 秒，'
                         f'T_net 为 {median(r["net_s"] for r in selected):.3f} 秒，有效 n={len(selected)}。'])
    lines.extend(['','逐段数据见 [segments.csv](segments.csv)，原始回执索引见 [evidence-index.json](evidence-index.json)。',''])
    expect(len(rows)==21*len(baselines), 'formal denominator changed')
    out.mkdir(exist_ok=False)
    with (out/'segments.csv').open('x',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    name='FINAL-1P1D-S1S2S3-SUMMARY.md' if final else 'B2-1P1D-S1S2S3-SUMMARY.md'
    (out/name).write_text('\n'.join(lines),encoding='utf-8')
    (out/'evidence-index.json').write_text(json.dumps(audit,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def handoff_evidence(audit):
    expect(audit['through']=='B1' and audit['passed'] is True and audit['formal_switches']==36,
           'Prism handoff requires both complete baselines')
    final=audit['runs'][-1]
    expect(final['case']==('B1','S3',None) and final['handoff_eligible'],
           'final round must prove 16-card zero and Shmem < 1 GiB on both nodes')
    result_path=Path(final['receipt']['path'])
    run=result_path.parent
    release=read(run/'release-both.json')
    expect(file_ref(run/'release-both.json')==final['release'] and file_ref(result_path)==final['receipt'],
           'final release or result changed after audit')
    shmem={r:release['nodes'][r]['shmem_bytes'] for r in ROLES}
    expect(all(type(v) is int and 0<=v<GIB for v in shmem.values()), 'invalid final Shmem sample')
    stop=read(run/'cleanup/services-stop.json')
    expected={'P':{'lmcache','store'},'D':{'store'}}
    expect(set(stop)==set(ROLES) and all(
        stop[r]['ok'] is True and stop[r]['role']==r and stop[r]['action']=='stop'
        and stop[r]==read(run/f'cleanup/sidecar-stop-{r}.stdout')
        and set(stop[r]['services'])==expected[r]
        and all(item['gone'] is True for item in stop[r]['services'].values()) for r in ROLES
    ), 'final LMCache and Store shutdown evidence is incomplete')

    def stopped(rows):
        return set(rows)==set(ROLES) and all(row['ok'] is True and row['receipt']['role']==r
            and row['receipt']['stage']=='stopped' and row['receipt']['gone'] is True
            and row['receipt']['exit_code']==0 for r,row in rows.items())

    workers=read(result_path)['worker_cleanup']
    # B1 在每次切换时就删除旧 Worker，末尾只清理最后一个模型。
    expect(len(workers)==1 and workers[0]['model']==final['segments'][-1]['model']
           and stopped(workers[0]['roles']), 'final worker shutdown evidence is incomplete')
    old_stops=[run/f"{row['step']:02d}-{row['model']}"/'transition/old-stop.json'
               for row in final['segments'][1:]]
    expect(all(stopped(read(path)) for path in old_stops), 'B1 switch worker shutdown evidence is incomplete')
    keys=run/'cleanup/api-key-cleanup.json'
    expect(read(keys)=={r:dict(ok=True) for r in ROLES}, 'final API key cleanup evidence is incomplete')
    router=run/'router-exit.json'
    expect(read(router)['exited'] is True, 'final router shutdown evidence is incomplete')
    return dict(protocol='v6',from_project='pd-1p1d-baselines',to_project='prism-pd-1p1d',
                released=True,formal_switches=audit['formal_switches'],matrix_sha256=audit['matrix_sha256'],
                run_ids=[row['run_id'] for row in audit['runs']],last_run_id=final['run_id'],
                release_end_wall_ns=final['release_end_wall_ns'],common_zero_seconds=final['common_zero_seconds'],
                clock_offset_seconds=release['relative_clock_offset_seconds'],
                shmem_bytes=shmem,release=final['release'],worker_shutdown=final['receipt'],
                switch_worker_shutdown=[file_ref(path) for path in old_stops],
                sidecar_shutdown=file_ref(run/'cleanup/services-stop.json'),router_exit=file_ref(router),key_cleanup=file_ref(keys))


def main():
    cli=ArgumentParser(description=__doc__)
    cli.add_argument('run_ids',nargs='+')
    cli.add_argument('--through',choices=('B2','B1'),required=True)
    cli.add_argument('--out',type=Path)
    args=cli.parse_args()
    audit=audit_campaign(args.run_ids,through=args.through)
    if args.out:
        report(audit,args.out)
    print(json.dumps(dict(through=args.through,passed=True,formal_switches=audit['formal_switches'],
                          run_ids=args.run_ids,handoff=None)))


if __name__=='__main__':
    main()
