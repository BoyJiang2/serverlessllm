"""Postprocess frozen public small receipts only; no model file/runtime access."""
import copy, csv, hashlib, json, statistics, sys, time
from pathlib import Path


def need(ok, why):
    if not ok:raise ValueError(why)


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def write(p, value):
    with p.open('x') as f:json.dump(value,f,indent=2,sort_keys=True);f.write('\n')


def domain(c):return tuple(c[k] for k in ('boot_id','time_namespace','monotonic_clock_implementation'))


def endpoints(x, run_id):
    need(x['status']=='COMPLETE8_RANK_HOST_LOAD_CALL_SPAN' and x['run_id']==run_id,'complete host span identity')
    ranks=x['ranks'];need(len(ranks)==8 and sorted(r['tp_rank'] for r in ranks)==list(range(8)),'8 ranks')
    for key in ('pid','device_uuid'):
        need(len({r[key] for r in ranks})==8,'8 distinct '+key)
    need(len({r['host_owner']['pid'] for r in ranks})==8,'8 host PIDs')
    clock=domain(ranks[0]['clock_identity'])
    for r in ranks:
        need(r['schema']=='kimi-weight-load-host-endpoints-v1' and r['status']=='COMPLETE','rank endpoint schema')
        need(r['run_id']==run_id and r['model_instance_id']==x['model_instance_id'],'rank model/run identity')
        need(r['begin_boundary']=='before_model_loader_load_model' and r['end_boundary']=='after_model_loader_load_model_return_before_optional_runner_setup' and r['added_cuda_synchronization'] is False,'actual host boundary/no synchronization')
        need(domain(r['clock_identity'])==clock and r['clock_identity']['process_startticks']==r['host_owner']['startticks'],'rank clock/startticks')
        need(r['clock_identity']['container_hostname'] in r['host_owner']['cgroup'] and 'docker-' in r['host_owner']['cgroup'],'owner CID association')
        need(r['end_ns']>=r['begin_ns'] and r['host_duration_ns']==r['end_ns']-r['begin_ns'],'rank duration')
    begin=min(r['begin_ns'] for r in ranks);end=max(r['end_ns'] for r in ranks);maximum=max(r['end_ns']-r['begin_ns'] for r in ranks)
    need(x['begin_ns']==begin and x['end_ns']==end and x['span_ns']==x['weight_load_host_span_ns']==end-begin,'all-rank extrema')
    need(x['max_rank_weight_load_host_duration_ns']==maximum,'max single-rank duration')
    return {'begin_ns':begin,'end_ns':end,'span_ns':end-begin,'span_s':(end-begin)/1e9,'max_rank_duration_ns':maximum,'max_rank_duration_s':maximum/1e9,'clock_domain':clock}


def stats(values):return {'raw_array_s':values,'n':len(values),'mean_s':statistics.mean(values) if values else None,'median_s':statistics.median(values) if values else None}


def main(root, out):
    need(sha(root/'EXPORT-MANIFEST.json')=='4c7eaed3933603df657381b8679f8ae339be77334b6f3044c98d331c381d6840','export manifest SHA')
    need(sha(root/'RAW-REFERENCE-MANIFEST.json')=='c8227a701f068730e2512fbca25631662636de7a8303e46eb8d20f252e3b08ea','raw reference SHA')
    manifest=json.loads((root/'EXPORT-MANIFEST.json').read_text());need(len(manifest['files'])==126,'126 receipts')
    inputs=[]
    for item in manifest['files']:
        rel=Path(item['relative']);need(not rel.is_absolute() and '..' not in rel.parts,'finite public path')
        p=root/rel;need(not p.is_symlink() and p.stat().st_size==item['bytes'] and sha(p)==item['sha256'],'public copied bytes/SHA')
        inputs.append({'path':str(p),'sha256':item['sha256'],'bytes':item['bytes'],'original_source':item.get('source')})
    load=lambda name:json.loads((root/name).read_text())
    block=load('block-final.json');rows=[];attempts=[];metrics={};observed_records=[]
    need(len(block['attempts'])==4,'4 planned attempts')
    for a in block['attempts']:
        cond,run=a['condition'],a['run_id'];base=root/cond;spanpath=base/'unified-loading-host-span.json'
        result={'condition':cond,'run_id':run,'attempt_status':a['status'],'parent_block_status':block['status'],'source_commit':block['source_commit'],'protocol_sha256':block['protocol_sha256'],'endpoint_observed':spanpath.exists(),'condition_qualified':False,'normal_cleanup':False,'controller_pass':False,'complete_block_primary_eligible':False,'raw_endpoints':None,'endpoint_formula':None,'missing_reason':None}
        if not spanpath.exists():
            need(a['status']=='NOT_STARTED' and a['launch_requested'] is False,'missing observed endpoint unexpected')
            result['missing_reason']='NOT_STARTED; no endpoint recovery attempted'
            slots=[{'expected_rank':rank,'tp_rank':None,'begin_ns':None,'end_ns':None,'missing_reason':result['missing_reason']} for rank in range(8)]
        else:
            raw=load(cond+'/unified-loading-host-span.json');value=endpoints(raw,run);observed_records.append(raw)
            v=load(cond+'/sample-validity.json');cleanup=load(cond+'/cleanup-result.json');terminal=load(cond+'/condition-terminal.json')
            controller_name='controller-final.json' if (base/'controller-final.json').exists() else 'controller-failed.json';controller=load(cond+'/'+controller_name)
            need(raw['source_commit']==v['source_commit']==controller['source_commit']==block['source_commit'] and raw['protocol_sha256']==v['protocol_sha256']==controller['protocol_sha256']==block['protocol_sha256'],'source/protocol chain')
            need(v['run_id']==cleanup['run_id']==terminal['run_id']==run,'condition run chain')
            qualified=v['primary_sample_qualified'] is True
            result.update(condition_qualified=qualified,normal_cleanup=cleanup['normal'],controller_pass=controller_name=='controller-final.json',raw_endpoints=raw,endpoint_formula=value,endpoint_input={'path':str(spanpath),'sha256':sha(spanpath)},controller_observed_elapsed_s=controller['elapsed'],controller_status=controller['status'],cleanup_status=cleanup['status'],qualification_invalid_reasons=v['invalid_reasons'])
            need(not qualified or (cleanup['normal'] is True and controller_name=='controller-final.json'),'qualified cleanup/controller contradiction')
            slots=[{'expected_rank':rank,**next(r for r in raw['ranks'] if r['tp_rank']==rank)} for rank in range(8)]
            m=load(cond+'/sample-metrics.json');req={r['case']:r for r in m['requests']};arith=req['arithmetic'];t=arith['producer_times']
            begin=load(cond+'/event-command_register_begin.json');done=load(cond+'/event-arithmetic_http_complete_correct.json')
            need(begin['run_id']==done['run_id']==run and begin['clock_identity']==done['clock_identity']==arith['clock_identity'],'HTTP producer event identity')
            need(begin['monotonic_ns']==t['registration_command_ns'] and done['monotonic_ns']==t['received_ns'] and done['primary_A_ns']==arith['primary_A_ns']==t['received_ns']-t['registration_command_ns'],'HTTP endpoint A crosscheck')
            prep_begin=load(cond+'/event-target_condition_construction_begin.json');prep_end=load(cond+'/event-cache_pool_admission_complete.json')
            need(domain(prep_begin['clock_identity'])==domain(prep_end['clock_identity'])==domain(arith['clock_identity']),'Prep/HTTP domain')
            prep=prep_end['monotonic_ns']-prep_begin['monotonic_ns'];gap=t['registration_command_ns']-prep_end['monotonic_ns'];A=arith['primary_A_ns']
            common_begin=load(cond+'/event-common_environment_begin.json')
            releasepath=base/'event-release_quiet60_complete.json'
            release=load(cond+'/event-release_quiet60_complete.json') if releasepath.exists() else None
            metrics[cond]={'observation_only_when_failed':not qualified,'Prep_s':prep/1e9,'A_s':A/1e9,'Gap_s':gap/1e9,'Prep_plus_A_s':(prep+A)/1e9,'contiguous_Prep_Gap_A_s':(prep+gap+A)/1e9,'B_arithmetic_http_s':arith['business_B_ns']/1e9,'D_ready_fixed32_whole_http_s':req['fixed32']['business_B_ns']/1e9,'producer_times_arithmetic':t,'HTTP_clock_identity':arith['clock_identity'],'controller_common_environment_begin_ns':common_begin['monotonic_ns'],'controller_release_quiet60_complete_ns':release['monotonic_ns'] if release else None,'controller_common_begin_to_release_event_s':(release['monotonic_ns']-common_begin['monotonic_ns'])/1e9 if release else None,'controller_elapsed_s':controller['elapsed'],'scope':'Prep target cache/pool construction only; A register-to-arithmetic parsed/context-closed receive; neither Prep+A nor host loader span is total lifecycle; D whole ready HTTP, not pure decode/TTFT. Failed run elapsed ends in failure before abort release; missing normal release endpoint remains null.'}
        result['rank_slots']=slots
        for slot in slots:
            row={'run_id':run,'condition':cond,'expected_rank':slot['expected_rank'],'observed_tp_rank':slot.get('tp_rank'),'condition_qualified':result['condition_qualified'],'complete_block_primary_eligible':False,'source_endpoint_sha256':result.get('endpoint_input',{}).get('sha256'),'missing_reason':slot.get('missing_reason')}
            for key in ['begin_ns','end_ns','pid','device_uuid','loader_type','model','model_instance_id','run_id','added_cuda_synchronization','begin_boundary','end_boundary']:
                row['endpoint_'+key]=slot.get(key)
            for key,value in slot.get('host_owner',{}).items():row['owner_'+key]=value
            for key,value in slot.get('clock_identity',{}).items():row['clock_'+key]=value
            row['host_duration_ns']=slot['end_ns']-slot['begin_ns'] if slot.get('end_ns') is not None else None
            rows.append(row)
        attempts.append(result)
    complete=block['status']=='FOUR_CONDITION_BLOCK_RELEASE60_PASS' and all(a['condition_qualified'] and a['normal_cleanup'] and a['controller_pass'] and a['endpoint_observed'] for a in attempts)
    need(not complete,'this retained block must remain excluded')
    for fault in ['startticks','duration']:
        bad=copy.deepcopy(observed_records[0])
        if fault=='startticks':bad['ranks'][0]['clock_identity']['process_startticks']+=1
        else:bad['span_ns']+=1
        try:endpoints(bad,bad['run_id'])
        except ValueError:pass
        else:raise ValueError('negative endpoint selftest accepted '+fault)
    for a in attempts:write(out/(a['condition']+'-endpoints.json'),a)
    columns=list(dict.fromkeys(k for row in rows for k in row))
    with (out/'all-32-rank-slots.csv').open('x',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=columns);w.writeheader();w.writerows(rows)
    groups={}
    for a in attempts:
        vals=[a['endpoint_formula']['span_s']] if a['endpoint_observed'] else []
        groups[a['condition']]={'complete_block_primary':stats([]),'condition_qualified_descriptive':stats(vals if a['condition_qualified'] else []),'failed_condition_special_observation':stats(vals if a['endpoint_observed'] and not a['condition_qualified'] else []),'all_observed_descriptive':stats(vals),'status':a['attempt_status'],'missing_reason':a['missing_reason']}
    summary={'status':'PARTIAL_BLOCK_RETAINED_COMPLETE_BLOCK_N0','created_epoch':time.time(),'block_id':block['block_id'],'original_parent_status':block['status'],'complete_block_n':0,'complete_block_primary_eligible':False,'observed_condition_count':2,'qualified_condition_count':1,'failed_observation_count':1,'not_started_count':2,'condition_groups':groups,'timing_context':metrics,'primary_metric_formula':'(max_rank(end_ns)-min_rank(begin_ns))/1e9; max single-rank duration separately retained','measurement_boundary':'Host full model_loader.load_model call before fresh construction/storage through return, without added CUDA synchronization and before optional runner setup. Not pure kernel or disk I/O.','old_original40_modified':False,'old_primary8_blocks_modified':False,'old_primary_loading_endpoint_n':0,'old_ID_endpoint_imputation':False,'source_commit':block['source_commit'],'protocol_sha256':block['protocol_sha256'],'inputs':inputs,'export_manifest_sha256':sha(root/'EXPORT-MANIFEST.json'),'raw_reference_manifest_sha256':sha(root/'RAW-REFERENCE-MANIFEST.json'),'script_sha256':sha(Path(__file__)),'selftests':'actual16 endpoint rows validated; startticks corruption and extrema corruption rejected; failed parent enforced complete-block n0','limitations':['Physical ownership/clock authentication inherited from hashed public consumer output and validity gates; root-private full16records/NVML/OCI not re-read.','Storecold endpoint is failed-condition observation only; no complete-block comparative mean or ratio is inferred.','Both unstarted conditions retain 8 null rank slots; no old-run substitution.','Normal cleanup failure differs from subsequent abort release; original failed condition is never promoted.']}
    write(out/'summary.json',summary)
    artifacts=[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(out.iterdir()) if p.is_file()]
    write(out/'ARTIFACT-MANIFEST.json',{'status':'CPU_FROZEN_SMALL_RECEIPTS_ONLY','files':artifacts,'complete_block_n':0,'old_datasets_unchanged':True})
    print(json.dumps({'summary_sha256':sha(out/'summary.json'),'manifest_sha256':sha(out/'ARTIFACT-MANIFEST.json'),'script_sha256':sha(Path(__file__)),'observed_spans_s':{a['condition']:a['endpoint_formula']['span_s'] if a['endpoint_formula'] else None for a in attempts}},indent=2))


if __name__=='__main__':main(Path(sys.argv[1]),Path(sys.argv[2]))
