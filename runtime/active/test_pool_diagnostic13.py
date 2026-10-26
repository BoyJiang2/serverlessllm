"""Execute loaded-pool admission/prefix/exit on CPU fixtures; never allocate a pool."""
import ast,json,pathlib,tempfile,time,types,unittest
import urllib.request
from unittest.mock import patch
import materialize_control13 as materialize
from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_BUDGET,require_budget

SOURCE=pathlib.Path(__file__).resolve().parent

class PoolDiagnostic(unittest.TestCase):
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
                def wait(self,**kwargs):return self.returncode
                def kill(self):self.returncode=-9
            tree=ast.parse((SOURCE/'bootstrap_control13.py').read_text());body=next(n for n in tree.body if isinstance(n,ast.Try))
            index=next(i for i,n in enumerate(body.body) if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='pool_diagnostic')
            mini=ast.Try(body=body.body[index:index+2],handlers=[],orelse=[],finalbody=body.finalbody)
            scope=dict(pool_diagnostic=True,out=out,control=control,manifest=dict(run_id='test-run',runtime_identity=dict(boot_id='boot')),json=json,time=time,pool_bytes=768799145984,chunk_bytes=536870912,processes=[Process()],logs=[],env={},python='fixture',subprocess=types.SimpleNamespace(run=lambda *args,**kwargs:None),launch=lambda *args:(_ for _ in ()).throw(AssertionError('native SLLM service reached')))
            with self.assertRaises((RuntimeError,KeyError) if extra_gpu or missing_gpu else SystemExit) as error:exec(compile(ast.fix_missing_locations(ast.Module(body=[mini],type_ignores=[])),'actual-pool-ready-finally','exec'),scope)
            if not (extra_gpu or missing_gpu):self.assertEqual(error.exception.code,0)
            stages=json.loads((out/'pool-diagnostic-teardown.json').read_text());self.assertEqual(stages[-1]['stage'],'children_reaped')
            self.assertEqual((out/'pool-diagnostic-result.json').is_file(),not (extra_gpu or missing_gpu))

    def test_actual_ready_branch_exits_through_finally_before_sllm(self):self.ready_branch()

    def test_extra_own_gpu_pid_blocks_completion_and_still_cleans_up(self):self.ready_branch(extra_gpu=True)

    def test_missing_gpu_pid_list_blocks_completion_and_still_cleans_up(self):self.ready_branch(missing_gpu=True)

if __name__=='__main__':unittest.main()
