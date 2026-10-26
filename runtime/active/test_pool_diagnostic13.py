"""Execute loaded-pool admission/prefix/exit on CPU fixtures; never allocate a pool."""
import ast,json,pathlib,tempfile,time,types,unittest,signal
import urllib.request
from unittest.mock import patch
import materialize_control13 as materialize
from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_BUDGET,require_budget

SOURCE=pathlib.Path(__file__).resolve().parent

class PoolDiagnostic(unittest.TestCase):
    def test_grpc_probe_flag_is_rejected_outside_cpu_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for scope in (POOL_DIAGNOSTIC_SCOPE,'G1 independent untimed controls only; zero formal IDs'):
                (root/'run-manifest.json').write_text(json.dumps(dict(scope=scope,grpc_shutdown_probe=True)))
                with self.assertRaisesRegex(RuntimeError,'CPU-only'):materialize.validate_scope(root,'service13a')

    def test_only_fixed_roles_and_real_budget_derived(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);run='test-run'
            (root/'run-manifest.json').write_text(json.dumps(dict(run_id=run,scope=POOL_DIAGNOSTIC_SCOPE,monitor_diagnostic_trace=True)))
            head=materialize.expected_spec(root,'headservice13a')
            self.assertEqual(head['argv'][:4],['/usr/bin/timeout','--signal=TERM','--kill-after=5s','1195s'])
            info=json.dumps([dict(State=dict(Running=True),Config=dict(Labels={'sllm.run_id':run,'sllm.uid':'2147'}),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress='172.17.0.2'))))])
            with patch.object(materialize,'call',return_value=info):worker=materialize.expected_spec(root,'service13a')
            self.assertEqual(worker['env']['SLLM_POOL_DIAGNOSTIC'],'1')
            self.assertEqual(worker['env']['STORAGE_PATH'],'/r/service-bootstrap-service13a/store')
            self.assertFalse(worker['phase_trace'])
            for key in ('SLLM_VALIDATE_FULL_MODEL','SLLM_VERIFY_WEIGHTS','SLLM_SERVICE_WEIGHT_AUDIT_DIR','VLLM_ALLOW_INSECURE_SERIALIZATION'):
                self.assertNotIn(key,worker['env'])
            self.assertEqual(require_budget('service13a',2400,pool_diagnostic=True),900)
            for name in ('monitor13','headservice13a','service13a'):
                with self.assertRaises(RuntimeError):require_budget(name,2399.999,pool_diagnostic=True)
            for name in ('ray13a','headray13a','ray13b','formal13'):
                with self.assertRaises(RuntimeError):materialize.expected_spec(root,name)
                with self.assertRaises(RuntimeError):require_budget(name,10000,pool_diagnostic=True)

    def prefix(self,flag='1',pool_bytes=768799145984):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=pathlib.Path(temporary.name);assets=root/'assets';control=root/'control'
        (assets/'validation').mkdir(parents=True);control.mkdir()
        (control/'run-manifest.json').write_text(json.dumps(dict(scope=POOL_DIAGNOSTIC_SCOPE)))
        (assets/'validation/store-restore-validation.json').write_text(json.dumps(dict(status='full Store component restore validation passed',pool_bytes=pool_bytes,chunk_bytes=536870912)))
        tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text());body=[]
        for node in tree.body:
            if isinstance(node,ast.FunctionDef):break
            body.append(node)
        actual_path=pathlib.Path
        fake_path=types.SimpleNamespace(Path=lambda value:assets if str(value)=='/experiment' else actual_path(value))
        workspace=types.SimpleNamespace(run_root=lambda:control,verify_input_layout=lambda:(_ for _ in ()).throw(AssertionError('checkpoint layout should not be read by pool-only bootstrap')))
        env={'SLLM_PRIVATE_RAY_ADDRESS':'172.17.0.2:26532','SLLM_POOL_DIAGNOSTIC':flag}
        scope={}
        with patch.dict(__import__('os').environ,env,clear=True),patch.object(__import__('sys'),'argv',['bootstrap','service13a']),patch.dict(__import__('sys').modules,dict(pathlib=fake_path,workspace=workspace,socket=types.SimpleNamespace(gethostname=lambda:'cpu-fixture',gethostbyname=lambda name:'172.17.0.3'))):
            exec(compile(ast.Module(body=body,type_ignores=[]),'actual-bootstrap-prefix','exec'),scope)
        return scope

    def test_actual_prefix_uses_full_pool_and_empty_own_storage(self):
        scope=self.prefix();self.assertEqual(scope['pool_bytes'],768799145984);self.assertEqual(scope['chunk_bytes'],536870912)
        self.assertFalse(scope['validate_model']);self.assertEqual(list(pathlib.Path(scope['env']['STORAGE_PATH']).iterdir()),[])
        self.assertEqual(pathlib.Path(scope['env']['STORAGE_PATH']).parent,scope['out'])

    def test_invalid_marker_or_small_pool_rejected_before_launch(self):
        with self.assertRaises(RuntimeError):self.prefix(flag='0')
        with self.assertRaises(AssertionError):self.prefix(pool_bytes=67108864)

    def ready_branch(self,extra_gpu=False,missing_gpu=False):
        with tempfile.TemporaryDirectory() as directory:
            control=pathlib.Path(directory);out=control/'service-bootstrap-service13a';out.mkdir()
            launch=dict(pid=1124,pid_namespace='pid:[42]',start_ticks='333',boot_id='boot')
            service=dict(container_id='c'*64,cgroup='0::/own/container\n')
            helper=dict(host_pid=9001,namespace_pid=1124,pid_namespace='pid:[42]',start_ticks='333',boot_id='boot',container_id='c'*64,cgroup=service['cgroup'],uid=2147)
            stamp=time.monotonic_ns();snapshot=dict(run_id='test-run',boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=stamp,sample_begin_ns=stamp,sample_end_ns=stamp,history_ok=True,first_monitor_error=None,registered_store_helper=helper,gpu_pids=[9001,9002] if extra_gpu else [9001])
            if missing_gpu:del snapshot['gpu_pids']
            for path,value in [(out/'store-numa-launch.json',launch),(control/'service13a-identity.json',service),(out/'pool-ready.json',dict(monotonic_ns=stamp-1)),(control/'monitor-v3-ready.json',snapshot)]:path.write_text(json.dumps(value))
            class Process:
                pid=123;returncode=None
                def poll(self):return self.returncode
                def terminate(self):self.returncode=0
                def send_signal(self,signum):self.returncode=0
                def wait(self,**kwargs):return self.returncode
                def kill(self):self.returncode=-9
            tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text());body=next(n for n in tree.body if isinstance(n,ast.Try))
            index=next(i for i,n in enumerate(body.body) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='pool_diagnostic')
            mini=ast.Try(body=body.body[index:index+2],handlers=[],orelse=[],finalbody=body.finalbody)
            scope=dict(store=None,signal=types.SimpleNamespace(alarm=lambda n:None,SIGINT=signal.SIGINT),STORE_SHUTDOWN_GRACE=240,pool_diagnostic=True,out=out,control=control,manifest=dict(run_id='test-run',runtime_identity=dict(boot_id='boot')),json=json,time=time,pool_bytes=768799145984,chunk_bytes=536870912,processes=[Process()],logs=[],env={},python='fixture',subprocess=types.SimpleNamespace(run=lambda *args,**kwargs:None),launch=lambda *args:(_ for _ in ()).throw(AssertionError('native SLLM service reached')))
            scope['store']=scope['processes'][0]
            with self.assertRaises((RuntimeError,KeyError) if extra_gpu or missing_gpu else SystemExit) as error:exec(compile(ast.fix_missing_locations(ast.Module(body=[mini],type_ignores=[])),'actual-pool-ready-finally','exec'),scope)
            if not (extra_gpu or missing_gpu):self.assertEqual(error.exception.code,0)
            stages=json.loads((out/'pool-diagnostic-teardown.json').read_text());self.assertEqual(stages[-1]['stage'],'children_reaped')
            self.assertEqual((out/'pool-diagnostic-result.json').is_file(),not (extra_gpu or missing_gpu))

    def test_actual_ready_branch_exits_through_finally_before_sllm(self):self.ready_branch()

    def test_extra_own_gpu_pid_blocks_completion_and_still_cleans_up(self):self.ready_branch(extra_gpu=True)

    def test_missing_gpu_pid_list_blocks_completion_and_still_cleans_up(self):self.ready_branch(missing_gpu=True)

    def test_interrupted_finally_keeps_flushed_begin_without_fake_end(self):
        with tempfile.TemporaryDirectory() as directory:
            out=pathlib.Path(directory)
            class Process:
                pid=123;returncode=None
                def poll(self):return self.returncode
                def terminate(self):self.returncode=0
            def fail(*args,**kwargs):raise RuntimeError('interrupted Ray cleanup')
            tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text());body=next(n for n in tree.body if isinstance(n,ast.Try))
            scope=dict(store=None,signal=types.SimpleNamespace(alarm=lambda n:None,SIGINT=signal.SIGINT),STORE_SHUTDOWN_GRACE=240,pool_diagnostic=True,out=out,control=out,manifest=dict(run_id='r',runtime_identity=dict(boot_id='b')),time=time,json=json,processes=[Process()],logs=[],env={},python='fixture',subprocess=types.SimpleNamespace(run=fail))
            with self.assertRaisesRegex(RuntimeError,'interrupted Ray'):exec(compile(ast.Module(body=body.finalbody,type_ignores=[]),'actual-finally-partial','exec'),scope)
            events=[json.loads(line) for line in (out/'pool-diagnostic-teardown.jsonl').read_text().splitlines()]
            self.assertEqual(events[-1]['stage'],'ray_stop_begin')
            self.assertFalse(any(e['stage']=='ray_stop_end' for e in events))
            self.assertFalse((out/'pool-diagnostic-teardown.json').exists())
            self.assertEqual(events[0]['namespace_pid'],123)
            self.assertIsNone(events[0]['container_id'])  # Unavailable, never fabricated.

class GracefulPoolTeardown(unittest.TestCase):
    def execute(self,store_timeout=False,store_rc=0,interrupted=False,pool=True):
        import subprocess
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        out=pathlib.Path(temporary.name);calls=[]
        class Process:
            def __init__(self,pid):self.pid=pid;self.returncode=None
            def poll(self):return self.returncode
            def terminate(self):calls.append(('term',self.pid));self.returncode=1
            def send_signal(self,n):calls.append(('signal',self.pid,n))
            def wait(self,timeout=None):
                calls.append(('wait',self.pid,timeout))
                if self.pid==1 and store_timeout and self.returncode is None:raise subprocess.TimeoutExpired('store',timeout)
                if self.pid==1 and self.returncode is None:self.returncode=store_rc
                return self.returncode
            def kill(self):calls.append(('kill',self.pid));self.returncode=-9
        store=Process(1);worker=Process(2)
        def ray_stop(*a,**kw):
            calls.append(('ray_stop',kw['timeout']))
            if interrupted:raise RuntimeError('interrupted')
            return types.SimpleNamespace(returncode=0)
        # Clock advances during Ray stop: remaining grace cannot restart at wait.
        times=iter([1000000000,2000000000,3000000000,4000000000,5000000000,6000000000,7000000000,8000000000,9000000000,10000000000,11000000000,12000000000,13000000000,14000000000,15000000000,16000000000,17000000000,18000000000,19000000000,20000000000])
        scope=dict(store=store,pool_diagnostic=pool,STORE_SHUTDOWN_GRACE=240,signal=types.SimpleNamespace(SIGINT=signal.SIGINT,alarm=lambda n:calls.append(('alarm',n))),out=out,control=out,manifest=dict(run_id='run',runtime_identity=dict(boot_id='boot')),time=types.SimpleNamespace(monotonic_ns=lambda:next(times)),json=json,processes=[worker,store],logs=[],env={},python='fixture',subprocess=types.SimpleNamespace(run=ray_stop,TimeoutExpired=subprocess.TimeoutExpired))
        tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text());body=next(n for n in tree.body if isinstance(n,ast.Try))
        try:exec(compile(ast.fix_missing_locations(ast.Module(body=body.finalbody,type_ignores=[])),'actual-teardown','exec'),scope)
        except RuntimeError:
            if not (store_timeout or interrupted or store_rc):raise
        else:self.assertFalse(store_timeout or interrupted or store_rc,'failure promoted to success')
        return calls,out

    def test_sigint_exact_store_cancel_alarm_and_grace_from_send(self):
        calls,out=self.execute()
        self.assertEqual(calls[0],('alarm',0));self.assertIn(('signal',1,signal.SIGINT),calls)
        self.assertIn(('term',2),calls);self.assertNotIn(('term',1),calls)
        waits=[row for row in calls if row[:2]==('wait',1)]
        self.assertGreater(waits[0][2],0);self.assertLess(waits[0][2],240)
        rows=json.loads((out/'pool-diagnostic-teardown.json').read_text())
        sent=next(r for r in rows if r['stage']=='interrupt_sent')
        self.assertEqual(sent['grace_deadline_ns']-sent['sent_ns'],240000000000)
        self.assertEqual(rows[-1]['stage'],'children_reaped')

    def test_grace_expiry_kill_bounded_and_not_success(self):
        calls,out=self.execute(store_timeout=True)
        self.assertIn(('kill',1),calls);self.assertIn(('wait',1,5),calls)

    def test_nonzero_store_exit_not_success(self):self.execute(store_rc=1)

    def test_interrupted_ray_keeps_partial_record(self):
        calls,out=self.execute(interrupted=True)
        self.assertFalse((out/'pool-diagnostic-teardown.json').exists())
        self.assertIn('interrupt_sent',(out/'pool-diagnostic-teardown.jsonl').read_text())

    def test_g1_reuses_exact_store_sigint_and_durable_teardown(self):
        calls,out=self.execute(pool=False)
        self.assertIn(('signal',1,signal.SIGINT),calls)
        self.assertEqual(json.loads((out/'service-teardown.json').read_text())[-1]['stage'],'children_reaped')
        self.assertFalse((out/'pool-diagnostic-teardown.json').exists())

    def test_full_g1_phase_and_suffix_budget_include_release(self):
        from budget_control13 import G1_PHASE_BUDGETS,g1_required_window
        self.assertEqual(G1_PHASE_BUDGETS,dict(prepare=300,ray13a=720,ray13b=720,service13a=3900))
        self.assertEqual([g1_required_window(name) for name in G1_PHASE_BUDGETS],[5820,5520,4800,4080])
        self.assertGreater(G1_PHASE_BUDGETS['service13a'],3420+45+90+5+5+60)

    def test_g1_alarm_uses_frozen_work_budget_with_cleanup_reserve(self):
        from budget_control13 import G1_SERVICE_WORK_BUDGET,STORE_SHUTDOWN_GRACE,POOL_DIAGNOSTIC_WORK_BUDGET
        self.assertEqual(G1_SERVICE_WORK_BUDGET,3060)
        self.assertLess(G1_SERVICE_WORK_BUDGET+STORE_SHUTDOWN_GRACE+30+20+15+20,3420-25)
        tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text())
        assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='work_deadline' for x in n.targets))
        alarm=next(n for n in tree.body if isinstance(n,ast.Expr) and ast.unparse(n).startswith('signal.alarm('))
        for pool,budget in [(False,3060),(True,540)]:
            observed=[];scope=dict(time=types.SimpleNamespace(monotonic=lambda:100),signal=types.SimpleNamespace(alarm=observed.append),pool_diagnostic=pool,POOL_DIAGNOSTIC_WORK_BUDGET=POOL_DIAGNOSTIC_WORK_BUDGET,G1_SERVICE_WORK_BUDGET=G1_SERVICE_WORK_BUDGET)
            exec(compile(ast.Module(body=[assignment,alarm],type_ignores=[]),'actual-work-deadline','exec'),scope)
            self.assertEqual(observed,[budget]);self.assertEqual(scope['work_deadline'],100+budget)

    def test_work_and_cleanup_fit_outer_work_deadline(self):
        from budget_control13 import POOL_DIAGNOSTIC_WORK_BUDGET,STORE_SHUTDOWN_GRACE
        self.assertLess(POOL_DIAGNOSTIC_WORK_BUDGET+STORE_SHUTDOWN_GRACE+30+10+5+20,POOL_DIAGNOSTIC_BUDGET-25)
        source=(SOURCE/'bootstrap_control13.py').read_text()
        self.assertIn('min(480 if pool_diagnostic else 1800,work_deadline-time.monotonic())',source)

class SigintAdmission(unittest.TestCase):
    def fixture(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);root=pathlib.Path(temp.name)
        stamp=time.monotonic_ns();cid='c'*64
        manifest=dict(run_id='r',image_id='image',runtime_identity=dict(boot_id='boot'))
        head=dict(run_id='r',container_id=cid,code_commit='code',manifest_sha256='sha',pid=100)
        dispatch=dict(boot_id='boot',dispatch_monotonic_ns=stamp-1000)
        proof=dict(run_id='r',head_container_id=cid,image_id='image',boot_id='boot',code_commit='code',manifest_sha256='sha',signal=2,returncode=0,cleanup_marker='cleanup',interpreter='/experiment/.venv/bin/python',monotonic_ns=stamp)
        for name,data in [('run-manifest.json',manifest),('headservice13a-identity.json',head),('headservice13a-dispatch.json',dispatch),('cpu-import-preflight.json',dict(sigint_probe=proof))]:
            (root/name).write_text(json.dumps(data))
        info=dict(Id=cid,Image='image',State=dict(Running=True,Pid=100),Config=dict(Labels={'sllm.run_id':'r','sllm.uid':'2147'}))
        return root,proof,info

    def test_bound_current_probe_admits(self):
        root,proof,info=self.fixture();materialize.verify_sigint_probe(root,info,'code','sha')

    def test_missing_probe_blocks_direct_launcher(self):
        root,proof,info=self.fixture();(root/'cpu-import-preflight.json').unlink()
        with self.assertRaises(FileNotFoundError):materialize.verify_sigint_probe(root,info,'code','sha')
        tree=ast.parse((SOURCE/'launch_control13.py').read_text())
        gate=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.If) and any(isinstance(x,ast.Name) and x.id=='verify_sigint_probe' for x in ast.walk(n)))
        create=next(i for i,n in enumerate(tree.body) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='args' for x in n.targets))
        self.assertLess(gate,create)

    def test_wrong_bindings_and_failed_markers_reject(self):
        for key,value in dict(run_id='other',head_container_id='d'*64,image_id='other',boot_id='other',code_commit='other',manifest_sha256='other',signal=15,returncode=1,cleanup_marker='ready',interpreter='/usr/bin/python',monotonic_ns=0).items():
            with self.subTest(key=key):
                root,proof,info=self.fixture();proof[key]=value;(root/'cpu-import-preflight.json').write_text(json.dumps(dict(sigint_probe=proof)))
                with self.assertRaises(RuntimeError):materialize.verify_sigint_probe(root,info,'code','sha')

    def test_head_exit_pid_reuse_and_foreign_labels_reject(self):
        for kind in ('stopped','pid','labels'):
            root,proof,info=self.fixture()
            if kind=='stopped':info['State']['Running']=False
            elif kind=='pid':info['State']['Pid']=101
            else:info['Config']['Labels']['sllm.run_id']='other'
            with self.subTest(kind=kind),self.assertRaises(RuntimeError):materialize.verify_sigint_probe(root,info,'code','sha')

if __name__=='__main__':unittest.main()
