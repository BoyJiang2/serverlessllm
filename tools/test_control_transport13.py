"""SSH failures must not restart or kill a verified healthy execution."""
import contextlib,io,json,pathlib,subprocess,sys,tempfile,time,types,unittest
from unittest.mock import patch
import run_control13 as operator

class Transport(unittest.TestCase):
    def exercise(self,status='running_verified',identity_ok=True,initial_rc=255,query_timeout=False,marker=False):
        with tempfile.TemporaryDirectory() as directory:
            repo=pathlib.Path(directory)/'repo';repo.mkdir()
            run='sllm-glm51fp8-20261026T000000Z-12345678';code='a'*40;sha='b'*64
            folder=repo.parent/'outputs'/('g1-'+run);folder.mkdir(parents=True)
            if marker:(folder/'cleanup-requested').write_text('request only')
            calls=[]
            def execute(args,**kwargs):
                calls.append(args)
                if len(calls)==1:return types.SimpleNamespace(returncode=initial_rc)
                if args[0]=='ssh':
                    if query_timeout:raise subprocess.TimeoutExpired(args,25)
                    row=dict(kind='control_reconciliation',run_id=run if identity_ok else 'other-run',phase='service13a',
                             code_commit=code,manifest_sha256=sha,status=status)
                    return types.SimpleNamespace(returncode=0,stdout=json.dumps(row),stderr='')
                return types.SimpleNamespace(returncode=0)
            with patch.object(operator,'REPO',repo),patch.object(operator,'load_context',return_value=run), \
                 patch.object(operator.subprocess,'run',side_effect=execute),contextlib.redirect_stdout(io.StringIO()):
                rc=operator.main(['service13a',code,sha,'reviews/verdict.json'])
            return rc,calls,[p.name for p in folder.iterdir()]

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
            save(out/'monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=time.monotonic_ns()))
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

    def diagnostic(self,trace=True,observe=False,expire=False):
        with tempfile.TemporaryDirectory() as directory:
            node=pathlib.Path(directory);run='test-run';code='a'*40;sha='b'*64
            out=node/'validation/controls'/run;out.mkdir(parents=True)
            def save(name,value):(out/name).write_text(json.dumps(value))
            save('run-manifest.json',dict(run_id=run,image_id='frozen-image',scope='monitor diagnostics only; no model or pool',monitor_diagnostic_trace=trace,cleanup_deadline='2099-01-01T00:00:00+00:00',runtime_identity=dict(boot_id='boot')))
            save('monitor-v3-ready.json',dict(run_id=run,boot_id='boot',observers_ok=True,foreign_gpu_pids=[],last_sample_ns=time.monotonic_ns()))
            save('headservice13a-identity.json',dict(run_id=run,code_commit=code,manifest_sha256=sha,container_id='own-head',pid=1,spec=dict(argv=['--port=26532'])))
            calls=[];clock=[100.0];running=[True]
            def command(args,**kwargs):
                calls.append((args,kwargs.get('timeout')))
                if args[:2]==['docker','inspect']:
                    info=dict(Image='frozen-image',Config=dict(Labels={'sllm.run_id':run,'sllm.uid':'2147'}),State=dict(Running=running[0],Pid=1),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress='172.17.0.2'))))
                    stdout=json.dumps([info])
                else:stdout=''
                if args[:2]==['docker','stop']:running[0]=False
                if args[:2]==['docker','exec'] and expire:clock[0]=281.0
                return types.SimpleNamespace(returncode=0,stdout=stdout,stderr='')
            actual_path=pathlib.Path
            def redirect(value):return node if str(value)=='/data/brick1/boyjiang/glm5.1-fp8' else actual_path(value)
            modules=dict(pathlib=types.SimpleNamespace(Path=redirect),subprocess=types.SimpleNamespace(run=command),
                         preflight_control13=types.SimpleNamespace(check=lambda root,**kwargs:None),
                         socket=types.SimpleNamespace(create_connection=lambda *args,**kwargs:contextlib.nullcontext()),
                         time=types.SimpleNamespace(monotonic=lambda:clock[0],monotonic_ns=time.monotonic_ns,time=time.time))
            scope=dict(RUN=run,PHASE='monitor-diagnostic',CODE=code,SHA=sha,OBSERVE=observe,SEGMENT='test')
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
            (source/'preflight_control13.py').write_text("raise RuntimeError('BOUND_SOURCE_PREFLIGHT_REACHED')\n")
            program=operator.REMOTE.replace("pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')",'pathlib.Path('+repr(str(node))+')')
            prefix=operator.bound_prefix('test-run','ray13a','a'*40,'b'*64,False,'import-test')
            result=subprocess.run([sys.executable,'-I','-'],input=prefix+program,text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)  # Deliberate stop before allocation.
            self.assertIn('BOUND_SOURCE_PREFLIGHT_REACHED',result.stderr)
            self.assertNotIn('ModuleNotFoundError',result.stderr)

if __name__=='__main__':unittest.main()
