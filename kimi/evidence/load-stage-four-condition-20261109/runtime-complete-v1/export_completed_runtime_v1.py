import pathlib,json,hashlib,os,pwd,subprocess,time
S=pathlib.Path('/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104')
N=S/'kimi-unified-loading-20261109-qjhs000-raid-v1/formal64-supplement-v1/load-stage-implementation-v1'
D=N/'runtime-complete-v1'
assert not D.exists(), 'export already exists; inspect rather than overwrite'
BID='kimi-load-stages-20261109-qjhs000-v1-b01'
bundle=json.loads((S/(BID+'-formal-v3-78beeac12a70-bundle.json')).read_text())
claims=pathlib.Path(bundle['claims_root']);runs=pathlib.Path(bundle['runs_root'])
ledger=json.loads((claims/(BID+'-final.json')).read_text())
assert ledger['status']=='THREE_STAGE_EXIT300_BLOCK_RELEASE60_PASS'
D.mkdir();manifest=[];checks=[]
def put(rel,data,source=None,kind='UNMODIFIED_RAW_COPY'):
 p=D/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
 manifest.append({'export_path':rel,'source_path':str(source) if source else None,'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),'kind':kind})
def raw(p,rel):
 data=p.read_bytes();put(rel,data,p)
def derived(rel,value,source=None):
 put(rel,(json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode(),source,'DERIVED_CAPSULE_NOT_RAW_FULL_FILE')
raw(claims/(BID+'-final.json'),'block-final.json')
selected=['controller-final.json','sample-validity.json','sample-metrics.json','sample-metrics-arithmetic.json','sample-metrics-translation.json','sample-metrics-fixed32.json','cleanup-result.json','condition-terminal.json','unified-loading-host-span.json','structured-lifecycle-events.json','cache-construction-result.json','cache-admission.json','cache-admission-page-counts.json','pool-admission.json','storage-route-admission.json','load-evidence-original-files-before.json','load-evidence-original-files-after.json','store-stop-exit300.json','controller-initial-authenticated-identity.json','initial-fresh-store-baseline.json','life1-deleted-fresh-baseline.json','life1-response0.json','life1-response1.json','life1-response2.json','life1-tokens0.json','life1-tokens1.json','life1-tokens2.json']
for a in ledger['attempts']:
 c=a['condition'];b=runs/a['run_id'];f=json.loads((b/'controller-final.json').read_text())
 v=json.loads((b/'sample-validity.json').read_text());cl=json.loads((b/'cleanup-result.json').read_text())
 assert f['status']=='THREE_STAGE_EXIT300_SINGLE_CONDITION_CORRECT_RELEASE60_PASS'
 assert v['primary_sample_qualified'] and cl['normal'] and cl['quiet_seconds']>=60
 assert all(x['MainPID']=='0' and x['ExecMainStatus']=='0' and x['Result']=='success' for x in f['units'].values())
 for name in selected:
  p=b/name
  if p.exists():raw(p,c+'/'+name)
 for p in b.glob('event-*.json'):raw(p,c+'/'+p.name)
 for role in ['head','worker']:
  p=b/('guard-'+role)/'guard-final.json';g=json.loads(p.read_text())
  assert not g['failures'] and g['quiet_seconds']>=60 and g['abort_monotonic'] is None
  raw(p,c+'/guard-'+role+'/guard-final.json')
  for ident in g['pinned_identities']:
   p0=pathlib.Path('/proc')/str(ident['pid'])/'stat'
   if p0.exists():assert int(p0.read_text().rpartition(')')[2].split()[19])!=ident['startticks']
  ex=b/(role+'-exited.json');ev=json.loads(ex.read_text());state=ev['State']
  assert state['ExitCode']==0 and not state['Running'] and not state['OOMKilled'] and state['Pid']==0
  scope=pathlib.Path('/sys/fs/cgroup/system.slice')/('docker-'+ev['Id']+'.scope')
  assert not scope.exists()
  derived(c+'/'+role+'-exit-state-capsule.json',{'source_path':str(ex),'source_sha256':hashlib.sha256(ex.read_bytes()).hexdigest(),'Id':ev['Id'],'State':state,'Image':ev['Image'],'HostConfig':{k:ev['HostConfig'].get(k) for k in ['PidsLimit','ShmSize','Memory','NanoCpus','DeviceRequests']},'Mounts':ev['Mounts'],'scope_absent_post_release':True},ex)
 ob=b/'observer/observer-final.json';o=json.loads(ob.read_text());assert o['quiet_seconds']>=60;raw(ob,c+'/observer/observer-final.json')
 before=json.loads((b/'load-evidence-original-files-before.json').read_text())['files']
 after=json.loads((b/'load-evidence-original-files-after.json').read_text())['files']
 assert len(before)==len(after)==8
 bt={x['tp_rank']:x for x in before};at={x['tp_rank']:x for x in after}
 ownpath=b/'life1-eight-rank-load-and-ownership.json';ownership=json.loads(ownpath.read_text())
 spans=json.loads((b/'unified-loading-host-span.json').read_text())
 for rank in range(8):
  rec=bt[rank];p=pathlib.Path(rec['path']);data=p.read_bytes();h=hashlib.sha256(data).hexdigest()
  assert h==rec['sha256']==at[rank]['sha256']
  recjson=json.loads(data);span=recjson['weight_load_host_span']
  sr=next(x for x in spans['ranks'] if x['tp_rank']==rank)
  assert span['begin_ns']==sr['begin_ns'] and span['end_ns']==sr['end_ns'] and span['stages']=={k:{a:b for a,b in vv.items() if a not in ['host_duration_ns','host_duration_s']} for k,vv in sr['stages'].items()}
  assert span['status']=='COMPLETE' and span['stage_schema']=='kimi-weight-load-host-stages-v1'
  derived(c+'/rank-endpoint-capsules/rank-'+str(rank)+'.json',{'original_path':str(p),'original_sha256':h,'original_bytes':len(data),'whole_file_sha_matches_before_after':True,'tp_rank':rank,'pid':recjson['pid'],'device_uuid':recjson['device_uuid'],'load_completed':recjson['load_completed'],'weight_load_host_span':span,'host_owner':sr['host_owner']},p)
 derived(c+'/ownership-capsule.json',{'source_path':str(ownpath),'source_sha256':hashlib.sha256(ownpath.read_bytes()).hexdigest(),'owners':ownership['owners'],'physical':ownership['physical'],'records_omitted':'registered tensor/storage metadata retained in original root evidence; no weight values read here'},ownpath)
 cp=claims/(a['run_id']+'-controller-unit-span.json');raw(cp,c+'/controller-unit-span.json')
 native=json.loads(cp.read_text());assert native['termination_class']=='NORMAL_EXIT0' and native['clock_proof']['status']=='VALID'
 checks.append({'condition':c,'sample_qualified':True,'normal_exit':True,'quiet_seconds_min':cl['quiet_seconds'],'8_rank_full_files_sha_unchanged':True,'native_controller_span_s':native['span_ns']/1e9})
gpus=subprocess.check_output(['nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader,nounits'],text=True,timeout=15)
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,gpu_uuid,used_memory','--format=csv,noheader,nounits'],text=True,timeout=15)
assert len(gpus.strip().splitlines())==8 and all(x.rsplit(',',1)[1].strip()=='0' for x in gpus.strip().splitlines()) and not apps.strip()
derived('post-release-verification.json',{'epoch':time.time(),'status':'ALL_FOUR_NORMAL_EXIT_RELEASE60_AND_GPU_FREE_VERIFIED','conditions':checks,'gpu_query':gpus,'compute_apps':apps,'parent_unit':'inactive/dead MainPID0; transient unit properties reclaimed, no fabricated native parent timestamps','note':'Child exact native process spans retained separately; raw endpoints and full original rank metadata remain unchanged on server.'})
derived('EXPORT-MANIFEST.json',manifest)
u=pwd.getpwnam('boyjiang')
for p in [D]+list(D.rglob('*')):
 os.chown(p,u.pw_uid,u.pw_gid);os.chmod(p,0o755 if p.is_dir() else 0o644)
print(json.dumps({'path':str(D),'files':len(manifest),'bytes':sum(x['bytes'] for x in manifest),'checks':checks}))
