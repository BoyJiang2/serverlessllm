"""Frozen small-receipt postprocessing only. No model paths are opened."""
import copy, csv, hashlib, json, re, sys, time
from pathlib import Path


def need(value, message):
    if not value:raise ValueError(message)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def validate(c, inventory):
    need(c['read_complete'] is True and c['observation_complete'] is True and c['admitted'] is True and c['technical_status']=='COMPLETED','completed admission')
    need((c['read_cap_s'],c['query_cap_s'],c['start_lateness_tolerance_s'])==(1800,30,2),'fixed limits')
    need(c['read_phase_begin_ns']<=c['read_begin_ns']<=c['read_end_ns']<c['read_phase_begin_ns']+1800*10**9,'read deadline')
    reads=c['read_files'];by={r['path']:r for r in reads}
    need(len(reads)==len(by)==len(inventory)==84 and set(by)==set(inventory),'84 finite paths')
    previous=c['read_begin_ns']
    for r in reads:
        e=inventory[r['path']];identity={k:e['bytes'] if k=='size' else e[k] for k in ('dev','ino','size','mtime_ns','uid')}
        need(r['identity']==identity and r['read_bytes']==e['bytes'],'read identity/bytes')
        if e['bytes']:
            need(previous<=r['read_begin_ns']<=r['read_end_ns']<=c['read_end_ns'],'read order');previous=r['read_end_ns']
    need(previous==c['read_end_ns'] and sum(r['read_bytes'] for r in reads)==c['read_bytes'],'aggregate read endpoint/bytes')
    need([s['offset_s'] for s in c['sweeps']]==[0,30,60,90,120],'five fixed offsets')
    queries=[];totals=[]
    for s in c['sweeps']:
        scheduled=c['read_end_ns']+s['offset_s']*10**9
        need(s['scheduled_ns']==scheduled and scheduled<=s['attempted_ns']<=s['query_begin_ns']<=scheduled+2*10**9,'start anchor/tolerance')
        need(s['lateness_ns']==s['query_begin_ns']-scheduled and s['query_begin_ns']<=s['query_end_ns']<scheduled+30*10**9,'absolute round deadline')
        need(s['status']=='OBSERVED' and s['all_hot'] is True,'sweep observed/hot')
        need(len(s['files'])==84 and {r['path'] for r in s['files']}==set(by),'84 query rows')
        prev=s['query_begin_ns']
        for r in s['files']:
            read=by[r['path']]
            need(all(r[k]==read[k] for k in ('identity','read_bytes','read_begin_ns','read_end_ns')),'query identity/read binding')
            pages=(read['identity']['size']+4095)//4096
            need(r['pages']==r['resident_pages']==pages,'expected resident page count')
            need(prev<=r['query_begin_ns']<=r['query_end_ns']<=s['query_end_ns'],'per-file query order');prev=r['query_end_ns']
            t=r['mincore_trust'];need(t['effective_uid']==t['filesystem_uid']==0 and t['full_identity_uid_map'] is True and t['file_owner_uid']==r['identity']['uid'] and (t['file_owner_uid']==0 or t['cap_fowner'] is True),'mincore root trust')
            queries.append(dict(offset_s=s['offset_s'],scheduled_ns=scheduled,sweep_query_begin_ns=s['query_begin_ns'],sweep_query_end_ns=s['query_end_ns'],expected_pages=pages,**r))
        totals.append(dict(offset_s=s['offset_s'],pages=sum(r['pages'] for r in s['files']),resident_pages=sum(r['resident_pages'] for r in s['files']),start_lateness_s=s['lateness_ns']/1e9,query_duration_s=(s['query_end_ns']-s['query_begin_ns'])/1e9))
    return reads,queries,totals


def csv_write(path,rows,common):
    out=[]
    for row in rows:
        flat=dict(common)
        for k,v in row.items():
            if isinstance(v,dict):flat.update({k+'_'+a:b for a,b in v.items()})
            else:flat[k]=v
        out.append(flat)
    with path.open('x',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in out for k in r)));w.writeheader();w.writerows(out)


def meminfo(text):return {m.group(1):int(m.group(2))*1024 for line in text.splitlines() if (m:=re.search(r'(\w+):\s+(\d+) kB$',line))}


def main(root,inventory_path,out):
    load=lambda name:json.loads((root/name).read_text())
    manifest=load('EXPORT-MANIFEST.json');need(len(manifest['files'])==41,'41 source receipts')
    for e in manifest['files']:
        rel=Path(e['relative']);need(not rel.is_absolute() and '..' not in rel.parts,'bounded export path')
        p=root/rel;need(not p.is_symlink() and p.stat().st_size==e['bytes'] and sha(p)==e['sha256'],'export SHA/size')
    c=load('cache-observation-result.json');inventory=json.loads(inventory_path.read_text())
    reads,queries,totals=validate(c,{r['path']:r for r in inventory['roots']['safetensors']['files']})
    v=load('diagnostic-cache-validity.json');f=load('controller-final.json');end=load('runtime-terminal-release-confirmation-v1.json');event=load('event-cache_diagnostic_observations_complete.json')
    for x in load('cache-observation-callback-markers.json')+[event]:need(x['clock_identity']==c['clock_identity'] and x['pid']==c['pid'],'clock/producer identity')
    need(v['run_id']==manifest['run_id']==event['run_id'] and v['source_commit']==f['source_commit']==event['source_commit'] and v['protocol_sha256']==f['protocol_sha256']==event['protocol_sha256'],'run/source/protocol')
    need(event['monotonic_ns']>=c['sweeps'][-1]['query_end_ns'],'completion clock')
    need(v['diagnostic_only'] is True and v['primary_sample_qualified'] is False and v['cache_valid'] is True and f['status']=='DIAGNOSTIC_CACHE_ADMITTED_NORMAL_RELEASE60','diagnostic terminal')
    need(all(x in end['controller'] for x in ['MainPID=0','Result=success','ExecMainStatus=0']),'controller exit0')
    quiet={}
    for role in ('head','worker'):
        g=load('guard-'+role+'/guard-final.json');u=f['units'][role];cid=end['containers'][role]
        need(u['MainPID']=='0' and u['Result']=='success' and u['ExecMainStatus']=='0','guard unit exit0')
        need(g['cid']==cid['id']==f['cids'][role] and not g['failures'] and g['abort_monotonic'] is None and g['quiet_seconds']>=60,'guard normal release')
        need(cid['state']['ExitCode']==0 and cid['state']['Pid']==0 and cid['state']['Running'] is False and cid['cgroup_exists'] is False,'container normal exit/gone');quiet[role]=g['quiet_seconds']
    obs=load('observer/observer-final.json');quiet['observer']=obs['quiet_seconds']
    need(quiet['observer']>=60 and obs['monitor']['gaps']==0 and not end['gpu_processes'] and len(end['gpu_memory'])==8 and all(x.endswith(', 0') for x in end['gpu_memory']),'observer/post GPU zero')
    need(load('cleanup-result.json')['normal'] is True,'normal cleanup')
    for fault in ['identity','end','bytes']:
        bad=copy.deepcopy(c)
        if fault=='identity':bad['sweeps'][0]['files'][0]['identity']['ino']+=1
        elif fault=='end':bad['sweeps'][0]['query_end_ns']=bad['sweeps'][0]['scheduled_ns']+30*10**9
        else:bad['read_bytes']+=1
        try:validate(bad,{r['path']:r for r in inventory['roots']['safetensors']['files']})
        except ValueError:pass
        else:raise ValueError('negative selftest accepted '+fault)
    common={'run_id':manifest['run_id'],'pid':c['pid'],**c['clock_identity'],'source_record_sha256':sha(root/'cache-observation-result.json')}
    csv_write(out/'read-files-84.csv',reads,common);csv_write(out/'query-files-420.csv',queries,common)
    resource_path=root/'resource-metrics-view-v1.json';need(sha(resource_path)=='7ba7b3398e8e4fb0f10e112f515a66f0b030e14d47340f8e69cc6a6075196a68','resource view SHA');frames=json.loads(resource_path.read_text())['frames'];resource=[]
    owners = frames[0]['owners']
    for x in frames:
        need(x['owners'] == owners, 'same reader/Store identity across frames')
        need(x['Store_control']['config'] == {'chunk_size':67108864,'mem_pool_size':67108864} and x['Store_control']['model_activity_lines_observed'] is False, 'actual Store64MiB/no observed model activity')
        need(x['collection_begin_ns'] <= x['collection_end_ns'], 'resource collection clock')
        need(x['clock_identity']==c['clock_identity'] and x['producer_callback']['clock_identity']==c['clock_identity'],'resource clock')
        nodes={node:{k:v for k,v in meminfo(fields['meminfo']).items() if k in ['MemTotal','MemFree','FilePages','AnonPages','Mlocked']} for node,fields in x['numa'].items()}
        groups={}
        for who,ancestors in x['cgroup_ancestors'].items():
            rows=[{'path':a['path'],**{k:a.get(k) for k in ['memory.current','memory.max','memory.high','memory.pressure','memory.events','cpuset.cpus.effective','cpuset.mems.effective']}} for a in ancestors]
            groups[who]={'ancestors':rows,'effective_finite_memory_max_bytes':min((int(a['memory.max'].strip()) for a in ancestors if str(a.get('memory.max','')).strip().isdigit()),default=None),'effective_finite_memory_high_bytes':min((int(a['memory.high'].strip()) for a in ancestors if str(a.get('memory.high','')).strip().isdigit()),default=None),'current_scope':'memory.current retained separately at each ancestor, not summed or called a single effective current'}
        selected={scope:{k:v for k,v in values.items() if k.startswith(('pgscan','pgsteal','workingset','numa_')) or k in ['oom','oom_kill','high','max']} for scope,values in x['counter_deltas_from_previous_same_process_observation'].items()}
        resource.append({'label':x['producer_callback']['label'],'callback_ns':x['producer_callback']['monotonic_ns'],'collection_begin_ns':x['collection_begin_ns'],'collection_end_ns':x['collection_end_ns'],'raw_source_reference':x['raw_source_reference'],'owners':x['owners'],'numa_bytes':nodes,'host_memory_pressure':x['host']['pressure/memory'],'cgroup_ancestors':groups,'counter_deltas_from_previous_snapshot':selected,'Store_control':x['Store_control']})
    need(len(resource)==12,'12 resource snapshots')
    wall=(c['read_end_ns']-c['read_begin_ns'])/1e9
    result={'status':'EXISTING_RECEIPTS_RECOMPUTED_ACCEPT','created_epoch':time.time(),'run_id':manifest['run_id'],'diagnostic_only':True,'primary_sample_qualified':False,'new_formal_n':0,'model_registration':'NOT_PERFORMED','model_rank_ownership':None,'weight_load_host_all_rank_span':None,'read_files':len(reads),'query_rows':len(queries),'page_size_bytes':4096,'read_bytes':c['read_bytes'],'read_wall_s':wall,'host_buffered_read_bytes_per_second':c['read_bytes']/wall,'throughput_scope':'Host buffered preadv stream wall, including userspace/callback gaps; not physical disk I/O throughput.','sweeps':totals,'quiet_seconds':quiet,'controller_elapsed_s':f['elapsed'],'source_commit':f['source_commit'],'protocol_sha256':f['protocol_sha256'],'clock_identity':c['clock_identity'],'inputs':manifest['files'],'export_manifest_sha256':sha(root/'EXPORT-MANIFEST.json'),'inventory':{'path':str(inventory_path),'sha256':sha(inventory_path)},'resource_view':{'path':str(resource_path),'sha256':sha(resource_path)},'resource_snapshots':resource,'resource_units':{'numa_bytes':'sysfs kB multiplied by1024','memory.current/max/high':'bytes or literal max; limits checked across all recorded ancestors','PSI':'kernel original avg10/60/300 percentage and total microseconds','counter_deltas':'original sanitized view differences from preceding snapshot, first/missing/reset null; no rate/causality claim'},'selftests':'3 mutated actual-record validation cases rejected: identity/end boundary/byte sum','script_sha256':sha(Path(__file__)),'limitations':['Only frozen small receipts/inventory and sanitized view read; no fresh model stat, cache operations, GPU or physical I/O measurement.','Five sequential non-atomic client mincore snapshots are not continuous retention or server/disk cache evidence.','Counter changes, NUMA free/file pages and PSI do not establish causes; gauges not integrated as cost.','Raw-private OCI and large original telemetry not directly reverified; exported SHA provenance retained.']}
    resource_result={'run_id':manifest['run_id'],'view_sha256':sha(resource_path),'snapshots':resource,'units':result['resource_units'],'scope':'12 recorded matched-state observations only, no new sampling or causal attribution'}
    with (out/'resource-summary.json').open('x') as stream:json.dump(resource_result,stream,indent=2,sort_keys=True);stream.write('\n')
    result['resource_summary']={'path':str(out/'resource-summary.json'),'sha256':sha(out/'resource-summary.json')}
    result.pop('resource_snapshots')
    result['csv_outputs']=[{'path':str(out/name),'sha256':sha(out/name),'rows':n} for name,n in [('read-files-84.csv',84),('query-files-420.csv',420)]]
    with (out/'summary.json').open('x') as stream:json.dump(result,stream,indent=2,sort_keys=True);stream.write('\n')
    print(json.dumps({'summary':str(out/'summary.json'),'sha256':sha(out/'summary.json'),'script_sha256':sha(Path(__file__)),'read_wall_s':wall,'sweeps':totals},indent=2))


if __name__=='__main__':main(*(Path(x) for x in sys.argv[1:]))
