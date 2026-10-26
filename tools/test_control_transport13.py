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

if __name__=='__main__':unittest.main()
