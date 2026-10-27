"""Run or observe the existing guarded G1; SSH loss is not a model verdict."""
import argparse,hashlib,json,pathlib,re,subprocess,sys,uuid

REPO=pathlib.Path(__file__).resolve().parents[1]
SSH=['ssh','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10',
     '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=4','qjhs003','python3','-']

def load_context(code,sha,verdict_file):
    if not re.fullmatch('[0-9a-f]{40}',code) or not re.fullmatch('[0-9a-f]{64}',sha):raise RuntimeError('exact review binding required')
    source=REPO/'runtime/active'
    data=(source/'control-input-manifest.json').read_bytes()
    if hashlib.sha256(data).hexdigest()!=sha:raise RuntimeError('manifest differs')
    verdict_path=(REPO/verdict_file).resolve()
    if not verdict_path.is_relative_to((REPO/'reviews').resolve()):raise RuntimeError('review outside repository')
    sys.path.insert(0,str(source))
    from package_inventory13 import require_full_r1,reviewed_files
    require_full_r1(json.loads(verdict_path.read_text()),code,sha)
    manifest=json.loads(data);reviewed_files(source,manifest['files'])
    for row in manifest['files']:
        path=(source/row['path']).resolve()
        if not path.is_relative_to(source.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise RuntimeError('runtime input differs')
    names={row['path'] for row in manifest['repository_inputs']}
    if not {'tools/run_control13.py','tools/root_control13.py'}<=names:raise RuntimeError('operator code is not review-bound')
    for row in manifest['repository_inputs']:
        path=(REPO/row['path']).resolve()
        if not path.is_relative_to(REPO.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise RuntimeError('repository review input differs')
    run=json.loads((source/'run-manifest.json').read_text())['run_id']
    if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',run):raise RuntimeError('invalid run')
    return run

REMOTE=r'''
import datetime,hashlib,json,pathlib,socket,subprocess,time
root=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')
src=root/'code/experiment'
import sys
sys.path.insert(0,str(src))
out=root/'validation/controls'/RUN
from budget_control13 import G1_PHASE_BUDGETS,g1_required_window
diagnostic=PHASE=='monitor-diagnostic'
diagnostic_end=time.monotonic()+(180 if diagnostic else G1_PHASE_BUDGETS.get(PHASE,14400))
log=(out/('operator-'+('observe-' if OBSERVE else '')+PHASE+'-'+SEGMENT+'.jsonl')).open('x',buffering=1)
def event(**row):
 row.update(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),monotonic_ns=time.monotonic_ns())
 log.write(json.dumps(row)+'\n');print(json.dumps(row),flush=True)
def call(args,timeout=120):
 event(command=args)
 timeout=min(timeout,diagnostic_end-time.monotonic())
 if timeout<=0:raise TimeoutError('original control phase budget expired')
 p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
 event(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
 if p.returncode:raise RuntimeError('command failed: '+str(args))
 return p.stdout.strip()
def invoke(name,*args,timeout=120):return call(['python3',str(src/name),*args],timeout)
def launch(name):
 invoke('materialize_control13.py',name)
 invoke('launch_control13.py',name+'.json',SHA,CODE)
def identity(name):
 r=json.loads((out/(name+'-identity.json')).read_text())
 assert r['run_id']==RUN and r['code_commit']==CODE and r['manifest_sha256']==SHA
 d=json.loads(call(['docker','inspect',r['container_id']]))[0]
 assert d['Config']['Labels']['sllm.run_id']==RUN and d['Config']['Labels']['sllm.uid']=='2147'
 assert d['Image']==json.loads((out/'run-manifest.json').read_text())['image_id']
 if d['State']['Running']:assert d['State']['Pid']==r['pid']
 return r,d
def ready():
 r=json.loads((out/'monitor-v3-ready.json').read_text())
 from gpu_identity13 import require_monitor_ready
 require_monitor_ready(r,time.monotonic_ns(),RUN,json.loads((out/'run-manifest.json').read_text())['runtime_identity']['boot_id'])
 return r
def cpu_imports(r,label):
 probe="""import json,pathlib,hashlib
from run_store_server13 import verify_store_imports
from workspace import SOURCE,manifest,run_root
rows=verify_store_imports(manifest()['store_import_sha256'])
import torch,vllm,cloudpickle,tensor_audit
from tensor_audit import audit_model
from vllm.distributed import get_tensor_model_parallel_rank
path=pathlib.Path(tensor_audit.__file__).resolve()
assert path==SOURCE/'tensor_audit.py', 'audit helper origin differs'
assert hashlib.sha256(path.read_bytes()).hexdigest()=='069ab4d8cfdc3f57819358222b6c1a1e8973c795df2131974eb0a3b9161bbcff', 'audit helper hash differs'
assert cloudpickle.loads(cloudpickle.dumps(audit_model)) is audit_model, 'audit callback module import differs'
# Actual Linux interpreter cancellation proof, before any large pool allocation.
import select,signal,subprocess,sys,time
child_code="import asyncio\\nasync def main():\\n print('ready',flush=True)\\n try: await asyncio.Event().wait()\\n except asyncio.CancelledError: print('cleanup',flush=True)\\nasyncio.run(main())\\n"
child=subprocess.Popen([sys.executable,'-u','-c',child_code],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
try:
 assert select.select([child.stdout],[],[],5)[0], 'SIGINT probe readiness timeout'
 assert child.stdout.readline().strip()=='ready'
 child.send_signal(signal.SIGINT)
 stdout,stderr=child.communicate(timeout=5)
 assert child.returncode==0 and stdout.strip()=='cleanup', 'asyncio cancellation cleanup failed'
 sigint_proof=dict(pid=child.pid,signal=signal.SIGINT,returncode=child.returncode,cleanup_marker=stdout.strip(),interpreter=sys.executable,version=sys.version,monotonic_ns=time.monotonic_ns())
finally:
 if child.poll() is None:
  child.kill();child.communicate(timeout=5)
head_identity=json.loads((run_root()/'headservice13a-identity.json').read_text())
registration=manifest()
if registration.get('jit_seed_runtime_versions'):
 assert registration['jit_seed_runtime_versions']==dict(torch=str(torch.__version__),vllm=str(vllm.__version__),cuda=torch.version.cuda), 'JIT seed actual runtime dependency versions differ'
if registration.get('execution_purpose')=='formal-continuation':
 import os
 label=head_identity['spec']['name'].removeprefix('head')
 probe_dir=run_root()/(label+'-atomic-publication-probe');probe_dir.mkdir()
 temporary=probe_dir/'temporary';published=probe_dir/'published'
 temporary.write_bytes(b'controlled-stop-publication')
 os.link(temporary,published)
 assert published.read_bytes()==temporary.read_bytes()
 try:os.link(temporary,published)
 except FileExistsError:collision_rejected=True
 else:raise RuntimeError('atomic publication replaced existing request')
 published.unlink();temporary.unlink();probe_dir.rmdir()
 sigint_proof['atomic_publication']=dict(local_same_filesystem=True,exclusive_collision_rejected=collision_rejected,temporary_removed=True)
 from cache_contract13 import probe_cold_boundary
 sigint_proof['cold_boundary_probe']=probe_cold_boundary(run_root()/(label+'-cold-boundary-probe'),pathlib.Path('/experiment/checkpoint/config.json'))
 from run_store_server13 import verify_event_trace
 trace=verify_event_trace(registration['event_trace_sha256'])
 original=os.environ.get('SLLM_EVENT_DIR')
 assert original=='/r/events-'+label, 'CPU head timing directory absent or differs'
 probe_dir=run_root()/(label+'-event-trace-probe');probe_dir.mkdir()
 try:
  os.environ['SLLM_EVENT_DIR']=str(probe_dir)
  from sllm_store.event_trace import record_event
  record_event('backend_engine_create_begin',probe=True)
  record_event('router_instance_published',probe=True)
  files=list(probe_dir.glob('*.jsonl'));assert len(files)==1
  data=files[0].read_bytes();events=[json.loads(line) for line in data.splitlines()]
  assert [row['name'] for row in events]==['backend_engine_create_begin','router_instance_published']
  creation=events[0]
  assert creation['boot_id']==registration['runtime_identity']['boot_id']
  assert creation['pid']==os.getpid() and creation['process_start_ticks']>0 and creation['clock_ticks_per_second']>0
  assert type(creation['boottime_minus_monotonic_ns']) is int
  sigint_proof['event_trace_probe']=dict(source=trace,measurement_event_dir=original,probe_event_dir=str(probe_dir),raw_events=events,raw_sha256=hashlib.sha256(data).hexdigest(),original_env_verified=True,isolated=True)
 finally:
  os.environ['SLLM_EVENT_DIR']=original
 assert os.environ['SLLM_EVENT_DIR']==original
 sigint_proof['event_trace_probe']['environment_restored']=True

deployment=json.loads((run_root()/'deployment.json').read_text())
sigint_proof.update(run_id=registration['run_id'],head_container_id=head_identity['container_id'],image_id=registration['image_id'],boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),code_commit=deployment['code_commit'],manifest_sha256=deployment['input_manifest_sha256'])
if manifest().get('grpc_shutdown_probe') is True:
 assert manifest()['scope']=='monitor diagnostics only; no model or pool'
 import grpc,grpc.aio._server,grpc._cython.cygrpc,socket
 grpc_child_code="import asyncio,grpc,json,sys\\nmode=sys.argv[1]\\ndef event(stage,**values):print(json.dumps(dict(stage=stage,**values)),flush=True)\\nasync def main():\\n server=grpc.aio.server()\\n port=server.add_insecure_port('127.0.0.1:0')\\n assert port>0\\n await server.start()\\n waiter=asyncio.create_task(server.wait_for_termination())\\n stop_event=asyncio.Event()\\n if mode=='event':\\n  import signal\\n  asyncio.get_running_loop().add_signal_handler(signal.SIGINT,lambda:(event('signal_received'),stop_event.set()))\\n await asyncio.sleep(.1)\\n event('ready',port=port,mode=mode)\\n try:\\n  if mode=='event':await stop_event.wait()\\n  elif mode=='shield':await asyncio.shield(waiter)\\n  else:await waiter\\n except asyncio.CancelledError:\\n  event('cancelled',cancelling=asyncio.current_task().cancelling(),waiter_cancelled=waiter.cancelled())\\n event('stop_begin')\\n try:await server.stop(.1)\\n except BaseException as error:\\n  event('stop_error',error=type(error).__name__)\\n  raise\\n event('stop_end')\\n await waiter\\n event('waiter_end')\\nasyncio.run(main())\\n"
 grpc_sources={str(pathlib.Path(module.__file__).resolve()):hashlib.sha256(pathlib.Path(module.__file__).read_bytes()).hexdigest() for module in (grpc,grpc.aio._server,grpc._cython.cygrpc)}
 grpc_trace=(run_root()/'grpc-shutdown-probe.jsonl').open('x')
 grpc_trace.write(json.dumps(dict(stage='header',binding={key:sigint_proof[key] for key in ('run_id','head_container_id','image_id','boot_id','code_commit','manifest_sha256')},python=sys.version,grpc_version=grpc.__version__,child_source=grpc_child_code,child_source_sha256=hashlib.sha256(grpc_child_code.encode()).hexdigest(),sources=grpc_sources))+'\\n');grpc_trace.flush()
 grpc_rows=[]
 for mode in ('direct','shield','event'):
  process=subprocess.Popen([sys.executable,'-u','-c',grpc_child_code,mode],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  prefix='';stdout='';stderr='';forced=False;timed_out=False;port=None;failure=None;sent_ns=None;unreaped=False;begin_ns=time.monotonic_ns()
  try:
   assert select.select([process.stdout],[],[],5)[0], 'grpc readiness timeout'
   prefix=process.stdout.readline()
   first=json.loads(prefix);assert first['stage']=='ready'
   port=first['port'];process.send_signal(signal.SIGINT);sent_ns=time.monotonic_ns()
   try:stdout,stderr=process.communicate(timeout=10)
   except subprocess.TimeoutExpired as error:
    timed_out=True;failure=repr(error)
    stdout=error.stdout or '';stderr=error.stderr or ''
    if isinstance(stdout,bytes):stdout=stdout.decode(errors='replace')
    if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
  except Exception as error:
   failure=repr(error)
  finally:
   if process.poll() is None:
    forced=True;process.kill()
    try:stdout,stderr=process.communicate(timeout=2)
    except subprocess.TimeoutExpired as error:
     unreaped=True;failure=repr(error)
     stdout=error.stdout or '';stderr=error.stderr or ''
     if isinstance(stdout,bytes):stdout=stdout.decode(errors='replace')
     if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
  try:events=[json.loads(line) for line in (prefix+stdout).splitlines()]
  except ValueError:events=[];failure=failure or 'malformed child event output'
  listening=True
  try:
   if port is None:raise OSError('no listener identity')
   with socket.create_connection(('127.0.0.1',port),timeout=1):pass
  except OSError:listening=False
  grpc_rows.append(dict(mode=mode,pid=process.pid,signal=signal.SIGINT,signal_sent_ns=sent_ns,begin_ns=begin_ns,end_ns=time.monotonic_ns(),returncode=process.returncode,forced_kill=forced,timed_out=timed_out,failure=failure,events=events,stdout=prefix+stdout,stderr=stderr,listener_remaining=listening,stop_returned=any(row['stage']=='stop_end' for row in events)))
  grpc_trace.write(json.dumps(dict(stage='group_result',**grpc_rows[-1],unreaped=unreaped))+'\\n');grpc_trace.flush()
  if unreaped:raise RuntimeError('grpc child unreaped; stop control and request own cleanup')

 with (run_root()/'grpc-shutdown-probe.json').open('x') as stream:
  json.dump(dict(binding={key:sigint_proof[key] for key in ('run_id','head_container_id','image_id','boot_id','code_commit','manifest_sha256')},python=sys.version,grpc_version=grpc.__version__,child_source=grpc_child_code,child_source_sha256=hashlib.sha256(grpc_child_code.encode()).hexdigest(),sources=grpc_sources,rows=grpc_rows,scope='CPU empty gRPC server only; no Store/model/CUDA pool'),stream,indent=2)
 grpc_trace.close()
 assert len(grpc_rows)==3 and all(row['failure'] is None and row['signal_sent_ns'] is not None and any(event['stage']=='ready' for event in row['events']) and not row['forced_kill'] and not row['listener_remaining'] for row in grpc_rows), 'incomplete grpc comparison'
 assert all(row['failure'] is None and row['returncode']==0 and row['stop_returned'] and not row['forced_kill'] and not row['listener_remaining'] for row in grpc_rows if row['mode'] in ('shield','event')), 'grpc candidate controls failed'
with (run_root()/'cpu-import-preflight.json').open('x') as f:
 json.dump(dict(sigint_probe=sigint_proof,modules=rows,audit_helper=dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),callback_module=audit_model.__module__,callback_roundtrip=True),torch=str(torch.__version__),vllm=str(vllm.__version__),cuda=torch.version.cuda,scope='CPU-only own head import proof; no pool, serve or model'),f,indent=2)
print('CPU import preflight passed')"""
 probe=probe.replace("'headservice13a-identity.json'",repr('head'+label+'-identity.json'))
 if manifest.get('execution_purpose')=='formal-continuation':probe=probe.replace("'cpu-import-preflight.json'",repr(label+'-cpu-import-preflight.json'))
 call(['docker','exec',r['container_id'],'/experiment/.venv/bin/python','-c',probe],timeout=90)
from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_BUDGET,POOL_DIAGNOSTIC_PHASE_BUDGET,POOL_DIAGNOSTIC_WINDOW
manifest=json.loads((out/'run-manifest.json').read_text())
pool_diagnostic=manifest.get('scope')==POOL_DIAGNOSTIC_SCOPE
formal_entry=manifest.get('execution_purpose')=='formal-continuation'
if formal_entry:
 assert manifest['scope']=='formal continuation of original forty IDs' and PHASE in ('prepare','service13a','formal13')
 from budget_control13 import load_formal_batch,head_stop_requested
 from preflight_control13 import check
 check(out,monitor_required=PHASE!='prepare' or OBSERVE,continuation=OBSERVE)
 if PHASE=='prepare':
  if not OBSERVE:
   assert not call(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'])
   assert [int(x) for x in call(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits']).splitlines()]==[0]*8
   invoke('prepare_host13.py',SHA,CODE)
   launch('monitor13')
   stop=time.monotonic()+20
   while not (out/'monitor-v3-ready.json').exists():
    if time.monotonic()>stop:raise TimeoutError('monitor readiness timeout')
    time.sleep(.5)
  ready()
 else:
  ready();head='head'+PHASE
  if not OBSERVE:
   assert not (out/(head+'-dispatch.json')).exists(),'batch already issued; observe or clean, never redispatch'
   launch(head)
  r,d=identity(head)
  batch=load_formal_batch(out,PHASE)
  diagnostic_end=batch['head_end_ns']/1e9
  if not OBSERVE:
   port=26533 if PHASE=='formal13' else 26532
   stop=min(time.monotonic()+45,batch['initialization_end_ns']/1e9)
   while True:
    _,d=identity(head);assert d['State']['Running'],'head exited before GCS ready'
    try:
     with socket.create_connection((d['NetworkSettings']['Networks']['bridge']['IPAddress'],port),timeout=1):pass
     if time.monotonic()>=stop:raise TimeoutError('late GCS TCP readiness')
     break
    except OSError:
     if time.monotonic()>=stop:raise TimeoutError('GCS TCP readiness timeout')
     time.sleep(1)
   cpu_imports(r,PHASE)
   launch(PHASE)
  worker,state=identity(PHASE)
  assert worker['dispatch_monotonic_ns']>=batch['begin_ns']
  if state['State']['Running']:
   remaining=batch['total_end_ns']/1e9-time.monotonic()
   assert remaining>0,'issued worker deadline expired'
   exit_code=call(['docker','wait',worker['container_id']],timeout=remaining)
  else:exit_code=str(state['State']['ExitCode'])
  assert exit_code=='0','worker failed '+exit_code
  proof=json.loads((out/(PHASE+'-supervisor-result.json')).read_text())
  assert proof['status']=='passed including cleanup' and not proof['child_alive_at_end'] and proof['absolute_end_ns']==batch['total_end_ns']
  r,d=identity(head)
  request_path=out/(head+'-stop-request.json')
  if d['State']['Running'] and not request_path.exists():
   expected={key:r[key] for key in ('run_id','code_commit','manifest_sha256','container_id','pid')}
   expected.update(boot_id=manifest['runtime_identity']['boot_id'],head_dispatch_monotonic_ns=batch['begin_ns'])
   requested_ns=time.monotonic_ns()
   request=dict(identity=expected,requested_ns=requested_ns)
   import os
   temporary=request_path.with_name(request_path.name+'.'+SEGMENT+'.tmp')
   with temporary.open('x') as stream:json.dump(request,stream,indent=2)
   try:
    try:os.link(temporary,request_path)
    except FileExistsError:assert head_stop_requested(out,PHASE),'existing stop request binding differs'
   finally:temporary.unlink()
  assert head_stop_requested(out,PHASE),'head stopped without a valid completed-worker request'
  if d['State']['Running']:
   remaining=batch['head_end_ns']/1e9-time.monotonic();assert remaining>0,'issued head deadline expired'
   assert call(['docker','wait',r['container_id']],timeout=remaining)=='0','head supervisor failed'
  else:assert d['State']['ExitCode']==0,'head supervisor failed'
  head_proof=json.loads((out/(head+'-supervisor-result.json')).read_text())
  assert head_proof['status']=='passed including cleanup' and head_proof['controlled_stop'] is True and head_proof['forced_kill'] is False and head_proof['terminate_sent_ns'] is not None and head_proof['child_returncode'] in (0,1,-15) and not head_proof['child_alive_at_end'] and head_proof['absolute_end_ns']==batch['head_end_ns']
  _,d=identity(head);_,state=identity(PHASE)
  assert not d['State']['Running'] and not state['State']['Running']
  if PHASE=='service13a':
   from formal_gate import verify_normal_cache
   path=out/'normal-jit-qualification.json'
   verify_normal_cache(out,src,root,hashlib.sha256(path.read_bytes()).hexdigest())
  event(batch_complete=True,phase=PHASE,head_budget=batch)
 event(phase_passed=PHASE)
 raise SystemExit(0)
assert PHASE!='formal13','formal role requires formal purpose'
if pool_diagnostic:
 diagnostic_end=time.monotonic()+POOL_DIAGNOSTIC_PHASE_BUDGET
 assert PHASE in ('prepare','service13a') and not OBSERVE,'pool diagnostic cannot redispatch/observe or launch other roles'
 assert datetime.datetime.fromisoformat(manifest['cleanup_deadline']).timestamp()-time.time()>=POOL_DIAGNOSTIC_WINDOW,'insufficient pool diagnostic and release reserve'
if manifest.get('scope')=='monitor diagnostics only; no model or pool':
 assert PHASE in ('prepare','monitor-diagnostic'),'diagnostic run forbids G1 workers'
 assert datetime.datetime.fromisoformat(manifest['cleanup_deadline']).timestamp()-time.time()>=900,'insufficient diagnostic and release reserve'
if PHASE!='prepare' or OBSERVE:
 from preflight_control13 import check
 check(out,continuation=OBSERVE)
if PHASE=='prepare' and not OBSERVE:
 if manifest['scope']!='monitor diagnostics only; no model or pool' and not pool_diagnostic:assert datetime.datetime.fromisoformat(manifest['cleanup_deadline']).timestamp()-time.time()>=g1_required_window('prepare'),'insufficient complete G1 and release budget'
 assert call(['timedatectl','show','-p','Timezone','--value'])=='Asia/Shanghai'
 assert not call(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'])
 assert [int(x) for x in call(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits']).splitlines()]==[0]*8
 invoke('prepare_host13.py',SHA,CODE)
 assert json.loads((out/'host-preparation.json').read_text())['gpu_baseline_mib']==[0]*8
 launch('monitor13')
 deadline=time.monotonic()+20
 while not (out/'monitor-v3-ready.json').exists():
  if time.monotonic()>deadline:raise RuntimeError('monitor readiness timeout')
  time.sleep(.5)
 ready()
 if manifest.get('scope')=='monitor diagnostics only; no model or pool' or pool_diagnostic:
  monitor,info=identity('monitor13')
  argv=['/usr/bin/timeout','--version']
  output=call(['docker','exec',monitor['container_id'],*argv],timeout=10)
  assert output.startswith('timeout (GNU coreutils)'), 'required GNU timeout unavailable'
  proof=dict(run_id=RUN,code_commit=CODE,manifest_sha256=SHA,boot_id=manifest['runtime_identity']['boot_id'],container_id=monitor['container_id'],image_id=info['Image'],argv=argv,returncode=0,stdout=output,stdout_sha256=hashlib.sha256(output.encode()).hexdigest())
  with (out/'diagnostic-timeout-probe.json').open('x') as stream:json.dump(proof,stream,indent=2)
elif PHASE=='prepare':
 ready()
 proof=json.loads((out/'host-preparation.json').read_text());assert proof['code_commit']==CODE and proof['manifest_sha256']==SHA
 event(phase_passed=PHASE)
else:
 ready()
 deadline=datetime.datetime.fromisoformat(json.loads((out/'run-manifest.json').read_text())['cleanup_deadline']).timestamp()
 required=900 if diagnostic else g1_required_window(PHASE)
 manifest=json.loads((out/'run-manifest.json').read_text())
 if diagnostic:
  assert not OBSERVE,'interrupted diagnostic must be cleaned up, never redispatched'
  assert manifest['scope']=='monitor diagnostics only; no model or pool' and manifest.get('monitor_diagnostic_trace') is True
 if pool_diagnostic:required=POOL_DIAGNOSTIC_WINDOW
 if not OBSERVE:assert deadline-time.time()>=required,'insufficient full remaining budget'
 if PHASE=='ray13b':assert json.loads((out/'ray13a-supervisor-result.json').read_text())['status']=='passed including cleanup' and (out/'release60-ray13a.json').is_file()
 if PHASE=='service13a' and not pool_diagnostic:assert json.loads((out/'ray13b-supervisor-result.json').read_text())['status']=='passed including cleanup' and (out/'release60-ray13b.json').is_file()
 head='headservice13a' if diagnostic else 'head'+PHASE
 if not OBSERVE:
  if diagnostic or pool_diagnostic:
   from materialize_control13 import verify_timeout_probe
   _,monitor_info=identity('monitor13')
   verify_timeout_probe(out,monitor_info)
  launch(head)
  r,d=identity(head)
  argv=r['spec']['argv'];port=next(int(x.split('=',1)[1]) for x in argv if x.startswith('--port='))
  deadline_ready=time.monotonic()+45
  while True:
   d=json.loads(call(['docker','inspect',r['container_id']]))[0]
   assert d['State']['Running'],'head exited before GCS ready'
   try:
    with socket.create_connection((d['NetworkSettings']['Networks']['bridge']['IPAddress'],port),timeout=1):pass
    break
   except OSError:
    if time.monotonic()>deadline_ready:raise RuntimeError('GCS TCP readiness timeout')
    time.sleep(1)
  event(head_running_gcs_tcp_ready=True)
  if PHASE=='service13a' or diagnostic:
   cpu_imports(r,'service13a')
  if not diagnostic:launch(PHASE)
 else:
  r,_=identity(head)
 if diagnostic:
  # Keep the original dispatch budget, including head start/import/stop.
  ready()
  _,d=identity(head)
  if d['State']['Running']:call(['docker','stop','--time=5',r['container_id']],timeout=25)
  stop_ns=time.monotonic_ns()
  _,stopped=identity(head)
  assert not stopped['State']['Running'],'diagnostic head retained'
  while True:
   post_stop=ready()
   if post_stop['sample_begin_ns']>=stop_ns:break
   if time.monotonic()>=diagnostic_end:raise TimeoutError('no post-stop sample within original diagnostic budget')
   time.sleep(.2)
  if time.monotonic()>diagnostic_end:raise TimeoutError('original180s diagnostic budget expired')
  with (out/'monitor-diagnostic-result.json').open('x') as stream:
   json.dump(dict(run_id=RUN,code_commit=CODE,manifest_sha256=SHA,status='CPU phase completed; trace and release still require review',head_container_id=r['container_id'],stop_ns=stop_ns,post_stop_sample_begin_ns=post_stop['sample_begin_ns'],end_ns=time.monotonic_ns(),scope='no model or pool'),stream,indent=2)
 else:
  worker,state=identity(PHASE)
  budget=POOL_DIAGNOSTIC_BUDGET if pool_diagnostic else (330 if PHASE.startswith('ray') else 3420)
  remaining=budget-(time.monotonic_ns()-worker['dispatch_monotonic_ns'])/1e9
  if state['State']['Running']:
   assert remaining>0 and deadline-time.time()>=remaining+180,'original budget expired'
   exit_code=call(['docker','wait',worker['container_id']],timeout=remaining+20)
  else:exit_code=str(state['State']['ExitCode'])
  assert exit_code=='0','worker exit '+exit_code
  assert json.loads((out/(PHASE+'-supervisor-result.json')).read_text())['status']=='passed including cleanup'
  if PHASE.startswith('ray'):assert json.loads((out/('ray-only-'+PHASE)/'result.json').read_text())['status']=='passed'
  _,d=identity(head)
  if d['State']['Running']:call(['docker','stop','--time=5',r['container_id']],timeout=25)
  if pool_diagnostic:
   pool_proof=json.loads((out/'service-bootstrap-service13a/pool-diagnostic-result.json').read_text())
   assert pool_proof['run_id']==RUN and pool_proof['pool_bytes']==768799145984 and pool_proof['chunk_bytes']==536870912 and not pool_proof['checkpoint_read'] and not pool_proof['model_registered']
   teardown=json.loads((out/'service-bootstrap-service13a/pool-diagnostic-teardown.json').read_text())
   assert teardown[-1]['stage']=='children_reaped'
   stopped_ns=time.monotonic_ns();fresh_limit=time.monotonic()+5
   while True:
    post_stop=ready()
    if post_stop['sample_begin_ns']>=stopped_ns and post_stop['registered_store_helper'] is None and not post_stop['gpu_pids']:break
    if time.monotonic()>=fresh_limit:raise TimeoutError('no fresh post-pool teardown sample')
    time.sleep(.1)
   if time.monotonic()>diagnostic_end:raise TimeoutError('original pool phase budget expired')
   event(pool_teardown_sample_begin_ns=post_stop['sample_begin_ns'],stopped_ns=stopped_ns)
  if PHASE.startswith('ray'):
   release=out/('release60-'+PHASE+'.json')
   if not release.exists():invoke('release_control13.py',PHASE,timeout=180)
   from accept_control13 import validate_release
   validate_release(json.loads(release.read_text()),PHASE,RUN,json.loads((out/'host-preparation.json').read_text())['gpu_baseline_mib'])
 event(phase_passed=PHASE)
'''

RECONCILE=r'''
import pathlib,json,hashlib,subprocess,time
root=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8');src=root/'code/experiment';out=root/'validation/controls'/RUN
assert hashlib.sha256((src/'control-input-manifest.json').read_bytes()).hexdigest()==SHA
import sys
sys.path.insert(0,str(src))
from budget_control13 import POOL_DIAGNOSTIC_SCOPE
if PHASE=='monitor-diagnostic' or json.loads((out/'run-manifest.json').read_text()).get('scope')==POOL_DIAGNOSTIC_SCOPE:raise RuntimeError('interrupted diagnostic requires own cleanup; never redispatch')
from preflight_control13 import check
check(out,continuation=True)
r=json.loads((out/'monitor-v3-ready.json').read_text())
from gpu_identity13 import require_monitor_ready
require_monitor_ready(r,time.monotonic_ns(),RUN,json.loads((out/'run-manifest.json').read_text())['runtime_identity']['boot_id'])
name='monitor13' if PHASE=='prepare' else PHASE
record=json.loads((out/(name+'-identity.json')).read_text())
assert record['run_id']==RUN and record['code_commit']==CODE and record['manifest_sha256']==SHA
info=json.loads(subprocess.check_output(['docker','inspect',record['container_id']],text=True,timeout=10))[0]
assert info['Config']['Labels']['sllm.run_id']==RUN and info['Config']['Labels']['sllm.uid']=='2147'
assert info['Image']==json.loads((out/'run-manifest.json').read_text())['image_id']
if info['State']['Running']:assert info['State']['Pid']==record['pid']
if PHASE=='prepare':
    proof=json.loads((out/'host-preparation.json').read_text());assert proof['code_commit']==CODE and proof['manifest_sha256']==SHA
    assert info['State']['Running'];status='completed_verified'
else:
    proof_path=out/(PHASE+'-supervisor-result.json')
    proof=json.loads(proof_path.read_text()) if proof_path.exists() else None
    if info['State']['Running']:
        config=json.loads((out/'run-manifest.json').read_text())
        if config.get('execution_purpose')=='formal-continuation':
            from budget_control13 import load_formal_batch
            batch=load_formal_batch(out,PHASE)
            assert time.monotonic_ns()<batch['total_end_ns']
        else:
            budget=330 if PHASE.startswith('ray') else 3420
            assert (time.monotonic_ns()-record['dispatch_monotonic_ns'])/1e9<budget
        assert proof is None or proof['status']=='passed including cleanup'
        status='running_verified'
    elif info['State']['ExitCode']==0 and proof and proof['status']=='passed including cleanup':status='completed_verified'
    else:status='failed_verified'
print(json.dumps(dict(kind='control_reconciliation',run_id=RUN,phase=PHASE,code_commit=CODE,manifest_sha256=SHA,status=status,container_id=record['container_id'],state=info['State'])))
'''

def bound_prefix(run,phase,code,sha,observe,segment):
    return '\n'.join(k+'='+repr(v) for k,v in dict(RUN=run,PHASE=phase,CODE=code,SHA=sha,OBSERVE=observe,SEGMENT=segment).items())+'\n'

def probe_existing(prefix,run,phase,code,sha,folder,segment):
    path=folder/('transport-reconciliation-'+phase+'-'+segment+'.json')
    try:
        result=subprocess.run(SSH,input=prefix+RECONCILE,text=True,encoding='utf-8',capture_output=True,timeout=25)
        payload=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
        rows=[]
        for line in result.stdout.splitlines():
            try:row=json.loads(line)
            except ValueError:continue
            if isinstance(row,dict) and row.get('kind')=='control_reconciliation':rows.append(row)
        if result.returncode==0 and len(rows)==1:
            row=rows[0]
            if all(row.get(k)==v for k,v in dict(run_id=run,phase=phase,code_commit=code,manifest_sha256=sha).items()):
                payload['verified_status']=row.get('status')
    except subprocess.TimeoutExpired:
        payload=dict(error='bounded reconciliation timed out; state unknown')
    with path.open('x',encoding='utf-8') as stream:json.dump(payload,stream,indent=2)
    return payload.get('verified_status','unknown')

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    phases=('prepare','ray13a','ray13b','service13a','formal13','monitor-diagnostic')
    parser.add_argument('phase',choices=phases+tuple('observe-'+p for p in phases))
    parser.add_argument('code_commit');parser.add_argument('manifest_sha256');parser.add_argument('verdict')
    args=parser.parse_args(argv)
    code,sha=args.code_commit,args.manifest_sha256
    run=load_context(code,sha,args.verdict)
    observe=args.phase.startswith('observe-');phase=args.phase.removeprefix('observe-')
    segment=uuid.uuid4().hex[:12]
    folder=REPO.parent/'outputs'/('g1-'+run);folder.mkdir(exist_ok=True)
    prefix=bound_prefix(run,phase,code,sha,observe,segment)
    path=folder/('operator-'+args.phase+'-'+segment+'-client.log')
    diagnostic=json.loads((REPO/'runtime/active/run-manifest.json').read_text()).get('scope')=='monitor diagnostics only; no model or pool'
    from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_BUDGET,POOL_DIAGNOSTIC_PHASE_BUDGET,POOL_DIAGNOSTIC_WINDOW
    pool_diagnostic=json.loads((REPO/'runtime/active/run-manifest.json').read_text()).get('scope')==POOL_DIAGNOSTIC_SCOPE
    from budget_control13 import G1_PHASE_BUDGETS
    client_timeout=(300 if phase=='prepare' else (POOL_DIAGNOSTIC_PHASE_BUDGET+30 if pool_diagnostic else 210)) if diagnostic or pool_diagnostic else G1_PHASE_BUDGETS.get(phase,14400)+30
    if json.loads((REPO/'runtime/active/run-manifest.json').read_text()).get('execution_purpose')=='formal-continuation':
        client_timeout=330 if phase=='prepare' else (3930 if phase=='service13a' else 14430)
    with path.open('x',encoding='utf-8') as stream:
        try:
            result=subprocess.run(SSH,input=prefix+REMOTE,text=True,encoding='utf-8',stdout=stream,stderr=subprocess.STDOUT,timeout=client_timeout)
        except subprocess.TimeoutExpired:
            stream.write('bounded client SSH timeout; execution unknown; own cleanup required\n')
            result=subprocess.CompletedProcess(SSH,124)
    print(json.dumps(dict(run_id=run,phase=args.phase,returncode=result.returncode,log=str(path))))
    if result.returncode==0:return 0
    if result.returncode==255:
        status=probe_existing(prefix,run,phase,code,sha,folder,segment)
        if status in ('running_verified','completed_verified'):
            print(json.dumps(dict(status='transport interrupted; existing execution verified; observe only',next_phase='observe-'+phase,formal_or_G1_pass=False)))
            return 75
    # No confirmed safe continuation: request exact-owned cleanup; never call it release evidence.
    if not (folder/'cleanup-requested').exists():
        cleanup=subprocess.run([sys.executable,'-X','utf8',str(REPO/'tools/root_control13.py'),'cleanup',code,sha,args.verdict])
        if cleanup.returncode:return cleanup.returncode
    return 1

if __name__=='__main__':raise SystemExit(main())
