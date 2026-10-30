"""Tiny local fixtures exercise actual AST functions/statements of the readonly auditor."""
import ast,pathlib,json,gzip,hashlib,copy,tempfile,datetime
base=pathlib.Path(__file__).resolve().parent;source=base/'formal-raw-audit.py';tree=ast.parse(source.read_text());names={'require','load','require_cache_condition','require_pool_confirmations','cache','require_attempt_contract','issued_batch_from_evidence'}
functions=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[])
receipts=[]
def expect(label, action, rejects=False):
 try:action()
 except (ValueError,KeyError,AssertionError) as error:
  assert rejects,(label,str(error));receipts.append(dict(case=label,expected='reject',actual='reject',error=str(error)))
 else:
  assert not rejects,label;receipts.append(dict(case=label,expected='pass',actual='pass'))
with tempfile.TemporaryDirectory(dir=base) as temp:
 out=pathlib.Path(temp);closure={'/fixture/x':8192};ns=dict(pathlib=pathlib,json=json,hashlib=hashlib,gzip=gzip,out=out,assets=out,receipts=[],closures={'store_inputs':closure,'safetensors_inputs':closure},vectors=0,vector_pages=0,cache_proofs=[],vector_receipts=[])
 budget_source=pathlib.Path('runtime/active/budget_control13.py');budget_tree=ast.parse(budget_source.read_text());budget_nodes=[n for n in budget_tree.body if (isinstance(n,ast.FunctionDef) and n.name in ('formal_batch_budget','iso_time_ns')) or (isinstance(n,ast.Assign) and any(isinstance(v,ast.Name) and v.id.startswith(('FORMAL_','NORMAL_BATCH_')) for v in n.targets))]
 exec(compile(ast.Module(body=budget_nodes,type_ignores=[]),str(budget_source),'exec'),ns)
 exec(compile(functions,str(source),'exec'),ns)
 def fixture(mode, flags):
  row=dict(source_path='/fixture/x',bytes=8192,pages=2,page_size=4096,vector='x.gz',vector_sha256=hashlib.sha256(flags).hexdigest(),resident_pages=sum(v&1 for v in flags))
  return dict(kind='store_inputs',mode=mode,cache_condition_passed=None if mode=='check' else True,files=[row],preparation_complete_ns=10,verification_begin_ns=11,verification_complete_ns=12)
 def invoke(summary,flags=b'\x01\x00',mode='check',kind='store_inputs'):
  p=out/'fixture';p.mkdir(exist_ok=True);(p/'summary.json').write_text(json.dumps(summary));(p/'x.gz').write_bytes(gzip.compress(flags));return ns['cache']('fixture',mode,kind)
 valid=fixture('check',b'\x01\x00')
 expect('check permits partial residency with full vector',lambda:invoke(valid))
 expect('cold all pages absent',lambda:invoke(fixture('cold',b'\x00\x00'),b'\x00\x00','cold'))
 expect('hot all pages present',lambda:invoke(fixture('hot',b'\x01\x01'),b'\x01\x01','hot'))
 changes=[('wrong target kind',lambda r:r.update(kind='safetensors_inputs')),('wrong target mode',lambda r:r.update(mode='cold')),('check cannot claim pass',lambda r:r.update(cache_condition_passed=True)),('missing input',lambda r:r.update(files=[])),('wrong resident count',lambda r:r['files'][0].update(resident_pages=0)),('wrong source size',lambda r:r['files'][0].update(bytes=8193)),('wrong vector SHA',lambda r:r['files'][0].update(vector_sha256='0'*64)),('cache boundary order',lambda r:r.update(verification_begin_ns=9)),('escaped vector',lambda r:r['files'][0].update(vector='../x.gz'))]
 for label,change in changes:
  r=copy.deepcopy(valid);change(r);expect(label,lambda r=r:invoke(r),True)
 expect('cold rejects resident page',lambda:invoke(fixture('cold',b'\x01\x00'),b'\x01\x00','cold'),True)
 expect('hot rejects absent page',lambda:invoke(fixture('hot',b'\x01\x00'),b'\x01\x00','hot'),True)
 confirmations={f'vllm/glm51-fp8/rank_{r}':True for r in range(8)}
 expect('all eight pool confirmations',lambda:ns['require_pool_confirmations'](confirmations))
 for label,values in [('missing pool rank',{k:v for k,v in confirmations.items() if not k.endswith('7')}),('false pool rank',dict(confirmations,**{'vllm/glm51-fp8/rank_7':False})),('extra pool rank',dict(confirmations,extra=True)),('truthy nonbool pool rank',dict(confirmations,**{'vllm/glm51-fp8/rank_7':1}))]:
  expect(label,lambda values=values:ns['require_pool_confirmations'](values),True)
 def require_expression(message):
  return next(n for n in ast.walk(tree) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='require' and any(isinstance(a,ast.Constant) and a.value==message for a in n.value.args))
 prefix=ast.Module(body=[next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='expected_order' for t in n.targets)),require_expression('executed original prefix')],type_ignores=[])
 def check_prefix(actual):ns.update(remaining=['a','b','c'],actual=actual);exec(compile(prefix,str(source),'exec'),ns)
 expect('original prefix',lambda:check_prefix({'a','b'}))
 for label,values in [('skipped first ID',{'b'}),('nonprefix later ID',{'a','c'}),('extra unknown ID',{'a','z'}),('empty batch',set())]:expect(label,lambda values=values:check_prefix(values),True)
 norm=ast.Module(body=[require_expression('batch initial ledger identity/status/hash; host/container paths differ')],type_ignores=[])
 def normalized(before,after):ns.update(initial_rows=before,prior=after);exec(compile(norm,str(source),'exec'),ns)
 before=[dict(attempt_id='a',status='success',result_sha256='abc',result_path='/experiment/a')];after=[dict(before[0],result_path='/host/a')]
 expect('host container path translation only',lambda:normalized(before,after))
 for field,value in [('status','failed'),('result_sha256','bad'),('attempt_id','b')]:
  changed=[dict(after[0],**{field:value})];expect('initial ledger '+field+' drift',lambda changed=changed:normalized(before,changed),True)
 attempt=dict(attempt_id='x',block=4,condition='store_pool_hit',status='preregistered_not_started');prereg=dict(attempt_timeout_seconds=1200);batch=dict(work_end_ns=1500*10**9)
 record=dict(attempt_id='x',block=4,condition='store_pool_hit',status='success',success=True,preparation_begin_ns=100*10**9,attempt_deadline_ns=1300*10**9,batch_work_end_ns=batch['work_end_ns'],memory_events_before='oom 0',code_commit='code',source_manifest_sha256='manifest',execution_run_id='run',protocol_revision='protocol',preparation_revision='prep',resource_window_revision='window')
 origin=dict(record,status='started',success=False)
 def contract(r=record,o=origin):ns['require_attempt_contract'](r,o,attempt,prereg,batch)
 expect('issued attempt budget and prereg fields',contract)
 for label,r,o in [('extended selfreported attempt deadline',dict(record,attempt_deadline_ns=1400*10**9),dict(origin,attempt_deadline_ns=1400*10**9)),('replaced batch deadline',dict(record,batch_work_end_ns=1600*10**9),dict(origin,batch_work_end_ns=1600*10**9)),('condition drift',dict(record,condition='store_disk_cold'),dict(origin,condition='store_disk_cold')),('block drift',dict(record,block=5),dict(origin,block=5)),('origin immutable drift',record,dict(origin,code_commit='other'))]:expect(label,lambda r=r,o=o:contract(r,o),True)
 clipped=dict(record,preparation_begin_ns=500*10**9,attempt_deadline_ns=1500*10**9);clipped_origin=dict(clipped,status='started',success=False)
 expect('single deadline clipped to issued batch',lambda:contract(clipped,clipped_origin))
 stop_node=ast.Module(body=[require_expression('budget stop')],type_ignores=[])
 good_stop=dict(next_unstarted_attempt='next',work_end_ns=batch['work_end_ns'],resource_deadline=2000.0,observed_ns=batch['work_end_ns']-1000*10**9)
 def budget_stop(stop):ns.update(stop=stop,next_unstarted='next',batch=dict(batch,resource_deadline_ns=2000*10**9));exec(compile(stop_node,str(source),'exec'),ns)
 expect('stop bound to issued work end',lambda:budget_stop(good_stop))
 for key,value in [('work_end_ns',batch['work_end_ns']+1),('resource_deadline',2100.0),('next_unstarted_attempt','other')]:expect('stop '+key+' drift',lambda key=key,value=value:budget_stop(dict(good_stop,**{key:value})),True)
 config=dict(scope='formal continuation of original forty IDs',execution_purpose='formal-continuation',run_id='run',runtime_identity={'boot_id':'recorded-boot'},cleanup_deadline='2026-10-27T13:50:00+08:00')
 deployment=dict(run_id='run',code_commit='code',input_manifest_sha256='mf')
 window=dict(cleanup_deadline=config['cleanup_deadline'],bookings=[{'start_ms':1793062800000,'end_ms':1793080800000}])
 wall=ns['iso_time_ns']('2026-10-27T09:57:36+08:00');issued=ns['formal_batch_budget'](1000000,wall,ns['iso_time_ns'](config['cleanup_deadline']))
 dispatch=dict(spec={'name':'headformal13'},run_id='run',boot_id='recorded-boot',code_commit='code',manifest_sha256='mf',batch_budget=issued,dispatch_monotonic_ns=1000000,dispatch_utc='2026-10-27T09:57:36+08:00')
 def history(d=dispatch):assert ns['issued_batch_from_evidence'](d,config,deployment,window)==issued
 expect('historical issued budget',history)
 from unittest import mock
 with mock.patch('time.time',return_value=4070908800.0),mock.patch('time.time_ns',return_value=4070908800*10**9),mock.patch('pathlib.Path.read_text',return_value='different-current-boot'):
  expect('valid historical budget after window/current boot changed',history)
 expect('recorded boot mismatch',lambda:history(dict(dispatch,boot_id='wrong')),True)
 expect('recorded batch extension',lambda:history(dict(dispatch,batch_budget=dict(issued,work_end_ns=issued['work_end_ns']+1))),True)
 expect('recorded wall timestamp drift',lambda:history(dict(dispatch,dispatch_utc='2026-10-27T10:57:36+08:00')),True)
 historical_function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='issued_batch_from_evidence')
 assert not any(isinstance(n,ast.Attribute) and n.attr in ('now','time','time_ns','read_text') for n in ast.walk(historical_function))
report=dict(status='local CPU fixture checks passed',source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),cases=len(receipts),passed=len(receipts),checks=receipts,scope='synthetic fixtures only; no remote read or measurement acceptance')
(base/'formal-audit-negative-tests.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8',newline='\n');print(json.dumps({k:v for k,v in report.items() if k!='checks'}))
