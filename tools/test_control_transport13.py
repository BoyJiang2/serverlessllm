"""SSH failures must not restart or kill a verified healthy execution."""
import ast,contextlib,io,json,pathlib,subprocess,sys,tempfile,time,types,unittest
from unittest.mock import patch
import run_control13 as operator

class Transport(unittest.TestCase):
    def exercise(self,status='running_verified',identity_ok=True,initial_rc=255,query_timeout=False,marker=False,diagnostic=False,client_timeout=False):
        with tempfile.TemporaryDirectory() as directory:
            repo=pathlib.Path(directory)/'repo';repo.mkdir()
            run='sllm-glm51fp8-20261026T000000Z-12345678';code='a'*40;sha='b'*64
            (repo/'runtime/active').mkdir(parents=True)
            (repo/'runtime/active/run-manifest.json').write_text(json.dumps(dict(scope='monitor diagnostics only; no model or pool' if diagnostic else 'G1')))
            phase='monitor-diagnostic' if diagnostic else 'service13a'
            folder=repo.parent/'outputs'/('g1-'+run);folder.mkdir(parents=True)
            if marker:(folder/'cleanup-requested').write_text('request only')
            calls=[]
            def execute(args,**kwargs):
                calls.append(args)
                if len(calls)==1:
                    if client_timeout:raise subprocess.TimeoutExpired(args,kwargs['timeout'])
                    if diagnostic:self.assertEqual(kwargs['timeout'],210)
                    return types.SimpleNamespace(returncode=initial_rc)
                if args[0]=='ssh':
                    if query_timeout:raise subprocess.TimeoutExpired(args,25)
                    row=dict(kind='control_reconciliation',run_id=run if identity_ok else 'other-run',phase=phase,
                             code_commit=code,manifest_sha256=sha,status=status)
                    return types.SimpleNamespace(returncode=0,stdout=json.dumps(row),stderr='')
                return types.SimpleNamespace(returncode=0)
            with patch.object(operator,'REPO',repo),patch.object(operator,'load_context',return_value=run), \
                 patch.object(operator.subprocess,'run',side_effect=execute),contextlib.redirect_stdout(io.StringIO()):
                rc=operator.main([phase,code,sha,'reviews/verdict.json'])
            return rc,calls,[p.name for p in folder.iterdir()]

    def test_diagnostic_client_timeout_requests_cleanup_without_redispatch(self):
        rc,calls,_=self.exercise(diagnostic=True,client_timeout=True)
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[-1][4],'cleanup')

    def test_diagnostic_ssh255_unknown_requests_cleanup_without_redispatch(self):
        rc,calls,_=self.exercise(diagnostic=True,status='unknown')
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[-1][4],'cleanup')

    def test_reset_with_verified_running_worker_does_not_cleanup(self):
        rc,calls,files=self.exercise()
        self.assertEqual(rc,75)
        self.assertEqual(len(calls),2)
        self.assertTrue(all(args[0]=='ssh' for args in calls))
        self.assertIn('ServerAliveInterval=15',calls[0])
        self.assertIn('ServerAliveCountMax=4',calls[0])
        self.assertTrue(any(name.startswith('transport-reconciliation-') for name in files))

    def test_completed_worker_requires_observation_not_redispatch(self):
        rc,calls,_=self.exercise(status='completed_verified')
        self.assertEqual(rc,75)
        self.assertEqual(len(calls),2)

    def test_wrong_identity_cannot_authorize_continuation(self):
        rc,calls,_=self.exercise(identity_ok=False)
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),3)
        self.assertEqual(calls[-1][4],'cleanup')

    def test_bounded_query_timeout_remains_unknown_then_requests_cleanup(self):
        rc,calls,_=self.exercise(query_timeout=True)
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),3)

    def test_actual_worker_failure_requests_cleanup(self):
        rc,calls,_=self.exercise(status='failed_verified')
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),3)

    def test_remote_protocol_failure_is_not_a_success(self):
        rc,calls,_=self.exercise(initial_rc=1)
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[-1][4],'cleanup')

    def test_cleanup_request_marker_is_not_release_proof(self):
        rc,calls,_=self.exercise(status='failed_verified',marker=True)
        self.assertEqual(rc,1)
        self.assertEqual(len(calls),2)

    def test_observe_completed_ray_never_materializes_or_starts_container(self):
        with tempfile.TemporaryDirectory() as directory:
            node=pathlib.Path(directory);run='test-run';code='a'*40;sha='b'*64
            out=node/'validation/controls'/run;out.mkdir(parents=True)
            def save(path,value):
                path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
            save(out/'run-manifest.json',dict(run_id=run,image_id='frozen-image',cleanup_deadline='2099-01-01T00:00:00+00:00',runtime_identity=dict(boot_id='boot')))
            stamp=time.monotonic_ns()
            save(out/'monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=stamp,sample_begin_ns=stamp,sample_end_ns=stamp,history_ok=True,first_monitor_error=None))
            for name in ('headray13a','ray13a'):
                save(out/(name+'-identity.json'),dict(run_id=run,code_commit=code,manifest_sha256=sha,
                     container_id=name,pid=1,dispatch_monotonic_ns=time.monotonic_ns()-10_000_000_000))
            save(out/'ray13a-supervisor-result.json',dict(status='passed including cleanup'))
            save(out/'ray-only-ray13a/result.json',dict(status='passed'))
            save(out/'host-preparation.json',dict(gpu_baseline_mib=[0]*8))
            save(out/'release60-ray13a.json',[dict(second=i) for i in range(61)])
            calls=[];validated=[]
            def command(args,**kwargs):
                calls.append(args)
                self.assertEqual(args[:2],['docker','inspect'])
                info=dict(Image='frozen-image',Config=dict(Labels={'sllm.run_id':run,'sllm.uid':'2147'}),State=dict(Running=False,ExitCode=0))
                return types.SimpleNamespace(returncode=0,stdout=json.dumps([info]),stderr='')
            actual_path=pathlib.Path
            def redirect(value):return node if str(value)=='/data/brick1/boyjiang/glm5.1-fp8' else actual_path(value)
            modules=dict(pathlib=types.SimpleNamespace(Path=redirect),subprocess=types.SimpleNamespace(run=command),
                         preflight_control13=types.SimpleNamespace(check=lambda root,**kwargs:None),
                         accept_control13=types.SimpleNamespace(validate_release=lambda *args:validated.append(args)))
            scope=dict(RUN=run,PHASE='ray13a',CODE=code,SHA=sha,OBSERVE=True,SEGMENT='test')
            with patch.dict(sys.modules,modules),contextlib.redirect_stdout(io.StringIO()):
                try:exec(compile(operator.REMOTE,'guarded-observe','exec'),scope)
                finally:
                    if 'log' in scope:scope['log'].close()
            self.assertEqual(len(calls),3)
            self.assertEqual(len(validated),1)
            self.assertEqual(len(validated[0][0]),61)

    def diagnostic(self,trace=True,observe=False,expire=False,post_sample=True,pool_mode=False):
        with tempfile.TemporaryDirectory() as directory:
            node=pathlib.Path(directory);run='test-run';code='a'*40;sha='b'*64
            out=node/'validation/controls'/run;out.mkdir(parents=True)
            def save(name,value):(out/name).write_text(json.dumps(value))
            save('run-manifest.json',dict(run_id=run,image_id='frozen-image',scope='monitor diagnostics with resident Store only; no checkpoint or model' if pool_mode else 'monitor diagnostics only; no model or pool',monitor_diagnostic_trace=trace,cleanup_deadline='2099-01-01T00:00:00+00:00',runtime_identity=dict(boot_id='boot')))
            save('monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=100_000_000_000,sample_begin_ns=100_000_000_000,sample_end_ns=100_000_000_000,history_ok=True,first_monitor_error=None,registered_store_helper=None,gpu_pids=[]))
            save('monitor13-identity.json',dict(run_id=run,code_commit=code,manifest_sha256=sha,container_id='own-monitor',pid=1))
            if pool_mode:
                save('service13a-identity.json',dict(run_id=run,code_commit=code,manifest_sha256=sha,container_id='own-worker',pid=1,dispatch_monotonic_ns=100_000_000_000))
                save('service13a-supervisor-result.json',dict(status='passed including cleanup'))
                (out/'service-bootstrap-service13a').mkdir()
                save('service-bootstrap-service13a/pool-diagnostic-result.json',dict(run_id=run,pool_bytes=768799145984,chunk_bytes=536870912,checkpoint_read=False,model_registered=False))
                save('service-bootstrap-service13a/pool-diagnostic-teardown.json',[dict(stage='children_reaped')])
            save('headservice13a-identity.json',dict(run_id=run,code_commit=code,manifest_sha256=sha,container_id='own-head',pid=1,spec=dict(argv=['--port=26532'])))
            calls=[];clock=[100.0];running=[True]
            def command(args,**kwargs):
                calls.append((args,kwargs.get('timeout')))
                if args[:2]==['docker','inspect']:
                    info=dict(Image='frozen-image',Config=dict(Labels={'sllm.run_id':run,'sllm.uid':'2147'}),State=dict(Running=False if args[-1]=='own-worker' else running[0],Pid=1,ExitCode=0),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress='172.17.0.2'))))
                    stdout=json.dumps([info])
                else:stdout=''
                if args[:2]==['docker','stop']:
                    running[0]=False
                    clock[0]+=.1
                    if post_sample:save('monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=int(clock[0]*1e9),sample_begin_ns=int(clock[0]*1e9),sample_end_ns=int(clock[0]*1e9),history_ok=True,first_monitor_error=None,registered_store_helper=None,gpu_pids=[]))
                if args[:2]==['docker','exec'] and expire:
                    clock[0]=281.0
                    save('monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=int(clock[0]*1e9),sample_begin_ns=int(clock[0]*1e9),sample_end_ns=int(clock[0]*1e9),history_ok=True,first_monitor_error=None,registered_store_helper=None,gpu_pids=[]))
                return types.SimpleNamespace(returncode=0,stdout=stdout,stderr='')
            actual_path=pathlib.Path
            def redirect(value):return node if str(value)=='/data/brick1/boyjiang/glm5.1-fp8' else actual_path(value)
            modules=dict(pathlib=types.SimpleNamespace(Path=redirect),subprocess=types.SimpleNamespace(run=command),
                         preflight_control13=types.SimpleNamespace(check=lambda root,**kwargs:None),
                         socket=types.SimpleNamespace(create_connection=lambda *args,**kwargs:contextlib.nullcontext()),
                         materialize_control13=types.SimpleNamespace(verify_timeout_probe=lambda *args:None),
                         time=types.SimpleNamespace(monotonic=lambda:clock[0],monotonic_ns=lambda:int(clock[0]*1e9),time=time.time,sleep=lambda seconds:clock.__setitem__(0,clock[0]+seconds)))
            scope=dict(RUN=run,PHASE='service13a' if pool_mode else 'monitor-diagnostic',CODE=code,SHA=sha,OBSERVE=observe,SEGMENT='test')
            error=None
            with patch.dict(sys.modules,modules),contextlib.redirect_stdout(io.StringIO()):
                try:exec(compile(operator.REMOTE,'diagnostic-actual-remote','exec'),scope)
                except BaseException as failure:error=failure
                finally:
                    if 'log' in scope:scope['log'].close()
            return calls,error,(out/'monitor-diagnostic-result.json').exists()

    def test_diagnostic_actual_branch_never_launches_worker_or_pool(self):
        calls,error,receipt=self.diagnostic()
        self.assertIsNone(error)
        self.assertTrue(receipt)
        materialized=[args[-1] for args,_ in calls if 'materialize_control13.py' in args[1]]
        self.assertEqual(materialized,['headservice13a'])
        self.assertTrue(any(args[:2]==['docker','exec'] for args,_ in calls))
        self.assertTrue(any(args[:2]==['docker','stop'] and args[-1]=='own-head' for args,_ in calls))
        self.assertTrue(all(0<timeout<=180 for _,timeout in calls))

    def test_diagnostic_invalid_scope_or_observe_cannot_allocate(self):
        for kwargs in (dict(trace=False),dict(observe=True)):
            calls,error,receipt=self.diagnostic(**kwargs)
            self.assertIsInstance(error,AssertionError)
            self.assertEqual(calls,[])
            self.assertFalse(receipt)

    def test_pool_actual_operator_only_launches_head_and_service(self):
        calls,error,receipt=self.diagnostic(pool_mode=True)
        self.assertIsNone(error)
        self.assertFalse(receipt)  # Not a CPU-only phase receipt or G1 acceptance.
        names=[args[-1] for args,_ in calls if len(args)>1 and 'materialize_control13.py' in args[1]]
        self.assertEqual(names,['headservice13a','service13a'])
        self.assertFalse(any(args[:2]==['docker','wait'] for args,_ in calls))

    def test_pool_observe_cannot_allocate(self):
        calls,error,receipt=self.diagnostic(pool_mode=True,observe=True)
        self.assertIsInstance(error,AssertionError)
        self.assertEqual(calls,[])

    def test_post_stop_uses_the_exact_snapshot_validated_by_ready(self):
        good=dict(run_id='r',boot_id='b',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=1,sample_begin_ns=0,sample_end_ns=2,history_ok=True,first_monitor_error=None)
        bad=dict(good,history_ok=False,first_monitor_error=dict(reason='new violation'))
        reads=[]
        class Root:
            def __truediv__(self,name):
                def read():
                    if name=='run-manifest.json':return json.dumps(dict(runtime_identity=dict(boot_id='b')))
                    reads.append(name)
                    return json.dumps(good if len(reads)==1 else bad)
                return types.SimpleNamespace(read_text=read)
        tree=ast.parse(operator.REMOTE)
        ready=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='ready')
        loop=next(n for n in ast.walk(tree) if isinstance(n,ast.While) and any(isinstance(t,ast.Name) and t.id=='post_stop' for stmt in n.body if isinstance(stmt,ast.Assign) for t in stmt.targets))
        scope=dict(json=json,time=types.SimpleNamespace(monotonic_ns=lambda:2),out=Root(),RUN='r',stop_ns=0)
        exec(compile(ast.Module(body=[ready,loop],type_ignores=[]),'actual-ready-poststop-race','exec'),scope)
        self.assertEqual(len(reads),1)
        self.assertTrue(scope['post_stop']['history_ok'])

    def test_diagnostic_stale_pre_stop_sample_cannot_publish_completion(self):
        calls,error,receipt=self.diagnostic(post_sample=False)
        self.assertIsNotNone(error)
        self.assertFalse(receipt)

    def test_diagnostic_does_not_reset_budget_after_import(self):
        calls,error,receipt=self.diagnostic(expire=True)
        self.assertIsInstance(error,TimeoutError)
        self.assertFalse(receipt)
        self.assertFalse(any(args[:2]==['docker','stop'] for args,_ in calls))

    def test_real_isolated_stdin_imports_preflight_from_bound_source(self):
        with tempfile.TemporaryDirectory() as directory:
            node=pathlib.Path(directory);source=node/'code/experiment';source.mkdir(parents=True)
            (node/'validation/controls/test-run').mkdir(parents=True)
            (node/'validation/controls/test-run/run-manifest.json').write_text(json.dumps(dict(scope='G1 independent untimed controls only; zero formal IDs')))
            (source/'budget_control13.py').write_bytes((operator.REPO/'runtime/active/budget_control13.py').read_bytes())
            (source/'preflight_control13.py').write_text("raise RuntimeError('BOUND_SOURCE_PREFLIGHT_REACHED')\n")
            program=operator.REMOTE.replace("pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')",'pathlib.Path('+repr(str(node))+')')
            prefix=operator.bound_prefix('test-run','ray13a','a'*40,'b'*64,False,'import-test')
            result=subprocess.run([sys.executable,'-I','-'],input=prefix+program,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)  # Deliberate stop before allocation.
            self.assertIn('BOUND_SOURCE_PREFLIGHT_REACHED',result.stderr)
            self.assertNotIn('ModuleNotFoundError',result.stderr)

class SigintPreflight(unittest.TestCase):
    def probe(self):
        tree=ast.parse(operator.REMOTE)
        text=next(n.value.value for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='probe' for x in n.targets))
        compile(text,'actual-cpu-import-probe','exec')
        return text[text.index('import select,signal,subprocess,sys,time'):text.index("with (run_root()/'cpu-import-preflight.json')")]

    def test_actual_generated_probe_and_child_compile(self):
        probe=self.probe();tree=ast.parse(probe)
        child=next(n.value.value for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='child_code' for x in n.targets))
        compile(child,'actual-asyncio-child','exec')
        self.assertIn('communicate(timeout=5)',probe)

    @unittest.skipUnless(sys.platform=='linux','POSIX SIGINT delivery; mandatory actual Linux preallocation probe')
    def test_real_asyncio_sigint_cleans_up(self):
        scope={};exec(self.probe(),scope)
        self.assertEqual(scope['sigint_proof']['returncode'],0)
        self.assertEqual(scope['sigint_proof']['cleanup_marker'],'cleanup')

if __name__=='__main__':unittest.main()
