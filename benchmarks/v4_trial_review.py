"""Read-only condition/lifecycle review; raw journals never award runtime success."""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from v4_trial import cache_observation, checked_json, CONDITIONS
from v4_evidence import cache_timing_checks, validate_business_event_order, release_coverage, require, validate_single_engine_requests
from v4_native_service import validate_response
from v4_native_reference import inputs
from v4_native_release import check_snapshot
from v4_results import replay
from sllm_store.event_trace import read_event_window
from sllm_store.v4_state import write_once

root=Path(__file__).resolve().parents[1]

def review_trial(base, protocol_path=None, protocol_sha=None, journal_root=None, expected_head=None, publish=True, amendment_path=None, amendment_sha=None):
    require(not sys.flags.optimize and not os.environ.get('PYTHONOPTIMIZE'), 'optimized review forbidden')
    b=Path(base);o=b/'output'
    assert not (b/'trial-cache-qualification-revoked.json').exists(), 'cache qualification was revoked'
    def read(p):return json.loads(p.read_bytes())
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    p=read(b/'preparation.json');spec=p['trial'];candidate=read(b/'candidate.json');cleanup=read(b/'cleanup.json')
    assert not (b/'failure.json').exists() and spec['formal_id_consumed'] == (journal_root is not None)
    assert candidate['container_exit_codes']==candidate['guard_exit_codes']==dict(head=0,worker=0)
    assert candidate['release_observation_passed'] and candidate['formal_id_consumed'] == spec['formal_id_consumed']
    assert cleanup['head']==cleanup['worker']==dict(stopped=True,removed=True) and cleanup['network_removed']
    release={}
    declared=read(b/'READY.json')['roles']
    release_jobs={}
    for role in ['head','worker']:
     final=read(b/(role+'-final.json'));assert final['State']['ExitCode']==0 and not final['State']['OOMKilled'] and not final['State']['Running'] and final['State']['Pid']==0
     identity={key:declared[role][key] for key in ('container_id','created','image')}
     assert final['Id']==identity['container_id'] and final['Created']==identity['created'] and final['Image']==identity['image']
     assert final['Name']=='/'+b.name+'-'+role and final['Config']['User']=='2147:2147'
     job=read(b/(role+'-release/job.json'));release_jobs[role]=job
     assert job['identity']==identity and job['purpose']=='formal-trial' and all(value==0 for value in job['baseline_gpu_mib'].values())
     rows=[json.loads(x) for x in (b/(role+'-release/samples.jsonl')).read_text().splitlines()]
     for row in rows:check_snapshot(row['host_gpu_snapshot'],job['baseline_gpu_mib'])
     release[role]=release_coverage(rows,job['delete_ack_ns'])['sample_span_seconds']
     assert read(b/(role+'-release/result.json'))['native_release_observation_passed']
     for n in [role+'-ray-stop.log']:
      text=(o/n).read_text();assert 'Stopped all ' in text and 'SIGKILL' not in text and 'forcefully' not in text
    start=read(o/'daemon-shutdown-started.json');end=read(o/'daemon-shutdown-finished.json');shutdown=(end['completed_ns']-start['begin_ns'])/1e9
    assert end['exit_code']==0 and shutdown<=start['timeout_seconds']==90
    ready=read(o/'trial-service-ready.json');startup=read(o/'trial-startup.json');record=startup['record'];clock=startup['cache_identity']
    inv=checked_json(b/'cache-inventory.json',spec['cache_inventory_sha256']);kind=CONDITIONS[spec['condition']][0];other='store_inputs' if kind=='safetensors_inputs' else 'safetensors_inputs'
    t,_,v=cache_observation(o/'cache-target',inv[kind]);s,_,w=cache_observation(o/'cache-other',inv[other]);del v,w
    cache_timing_checks(record,t,s,spec['condition'],spec['attempt_timeout_seconds'])
    for scan in [t,s]:assert all(scan['identity'][k]==v for k,v in clock.items())
    if spec['condition']=='store_pool_hit':
     r=ready['pool_native_receipt'];assert ready['pool_confirmed'] and len(r['receipts'])==len(set(r['receipts'].values()))==496
     assert all(isinstance(x,str) and x for x in r['receipts'].values())
     assert ready['pool_confirmation_complete_ns']<=r['captured_ns']<=ready['ready_ns']<=record['startup_command_ns']
     assert r['required_chunks']<=r['pool_usage_chunks']<=r['pool_capacity_chunks']==26552
    complete=read(o/'trial-http-complete.json');gold=checked_json(root/'docs/evidence/v4-native-reference-success/successful-run/native-gold.json','9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1')['responses'];checks={}
    for req in inputs(root):
     purpose=req['purpose'];raw=read(o/('trial-'+purpose+'.raw.json'));assert raw['status']==200
     body=base64.b64decode(raw['body_base64']);assert body.decode('utf8')==raw['body'];actual=json.loads(body)
     checks[purpose]=validate_response(actual,gold[purpose],req);assert actual['id']==spec['run_id']+'-'+purpose
     assert raw['received_ns']==complete['responses'][purpose]['received_ns']
     if purpose=='business':
      record.update(business_trigger_ns=raw['sent_ns'],correct_business_response_ns=raw['received_ns'])
      events=read_event_window(o/'events',spec['run_id'],record['startup_command_ns'],raw['received_ns'],boot_id=clock['boot_id'],time_namespace=clock['time_namespace'])
      validate_business_event_order(events,actual['id'],record['startup_command_ns'],raw['sent_ns'],raw['received_ns'])
      if spec['condition'].startswith('store_'):
       groups={rank:set() for rank in range(8)}
       for event in events:
        if event['name']=='grouped_expert_postprocess_host_end':groups[event['rank']].add(event['group_id'])
       assert all(x=={f'expert_{i:04d}' for i in range(61)} for x in groups.values())
    paired_end=read(o/'trial-workload32.raw.json')['received_ns']
    leased=spec['condition']=='safetensors_disk_hot' and spec.get('sf_hot_page_cache_lease',False)
    if leased:
     locked=read(o/'trial-cache-lease-locked.json');released=read(o/'trial-cache-lease-released.json')
     assert locked['state']=='locked' and released['state']=='released'
     assert locked['run_id']==released['run_id']==spec['run_id'] and locked['identity']==released['identity']
     assert locked['files']==len(inv[kind]) and locked['bytes']==sum(x['bytes'] for x in inv[kind].values())
     shared=set(inv['store_inputs']) & set(inv['safetensors_inputs'])
     exclusive={name:value for name,value in inv['store_inputs'].items() if name not in shared}
     pre,_,pv=cache_observation(o/'cache-prelease-store-cold',inv['store_inputs'])
     post,_,qv=cache_observation(o/'cache-locked-store-exclusive-cold',exclusive);del pv,qv
     assert pre['mode']=='cold' and post['mode']=='check' and all(x['resident_pages']==0 for x in post['files'])
     assert pre['verification_complete_ns']<=locked['begin_ns']<=locked['locked_ns']<=post['preparation_begin_ns']
     assert post['verification_complete_ns']<=t['preparation_begin_ns']
     assert all(scan['identity']['run_id']==spec['run_id'] and all(scan['identity'][key]==value
                for key,value in locked['identity'].items()) for scan in [pre,post])
     assert locked['prelease_store_files']==len(inv['store_inputs']) and locked['store_exclusive_files']==len(exclusive)
     assert locked['store_exclusive_cold'] is True and locked['store_shared_files']==len(shared)
     assert set(locked['store_shared_paths'])==shared and locked['store_shared_state']=='hot_after_lock_by_construction'
     assert locked['VmLck']==sum(x['pages']*x['page_size'] for x in t['files']) and released['VmLck']==0
     assert locked['begin_ns']<=locked['locked_ns']<=t['preparation_begin_ns']<=record['startup_command_ns']
     assert paired_end<=read(o/'trial-delete.raw.json')['received_ns']<=released['released_ns']<=read(o/'trial-host-finished.json')['finished_ns']
    paired_events=read_event_window(o/'events',spec['run_id'],record['startup_command_ns'],paired_end,boot_id=clock['boot_id'],time_namespace=clock['time_namespace'])
    paired=validate_single_engine_requests(paired_events,[spec['run_id']+'-business',spec['run_id']+'-workload32'])
    io=read(o/'trial-native-io.json');assert io['native_log_sha256']==sha(o/'trial-native-io.log')
    lines=(o/'trial-native-io.log').read_text().splitlines();n=sum('Loading model ' in x and ' size ' in x and ' to host' in x for x in lines);m=sum('Finished loading model ' in x and ' from disk' in x for x in lines)
    assert n==m==io['native_disk_load_starts']==io['native_disk_load_finishes']==(496 if spec['condition']=='store_disk_cold' else 0)
    report=dict(source_head=read(b/'GO.json')['source_head'],base=str(b),condition=spec['condition'],runtime_qualified=True,formal_id_consumed=spec['formal_id_consumed'],checks=checks,ranks=8 if spec['condition'].startswith('store_') else None,expert_groups_per_rank=61 if spec['condition'].startswith('store_') else None,native_disk_loads=n,release_seconds=release,normal_daemon_exit=True,daemon_shutdown_seconds=shutdown,total_elapsed_seconds=candidate['total_elapsed_seconds'],preparation_seconds=(record['preparation_complete_ns']-record['preparation_begin_ns'])/1e9,command_to_correct_response_seconds=(record['correct_business_response_ns']-record['startup_command_ns'])/1e9,business_trigger_to_correct_response_seconds=(record['correct_business_response_ns']-record['business_trigger_ns'])/1e9,scope='Fixed selected service cohort; exact condition/gold/events/normal exit/release only; no all-host cleanup claim',input_sha256={str(x.relative_to(b)):sha(x) for x in b.rglob('*') if x.is_file() and x.name!='runtime-review.json'})
    report.update(single_engine_warm32_verified=paired['single_engine_warm32'],keep_alive_seconds=spec.get('keep_alive_seconds',0))
    if leased:report['sf_hot_page_cache_lease_verified']=True
    if (o/'trial-warm-idle-probe.json').exists():
     probe=read(o/'trial-warm-idle-probe.json');report['idle_probe_seconds']=(probe['complete_ns']-probe['begin_ns'])/1e9
    if amendment_path is not None:
     amendment=checked_json(amendment_path,amendment_sha)
     assert amendment['base_preregistration_sha256']==spec['protocol_sha256'] and spec['amendment_sha256']==amendment_sha
     assert spec['keep_alive_seconds']==ready['registration']['auto_scaling_config']['keep_alive']==3600
     report['amendment_sha256']=amendment_sha
    assert report['source_head'] == p['source_head'] and (expected_head is None or report['source_head'] == expected_head)
    assert spec['run_id'] == b.name and report['total_elapsed_seconds'] < p['total_seconds'] == 3600
    assert complete['formal_id_consumed'] == spec['formal_id_consumed']
    full=(o/'store-daemon.log').read_bytes();native=(o/'trial-native-io.log').read_bytes();boundary=read(o/'trial-native-io-boundary.json')
    assert boundary['startup_command_ns'] == record['startup_command_ns'] and full[boundary['offset']:boundary['offset']+len(native)] == native
    if spec['condition'].startswith('safetensors_'):
     reclaim,_,vectors=cache_observation(o/'cache-reclaim',inv['store_inputs']);del vectors
     assert reclaim['mode']=='cold' and reclaim['cache_condition_passed'] is True
     if spec['condition']=='safetensors_disk_hot':
      reset,_,vectors=cache_observation(o/'cache-target-reset',inv['safetensors_inputs']);del vectors
      assert reset['mode']=='cold' and reset['cache_condition_passed'] is True
    if journal_root is not None:
     assert protocol_path is not None and protocol_sha == spec['protocol_sha256']
     protocol=checked_json(protocol_path,protocol_sha);assert protocol['frozen'] is True
     results,_,entries=replay(protocol_path,protocol_sha,[journal_root])
     index=next(i for i,row in enumerate(results) if row['attempt_id']==b.name)
     derived=results[index];entry=entries[index]
     assert derived['status']=='evidence_complete_unqualified' and not derived['missing_evidence']
     assert derived['code_commit']==report['source_head'] and derived['execution_run_id']==b.name
     assert derived['correct_business_response_ns']==record['correct_business_response_ns']
     assert derived['startup_command_ns']==record['startup_command_ns']
     assert derived['preparation_begin_ns']==p['preparation_begin_ns'] and derived['attempt_deadline_ns']==p['deadline_ns']
     assert all(job['delete_ack_ns']==derived['delete_complete_ns'] for job in release_jobs.values())
     assert derived['register_complete_ns']==read(o/'trial-register.raw.json')['received_ns']
     assert derived['delete_complete_ns']==read(o/'trial-delete.raw.json')['received_ns']==complete['delete_ack_ns']
     stages={row['stage']:row for row in entry['journal']}
     assert stages['workload32']['monotonic_ns']==read(o/'trial-workload32.raw.json')['received_ns']
     assert stages['release']['data']['rows']==[json.loads(x) for x in (b/'worker-release/samples.jsonl').read_text().splitlines()]
     report.update(attempt_id=b.name,block=derived['block'],metrics=derived['metrics'],success=True,
                   journal_source_sha256={row['path']:row['sha256'] for row in entry['journal_files']+entry['response_files']})
     report['command_to_correct_response_seconds']=derived['metrics']['command_to_correct_business_response_s']
    else:
     report.update(full_model_condition_pilot_qualified=True,success=False)
    report['reviewer_source_sha256']=sha(Path(__file__))
    if publish:
     write_once(b/'runtime-review.json',report)
    return report
