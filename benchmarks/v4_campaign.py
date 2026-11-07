"""Linear new40 execution through the existing one-condition runner; stop on any failed gate."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from sllm_store.v4_state import write_once
from v4_attempts import collect_attempts, frozen_protocol
from v4_evidence import require
from v4_service_once import IMAGE, MEMORY_BYTES, gpu_snapshot, store_limits
from v4_trial import checked_json
from v4_trial_review import review_trial

ROOT=Path(__file__).resolve().parents[1]
EVIDENCE=ROOT.parent/'evidence'
JOURNALS=EVIDENCE/'v4-4x10-journals-20261107'


def next_attempt(history, head, evidence=EVIDENCE, acknowledged_failures=None, prior_qualified=None):
    pending=None
    for row in history:
        if not row['consumed']:
            if pending is None:pending=row
            continue
        approved=(acknowledged_failures or {}).get(row['attempt_id'])
        prestartup=approved is not None and approved.get('classification')=='prestartup_cache_admission_failed'
        require(pending is None and row['status']==('failed' if prestartup else 'evidence_complete_unqualified'),
                'failed/incomplete/out-of-order attempt blocks continuation')
        if approved is not None:
            require(row['attempt_id']==('v4n40_safetensors_disk_hot_01' if prestartup else 'v4n40_store_pool_hit_01'),
                    'only the two named sealed failures may be acknowledged')
            base=evidence/row['attempt_id']
            failure=checked_json(base/'failure.json',approved['failure_sha256'])
            require(approved['counts_as_success'] is False,'acknowledged failures never count as success')
            if prestartup:
                audit=checked_json(ROOT/approved['audit_review_path'],approved['audit_review_sha256'])
                require(row['attempt_id']=='v4n40_safetensors_disk_hot_01'
                        and audit['artifact_kind']=='failed_prestartup_cache_admission_audit'
                        and audit['attempt_id']==row['attempt_id'] and audit['journal_status']=='failed'
                        and audit['runtime_qualified'] is False and audit['counts_as_success'] is False
                        and audit['model_startup_reached'] is False and audit['safe_post_failure_selected_resource_cleanup_verified']
                        and audit['source_head']==approved['source_head']
                        and audit['failure_sha256']==approved['failure_sha256']
                        and audit['journal_terminal_sha256']==approved['journal_terminal_sha256'],
                        'only the sealed prestartup cache failure may be acknowledged')
                for name,sha in audit['input_sha256'].items():
                    require(hashlib.sha256((base/name).read_bytes()).hexdigest()==sha,'audited failed raw evidence changed')
            else:
                require(failure['artifact_kind']=='independent_runtime_review_failure'
                        and failure['main_runner_normal_completion'] and failure['formal_id_consumed']
                        and failure['engine_creations']==2,
                        'only the sealed warm-engine runtime failure may be acknowledged')
                require(hashlib.sha256((base/'candidate.json').read_bytes()).hexdigest()==approved['candidate_sha256'],
                        'acknowledged original evidence changed')
            require(hashlib.sha256((Path(row['directory'])/row['journal_files'][-1]['path']).read_bytes()).hexdigest()==approved['journal_terminal_sha256'],
                    'acknowledged original evidence changed')
            continue
        path=evidence/row['attempt_id']/'runtime-review.json'
        prior=(prior_qualified or {}).get(row['attempt_id'])
        review=checked_json(path,prior['review_sha256']) if prior else json.loads(path.read_bytes())
        require(review['runtime_qualified'] is True and review['success'] is True
                and review['formal_id_consumed'] is True and review['source_head']==(prior['source_head'] if prior else head)
                and review['attempt_id']==row['attempt_id'], 'missing/mismatched runtime review')
        for name,sha in review['input_sha256'].items():
            require(hashlib.sha256((path.parent/name).read_bytes()).hexdigest()==sha,
                    'reviewed raw evidence changed')
    return pending


def launch_one(row, protocol, protocol_sha, head, campaign, amendment=None, amendment_sha=None):
    name=row['attempt_id'];base=EVIDENCE/name
    require(not base.exists(), 'runtime directory already consumed')
    gpu=gpu_snapshot()
    require(not gpu['apps'] and len(gpu['rows'])==8 and all(int(x[4])==0 for x in gpu['rows']),
            'fresh GPU admission failed before claiming next ID')
    store=Path(json.loads((ROOT/'docs/evidence/v4-store-data-integrity/qualified-store.json').read_bytes())['store_root'])
    require(store_limits(store)['host_available_bytes']>=1024**4+64*1024**3,
            'fresh memory admission failed before claiming next ID')
    command=['python3','-u',str(ROOT/'benchmarks/v4_service_once.py'),str(base),head,
             '--trial-protocol',str(protocol),'--trial-sha256',protocol_sha,'--condition',row['condition'],
             '--attempt-id',name,'--journal-root',str(JOURNALS),'--execute']
    if amendment is not None:command+=['--amendment',str(amendment),'--amendment-sha',amendment_sha]
    with (campaign/(name+'.controller.log')).open('x') as log:
        process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
            start_new_session=True,env=dict(os.environ,PYTHONPATH=str(ROOT/'benchmarks')+':'+str(ROOT/'sllm_store')))
    handle=dict(base=str(base),pid=process.pid,start_ticks=int(Path('/proc',str(process.pid),'stat').read_text().rsplit(')',1)[1].split()[19]),
                controller_log=str(campaign/(name+'.controller.log')),source_head=head,purpose='formal-trial',formal_id_consumed=True,condition=row['condition'])
    write_once(campaign/(name+'.handle.json'),handle)
    pointer=EVIDENCE/'native-service-current-handle.json';temp=pointer.with_suffix('.next');temp.write_text(json.dumps(handle)+'\n');os.replace(temp,pointer)
    print(json.dumps(dict(stage='preparing',attempt_id=name,condition=row['condition'],pid=process.pid)),flush=True)
    begin=time.monotonic();launched=False;previous=()
    while process.poll() is None:
        ready=base/'READY.json'
        if ready.exists() and not launched:
            roles=json.loads(ready.read_bytes())['roles']
            for role,value in roles.items():
                actual,=json.loads(subprocess.check_output(['docker','inspect',value['container_id']],text=True,timeout=10))
                config=actual['HostConfig']
                require(actual['Created']==value['created'] and actual['Image']==IMAGE and actual['State']['Status']=='created'
                        and config['NanoCpus']==4000000000 and config['Memory']==MEMORY_BYTES[role]
                        and config['PidsLimit']==4096 and actual['Config']['User']=='2147:2147', 'actual role resource/identity differs')
                if role=='worker':require(set(config['CapAdd'])=={'FOWNER','DAC_READ_SEARCH'},'sampler capabilities differ')
            write_once(base/'GO.json',dict(source_head=head,purpose='formal-trial',notified_before_gpu=True,
                authorization='User authorized autonomous four conditions times10; batch GPU notification delivered; fixed order/no retry'))
            launched=True
            print(json.dumps(dict(stage='GPU_launch',attempt_id=name)),flush=True)
        markers=tuple(sorted(p.name for p in (base/'output').glob('*.json')))
        if markers!=previous:
            print(json.dumps(dict(stage='markers',attempt_id=name,new=[x for x in markers if x not in previous])),flush=True);previous=markers
        require(time.monotonic()-begin<3900,'controller overrun; stop campaign without retry or killing foreign work')
        time.sleep(1)
    require(process.returncode==0, 'attempt failed; original ID retained; campaign stopped')
    result=review_trial(base,protocol,protocol_sha,JOURNALS,head,amendment_path=amendment,amendment_sha=amendment_sha)
    print(json.dumps(dict(stage='qualified',attempt_id=name,condition=row['condition'],
        command_to_response_s=result['command_to_correct_response_seconds'],total_s=result['total_elapsed_seconds'])),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('protocol',type=Path);parser.add_argument('protocol_sha');parser.add_argument('source_head')
    parser.add_argument('--amendment',type=Path);parser.add_argument('--amendment-sha')
    parser.add_argument('--execute',action='store_true');parser.add_argument('--notified-before-gpu',action='store_true')
    args=parser.parse_args();protocol=checked_json(args.protocol,args.protocol_sha)
    require(not sys.flags.optimize and not os.environ.get('PYTHONOPTIMIZE'),'optimized campaign forbidden')
    require(protocol['frozen'] is True,'campaign protocol not frozen')
    rows=frozen_protocol(args.protocol,args.protocol_sha)
    policy=protocol;acknowledged={};prior_qualified={}
    if args.amendment is not None or args.amendment_sha is not None:
        require(args.amendment is not None and args.amendment_sha is not None,'amendment path/SHA pair required')
        policy=checked_json(args.amendment,args.amendment_sha)
        require(policy['schema']==1 and policy['frozen'] is True and policy['continuation_authorized'] is True
                and policy['base_preregistration_sha256']==args.protocol_sha and policy['keep_alive_seconds']==3600,
                'prospective warm-engine continuation not frozen')
        acknowledged=policy['acknowledged_failures']
        expected={'v4n40_store_pool_hit_01'}
        if policy.get('sf_hot_page_cache_lease'):
            expected.add('v4n40_safetensors_disk_hot_01')
            prior_qualified=policy['prior_qualified_attempts']
            require(set(prior_qualified)=={'v4n40_safetensors_disk_cold_01','v4n40_store_disk_cold_01'},
                    'only the two sealed original cold successes may retain their source')
            proof=checked_json(ROOT/policy['sf_hot_lease_pilot_review_path'],policy['sf_hot_lease_pilot_review_sha256'])
            require(proof['runtime_qualified'] and proof['sf_hot_page_cache_lease_verified']
                    and not proof['formal_id_consumed'],'temporal SF-hot pilot prerequisite missing')
        require(set(acknowledged)==expected,'unknown failure acknowledgement')
    for name,sha in policy['execution_source_sha256'].items():
        require(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha, 'frozen execution source changed')
    prerequisite=checked_json(ROOT/protocol['sf_hot_prerequisite_path'],protocol['sf_hot_prerequisite_review_sha256'])
    require(prerequisite['runtime_qualified'] and prerequisite['full_model_condition_pilot_qualified']
            and not prerequisite['formal_id_consumed'], 'genuine SF hot prerequisite missing')
    if not args.execute:
        if policy.get('sf_hot_page_cache_lease'):
            history=collect_attempts(args.protocol,args.protocol_sha,[JOURNALS])
            pending=next_attempt(history,args.source_head,acknowledged_failures=acknowledged,prior_qualified=prior_qualified)
            print(json.dumps(dict(attempts=len(rows),consumed=sum(x['consumed'] for x in history),
                next_id=pending['attempt_id'] if pending else None,gpu_or_model_started=False)))
        else:print(json.dumps(dict(attempts=len(rows),gpu_or_model_started=False,first=rows[0]['attempt_id'])))
        return
    require(args.notified_before_gpu,'batch GPU notice is required')
    require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==args.source_head
            and not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),'source changed')
    lease=(ROOT.parent/'campaign.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    campaign=EVIDENCE/('v4-4x10-campaign-'+args.amendment_sha[:12] if policy.get('sf_hot_page_cache_lease') else
        ('v4-4x10-campaign-amended-20261107' if args.amendment is not None else 'v4-4x10-campaign-20261107'));campaign.mkdir(exist_ok=False);JOURNALS.mkdir(exist_ok=True)
    write_once(campaign/'origin.json',dict(source_head=args.source_head,protocol_sha256=args.protocol_sha,amendment_sha256=args.amendment_sha,acknowledged_failed_ids=list(acknowledged),begin_ns=time.monotonic_ns()))
    try:
        while True:
            row=next_attempt(collect_attempts(args.protocol,args.protocol_sha,[JOURNALS]),args.source_head,
                             acknowledged_failures=acknowledged,prior_qualified=prior_qualified)
            if row is None:break
            frozen=next(item for item in rows if item['attempt_id']==row['attempt_id'])
            launch_one(frozen,args.protocol,args.protocol_sha,args.source_head,campaign,args.amendment,args.amendment_sha)
        write_once(campaign/'complete.json',dict(attempts=40,fully_qualified_attempts=40-len(acknowledged),acknowledged_runtime_failures=list(acknowledged),all_runtime_qualified=not acknowledged,completed_ns=time.monotonic_ns(),source_head=args.source_head,protocol_sha256=args.protocol_sha))
        print('CAMPAIGN_COMPLETE_40',flush=True)
    except BaseException as error:
        write_once(campaign/'failure.json',dict(error=repr(error),failed_ns=time.monotonic_ns(),automatic_retry=False))
        raise


if __name__=='__main__':main()
