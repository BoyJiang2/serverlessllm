"""Frozen new40 statistics only; runtime qualification must be joined separately."""
import itertools
import copy
import math
import random
import statistics


def percentile(values, probability):
    ordered=sorted(values)
    if not ordered:return None
    position=(len(ordered)-1)*probability
    left=math.floor(position);right=math.ceil(position)
    return ordered[left]+(ordered[right]-ordered[left])*(position-left)


def describe(values):
    values=list(values)
    if not values:return dict(n=0,mean=None,median=None,p90=None)
    return dict(n=len(values),mean=statistics.fmean(values),median=statistics.median(values),
                p90=sorted(values)[math.ceil(.9*len(values))-1])


def wilson(successes,total):
    if total==0:return [None,None]
    z=1.959963984540054;p=successes/total;denominator=1+z*z/total
    center=(p+z*z/(2*total))/denominator
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0,center-half),min(1,center+half)]


def paired_effect(pairs,resamples=10000,seed=20261105):
    pairs=list(pairs)
    if any(baseline<=0 or target<=0 for baseline,target in pairs):
        raise ValueError('Qualified primary latencies must be positive')
    logs=[math.log(baseline/target) for baseline,target in pairs]
    if not logs:return dict(n=0,geometric_latency_ratio=None,bootstrap95=None,p_exact=None)
    observed=statistics.fmean(logs)
    result=dict(n=len(logs),geometric_latency_ratio=math.exp(observed),bootstrap95=None,p_exact=None)
    if len(logs)<2:return result
    rng=random.Random(seed)
    bootstrap=[math.exp(statistics.fmean(logs[rng.randrange(len(logs))] for _ in logs)) for _ in range(resamples)]
    result['bootstrap95']=[percentile(bootstrap,.025),percentile(bootstrap,.975)]
    extreme=sum(abs(statistics.fmean(sign*value for sign,value in zip(signs,logs)))>=abs(observed)-1e-14
                for signs in itertools.product((-1,1),repeat=len(logs)))
    result['p_exact']=extreme/(2**len(logs))
    return result


def holm(pvalues):
    adjusted=[None]*len(pvalues);maximum=0
    for rank,index in enumerate(sorted(range(len(pvalues)),key=lambda index:1 if pvalues[index] is None else pvalues[index])):
        maximum=max(maximum,min(1,(len(pvalues)-rank)*(1 if pvalues[index] is None else pvalues[index])))
        if pvalues[index] is not None:adjusted[index]=maximum
    return adjusted


def join_validated_reviews(rows,protocol,reviews,failures,protocol_sha):
    """Join independently revalidated reports; this function cannot validate GPU execution."""
    frozen={row['attempt_id']:row for row in protocol['attempts']};expected=set(frozen)
    actual=[row['attempt_id'] for row in rows]
    if len(frozen)!=len(protocol['attempts']) or len(actual)!=len(set(actual)) or set(actual)!=expected:
        raise ValueError('Derived rows must cover the frozen IDs exactly once')
    if not set(reviews)<=expected or not set(failures)<=expected or set(reviews)&set(failures):
        raise ValueError('Unknown or conflicting qualification inputs')
    primary=protocol['statistics']['primary_metric'];joined=[]
    for original in rows:
        row=copy.deepcopy(original);name=row['attempt_id'];review=reviews.get(name)
        if (row['block'],row['condition'])!=(frozen[name]['block'],frozen[name]['condition']):
            raise ValueError('Block/condition differs from the frozen ID')
        if row.get('preregistration_sha256') not in (None,protocol_sha):
            raise ValueError('Journal belongs to a different protocol revision')
        row.update(runtime_qualified=False,success=False,
                   protocol_binding_verified=row.get('preregistration_sha256')==protocol_sha)
        if name in failures:
            if not row['consumed'] or failures[name].get('counts_as_success') is not False:
                raise ValueError('Failure acknowledgement cannot promote or consume an ID')
            row.update(full_run_status='failed',runtime_failure_class=failures[name]['classification'])
        elif review is not None:
            if not (row['consumed'] and row['terminal'] and row['status']=='evidence_complete_unqualified'
                    and row['protocol_binding_verified']
                    and not row['missing_evidence'] and review['runtime_qualified'] is True
                    and review['success'] is True and review['formal_id_consumed'] is True
                    and review['attempt_id']==name and review['source_head']==row['code_commit']
                    and review['block']==row['block'] and review['condition']==row['condition']
                    and review['metrics']==row['metrics']):
                raise ValueError('Independent qualification disagrees with the raw journal replay')
            latency=row['metrics'][primary]
            if not math.isfinite(latency) or latency<=0:
                raise ValueError('Qualified primary latency must be finite and positive')
            row.update(runtime_qualified=True,success=True,full_run_status='qualified',
                control_amendment_sha256=review.get('amendment_sha256'),
                runtime_observations={key:review[key] for key in ('total_elapsed_seconds','preparation_seconds',
                    'daemon_shutdown_seconds','release_seconds') if key in review})
        else:
            row['full_run_status']=('unstarted' if not row['consumed'] else
                                    row['status'] if row['status'] in ('failed','timeout') else 'incomplete')
        joined.append(row)
    return joined


def sampled_resources(samples):
    """Per-role and per-device observed maxima; never sum maxima from different times."""
    roles={};gpu={};gpu_ids=None;count=0;previous=None;first=None;last=None;gap=0;window=0
    gpu_count=0;gpu_previous=None;gpu_first=None;gpu_end=None;gpu_gap=0;gpu_missing=[]
    for sample in samples:
        begin=sample['monotonic_ns'];end=sample['observation_end_ns']
        if end<begin or previous is not None and begin<=previous:
            raise ValueError('Telemetry clock order differs')
        if previous is not None:gap=max(gap,begin-previous)
        first=begin if first is None else first;last=end;previous=begin;count+=1;window=max(window,end-begin)
        for role,group in sample['cgroups'].items():
            item=roles.setdefault(role,dict(samples=0,memory_current_sampled_max_bytes=None,pids_current_sampled_max=None,
                                           memory_current_samples=0,pids_current_samples=0))
            item['samples']+=1
            for source,target in [('memory.current','memory_current_sampled_max_bytes'),('pids.current','pids_current_sampled_max')]:
                if group.get(source) is not None:
                    value=int(group[source])
                    if value<0:raise ValueError('Negative cgroup counter')
                    item[target]=value if item[target] is None else max(item[target],value)
                    item['memory_current_samples' if source=='memory.current' else 'pids_current_samples']+=1
        if sample.get('gpu') is None:
            gpu_missing.append(dict(sample_index=count-1,begin_ns=begin,end_ns=end,
                                    error=sample.get('gpu_observation_error')))
            continue
        if gpu_previous is not None:gpu_gap=max(gpu_gap,begin-gpu_previous)
        gpu_first=begin if gpu_first is None else gpu_first;gpu_previous=begin;gpu_end=end;gpu_count+=1
        seen=set()
        for row in sample['gpu']['rows']:
            uuid=row[1];used=int(row[4]);total=int(row[3])
            if uuid in seen or not 0<=used<=total:raise ValueError('Invalid GPU telemetry')
            seen.add(uuid);gpu[uuid]=max(gpu.get(uuid,0),used)
        if gpu_ids is not None and seen!=gpu_ids:raise ValueError('GPU coverage changed')
        gpu_ids=seen
    return dict(samples=count,sample_span_seconds=(last-first)/1e9 if count else None,
        maximum_start_gap_seconds=gap/1e9 if count>1 else None,
        maximum_observation_window_seconds=window/1e9 if count else None,
        roles=roles,gpu_per_device_sampled_max_mib=gpu,
        gpu_valid_samples=gpu_count,gpu_missing_samples=gpu_missing,
        maximum_gpu_start_gap_seconds=gpu_gap/1e9 if gpu_count>1 else None,
        initial_gpu_unobserved_seconds=(gpu_first-first)/1e9 if gpu_count else None,
        final_gpu_unobserved_seconds=(last-gpu_end)/1e9 if gpu_count else None,
        scope='Observed per-role memory.current and pids.current tasks (processes and threads), plus per-device GPU maxima; not CPU reservations, exact peaks or a simultaneous summed peak. Missing samples remain explicit and never become zeros. Cgroup memory includes charged file cache and is not the complete RSS or physical footprint of shared/locked files.')


def campaign_statistics(rows,protocol):
    primary=protocol['statistics']['primary_metric'];denominator=protocol['denominator_per_condition']
    conditions=sorted({entry['condition'] for entry in protocol['attempts']});summary={}
    by_pair={(row['block'],row['condition']):row for row in rows}
    if len(by_pair)!=len(rows):raise ValueError('Duplicate block/condition in derived rows')
    for condition in conditions:
        group=[row for row in rows if row['condition']==condition]
        qualified=[row for row in group if row.get('runtime_qualified') is True and row.get('success') is True]
        latencies=[row['metrics'][primary] for row in qualified]
        metrics=sorted({name for row in qualified for name in row['metrics']})
        summary[condition]=dict(denominator=denominator,started=sum(row['consumed'] for row in group),
            fully_qualified=len(qualified),failed_or_incomplete=sum(row['consumed'] for row in group)-len(qualified),
            timeouts=sum(row.get('full_run_status')=='timeout' for row in group),
            correct_business_responses_observed=sum(row.get('correct_business_response_observed') is True for row in group),
            runtime_failed=sum(row.get('full_run_status')=='failed' for row in group),
            unstarted=sum(not row['consumed'] for row in group),success_wilson95=wilson(len(qualified),denominator),
            primary_latency_s=describe(latencies),metrics={name:describe(row['metrics'][name] for row in qualified
                if row['metrics'].get(name) is not None) for name in metrics})
    contrasts=[]
    for baseline,target in protocol['statistics']['contrasts']:
        pairs=[];blocks=[]
        for block in range(1,denominator+1):
            left=by_pair.get((block,baseline));right=by_pair.get((block,target))
            if left and right and all(row.get('runtime_qualified') is True and row.get('success') is True for row in [left,right]):
                pairs.append((left['metrics'][primary],right['metrics'][primary]));blocks.append(block)
        effect=paired_effect(pairs,protocol['statistics']['bootstrap']['resamples'],protocol['statistics']['bootstrap']['seed'])
        contrasts.append(dict(baseline=baseline,target=target,paired_blocks=blocks,**effect))
    for contrast,adjusted in zip(contrasts,holm([row['p_exact'] for row in contrasts])):contrast['p_holm']=adjusted
    return dict(primary_metric=primary,conditions=summary,contrasts=contrasts,
        interpretation='Qualified-only conditional latency; every started failure retains the original denominator. Ratios describe fixed loader/cache/media paths, not an isolated code-only or cross-model causal effect.',
        bootstrap_percentile_method='linear; descriptive95percent, not multiplicity-adjusted',
        primary_test_family='four frozen contrasts, two-sided exact paired sign flips, Holm alpha0.05')


def self_test():
    assert describe(range(1,11))['p90']==9
    assert abs(wilson(10,10)[0]-.7224672001371107)<1e-12
    effect=paired_effect([(2,1)]*10)
    assert abs(effect['geometric_latency_ratio']-2)<1e-12 and abs(effect['bootstrap95'][0]-2)<1e-12
    assert effect['p_exact']==2/1024
    assert paired_effect([])['n']==0 and paired_effect([(2,1)])['p_exact'] is None
    assert all(abs(left-right)<1e-12 for left,right in zip(holm([.01,.04,.03,.2]),[.04,.09,.09,.2]))
    conditions=['sf_cold','sf_hot','store_cold','pool']
    protocol=dict(denominator_per_condition=10,attempts=[dict(condition=condition) for condition in conditions],
        statistics=dict(primary_metric='latency',contrasts=[['sf_cold','store_cold'],['sf_hot','pool']],bootstrap=dict(resamples=100,seed=20261105)))
    rows=[dict(block=block,condition=condition,consumed=True,runtime_qualified=True,success=True,metrics={'latency':index+1},correct_business_response_observed=True)
          for block in range(1,11) for index,condition in enumerate(conditions)]
    first=next(row for row in rows if row['block']==1 and row['condition']=='pool')
    first.update(runtime_qualified=False,success=False,full_run_status='failed')
    summary=campaign_statistics(rows,protocol)
    assert summary['conditions']['pool']['denominator']==10 and summary['conditions']['pool']['fully_qualified']==9
    assert summary['conditions']['pool']['runtime_failed']==1 and summary['contrasts'][1]['n']==9
    frozen=dict(attempts=[dict(attempt_id='cpu',block=1,condition='sf')],statistics=dict(primary_metric='latency'))
    raw=dict(attempt_id='cpu',block=1,condition='sf',consumed=True,terminal=True,
        status='evidence_complete_unqualified',missing_evidence=[],code_commit='a'*40,
        metrics={'latency':2},correct_business_response_observed=True,preregistration_sha256='1'*64)
    review=dict(attempt_id='cpu',block=1,condition='sf',runtime_qualified=True,success=True,
        formal_id_consumed=True,source_head='a'*40,metrics={'latency':2})
    assert join_validated_reviews([raw],frozen,{'cpu':review},{},'1'*64)[0]['success']
    failure=join_validated_reviews([raw],frozen,{},dict(cpu=dict(counts_as_success=False,classification='warm_engine')),'1'*64)[0]
    assert not failure['success'] and failure['correct_business_response_observed'] and failure['metrics']['latency']==2
    assert raw['status']=='evidence_complete_unqualified' and 'full_run_status' not in raw
    for bad in [dict(review,source_head='b'*40),dict(review,formal_id_consumed=False),dict(review,metrics={'latency':1})]:
        try:join_validated_reviews([raw],frozen,{'cpu':bad},{},'1'*64)
        except ValueError:pass
        else:raise AssertionError('Mismatched qualification accepted')
    timeout=dict(raw,status='timeout',metrics={},correct_business_response_observed=False)
    assert join_validated_reviews([timeout],frozen,{},{} ,'1'*64)[0]['full_run_status']=='timeout'
    for bad in [dict(raw,condition='other'),dict(raw,preregistration_sha256='2'*64)]:
        try:join_validated_reviews([bad],frozen,{},{} ,'1'*64)
        except ValueError:pass
        else:raise AssertionError('Foreign protocol or condition accepted')
    samples=[dict(monotonic_ns=index*1_000_000_000,observation_end_ns=index*1_000_000_000+100,
        cgroups={'worker':{'memory.current':str(memory),'pids.current':str(index+2),'memory.peak':None}},
        gpu={'rows':[['0','gpu','CPU fixture','100',str(memory)]]}) for index,memory in enumerate([10,20,5])]
    resource=sampled_resources(samples)
    assert resource['roles']['worker']['memory_current_sampled_max_bytes']==20
    assert resource['roles']['worker']['pids_current_sampled_max']==4
    assert resource['gpu_per_device_sampled_max_mib']=={'gpu':20}
    assert sampled_resources([])['sample_span_seconds'] is None
    missing=copy.deepcopy(samples);missing[1]['gpu']=None
    missing[1]['gpu_observation_error']='CPU fixture timeout';missing[1]['cgroups']['worker']['memory.current']=None
    sparse=sampled_resources(missing)
    assert sparse['gpu_valid_samples']==2 and len(sparse['gpu_missing_samples'])==1
    assert sparse['maximum_gpu_start_gap_seconds']==2 and sparse['gpu_per_device_sampled_max_mib']=={'gpu':10}
    assert sparse['roles']['worker']['memory_current_samples']==2 and sparse['roles']['worker']['samples']==3
    assert sparse['roles']['worker']['memory_current_sampled_max_bytes']==10
    absent=sampled_resources([missing[1]])
    assert absent['gpu_valid_samples']==0 and absent['gpu_per_device_sampled_max_mib']=={}
    assert absent['initial_gpu_unobserved_seconds'] is None
    changed=copy.deepcopy(samples);changed[-1]['gpu']['rows'][0][1]='other-gpu'
    try:sampled_resources(changed)
    except ValueError:pass
    else:raise AssertionError('Changed GPU coverage accepted')
    try:sampled_resources([samples[1],samples[0]])
    except ValueError:pass
    else:raise AssertionError('Out-of-order telemetry accepted')
    print('STATISTICS_SELF_TEST_OK; no model or runtime claim')


if __name__=='__main__':self_test()
