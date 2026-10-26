"""Prepared entry faults with synthetic clocks and identities; no runtime work."""
import ast,copy,importlib.util,json,pathlib,sys,time,types,unittest
from unittest.mock import patch
import tempfile,hashlib
from test_formal_budget import BatchDispatch
from test_watchdog13 import Child
from control_watchdog13 import supervise_child
from budget_control13 import head_stop_requested
from gpu_identity13 import registered_store_identity


class ControlledHead(unittest.TestCase):
    def exercise(self,request_at=2,exit_at=None,ignore_term=False,now=0):
        clock=[now];child=Child(clock,exit_at=exit_at,ignore_term=ignore_term)
        with patch.object(time,'monotonic_ns',side_effect=lambda:int(clock[0]*1e9)),patch.object(time,'sleep',side_effect=lambda n:clock.__setitem__(0,clock[0]+n)):
            result=supervise_child(child,0,100,lambda:None,absolute_end_ns=100*10**9,
                cleanup_seconds=10,stop_requested=lambda:clock[0]>=request_at)
        return result,child

    def test_request_forwards_term_and_reaps(self):
        result,child=self.exercise()
        self.assertEqual(result['status'],'passed including cleanup')
        self.assertTrue(result['controlled_stop']);self.assertTrue(child.terminated)
        self.assertFalse(result['forced_kill']);self.assertFalse(result['child_alive_at_end'])

    def test_early_zero_exit_is_not_normal_stop(self):
        result,_=self.exercise(exit_at=0)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['controlled_stop'])

    def test_deadline_wins_over_late_request(self):
        result,_=self.exercise(request_at=90,now=90)
        self.assertEqual(result['status'],'failed')
        self.assertFalse(result['controlled_stop'])

    def test_forced_kill_is_failed_even_with_request(self):
        result,_=self.exercise(ignore_term=True)
        self.assertEqual(result['status'],'failed');self.assertTrue(result['forced_kill'])

    def test_exit_between_request_and_signal_is_failed(self):
        child=types.SimpleNamespace(poll=unittest.mock.Mock(side_effect=[None,0,0,0]),terminate=unittest.mock.Mock())
        result=supervise_child(child,time.monotonic_ns(),100,lambda:None,cleanup_seconds=10,stop_requested=lambda:True)
        self.assertEqual(result['status'],'failed');child.terminate.assert_not_called()

    def test_unreviewed_raw_exit_after_term_cannot_pass(self):
        clock=[0];child=Child(clock)
        child.terminate=lambda:setattr(child,'code',42)
        with patch.object(time,'monotonic_ns',return_value=0):
            result=supervise_child(child,0,100,lambda:None,cleanup_seconds=10,stop_requested=lambda:True)
        self.assertEqual(result['status'],'failed');self.assertEqual(result['child_returncode'],42)

    def test_last_moment_expiry_refuses_new_child(self):
        from budget_control13 import require_batch_start
        with patch.object(time,'monotonic_ns',return_value=10),patch.object(time,'time_ns',return_value=100):
            require_batch_start(dict(initialization_end_ns=11,resource_deadline_ns=101))
            for row in (dict(initialization_end_ns=10,resource_deadline_ns=101),dict(initialization_end_ns=11,resource_deadline_ns=100)):
                with self.assertRaises(TimeoutError):require_batch_start(row)

    def test_actual_create_and_popen_boundaries_make_zero_calls_when_expired(self):
        from budget_control13 import require_batch_start
        for name,target in (('launch_control13.py','cid'),('supervise_control13.py','child')):
            tree=ast.parse(pathlib.Path(__file__).with_name(name).read_text())
            body=tree.body if target=='cid' else next(node.body for node in tree.body if isinstance(node,ast.Try))
            position=next(i for i,node in enumerate(body) if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==target for t in node.targets))
            snippet=ast.Module(body=body[position-1:position+1],type_ignores=[])
            calls=[]
            namespace=dict(batch=dict(initialization_end_ns=10,resource_deadline_ns=1000),formal_purpose=True,
                require_batch_start=require_batch_start,call=lambda *args:calls.append(args),args=[],argv=[],
                subprocess=types.SimpleNamespace(Popen=lambda argv:calls.append(argv)))
            with patch.object(time,'monotonic_ns',return_value=10),patch.object(time,'time_ns',return_value=100):
                with self.subTest(entry=name),self.assertRaises(TimeoutError):exec(compile(snippet,name,'exec'),namespace)
            self.assertEqual(calls,[])


class StopBinding(BatchDispatch):
    def request_fixture(self):
        root,dispatch=self.fixture()
        head=dict(run_id='r',code_commit='c',manifest_sha256='m',container_id='cid',pid=42)
        identity=dict(head,boot_id='b',head_dispatch_monotonic_ns=dispatch['dispatch_monotonic_ns'])
        request=dict(identity=identity,requested_ns=10*10**9)
        worker=dict(status='passed including cleanup',child_alive_at_end=False,
            absolute_end_ns=dispatch['batch_budget']['total_end_ns'],end_ns=9*10**9)
        for name,row in [('headformal13-identity.json',head),('headformal13-stop-request.json',request),('formal13-supervisor-result.json',worker)]:
            (root/name).write_text(json.dumps(row))
        return root,request,worker

    def test_valid_stop_and_missing_request(self):
        root,_,_=self.request_fixture();self.assertTrue(head_stop_requested(root,'formal13'))
        (root/'headformal13-stop-request.json').unlink()
        self.assertFalse(head_stop_requested(root,'formal13'))

    def test_wrong_pid_boot_and_failure_or_incomplete_worker_reject(self):
        for kind in ('pid','boot','deadline','status','live','late','worker-budget'):
            root,request,worker=self.request_fixture()
            if kind=='pid':request['identity']['pid']+=1
            elif kind=='boot':request['identity']['boot_id']='old'
            elif kind=='deadline':request['requested_ns']=10**20
            elif kind=='status':worker['status']='failed'
            elif kind=='live':worker['child_alive_at_end']=True
            elif kind=='late':worker['end_ns']=request['requested_ns']+1
            else:worker['absolute_end_ns']+=1
            (root/'headformal13-stop-request.json').write_text(json.dumps(request))
            (root/'formal13-supervisor-result.json').write_text(json.dumps(worker))
            with self.subTest(kind=kind),self.assertRaises(ValueError):head_stop_requested(root,'formal13')


class HelperRoles(unittest.TestCase):
    def exercise(self,live):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for label in ('service13a','formal13'):
                (root/('service-bootstrap-'+label)).mkdir()
                (root/('service-bootstrap-'+label)/'store-numa-launch.json').write_text(json.dumps(dict(role=label)))
                (root/(label+'-identity.json')).write_text('{}')
            with patch('gpu_identity13.store_identity',side_effect=lambda launch,*args:dict(role=launch['role']) if launch['role'] in live else None):
                return registered_store_identity(root,[],'b')

    def test_old_normal_record_does_not_mask_actual_formal_helper(self):
        self.assertEqual(self.exercise(['formal13']),dict(role='formal13'))

    def test_two_live_helpers_reject(self):
        with self.assertRaises(RuntimeError):self.exercise(['service13a','formal13'])

    def test_no_actual_helper_returns_none(self):self.assertIsNone(self.exercise([]))


class PreparedMaterializer(unittest.TestCase):
    def fixture(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=pathlib.Path(temporary.name)
        config=dict(run_id='r',scope='formal continuation of original forty IDs',execution_purpose='formal-continuation')
        (root/'run-manifest.json').write_text(json.dumps(config))
        (root/'deployment.json').write_text(json.dumps(dict(code_commit='c',input_manifest_sha256='m')))
        (root/'formal-admission.json').write_text('{}')
        active=pathlib.Path(__file__).parent.parent/'active'
        module_spec=importlib.util.spec_from_file_location('prepared_materialize',pathlib.Path(__file__).with_name('materialize_control13.py'))
        module=importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules,workspace=types.SimpleNamespace(SOURCE=active,run_root=lambda:root)):
            module_spec.loader.exec_module(module)
        info=dict(State=dict(Running=True),Config=dict(Labels={'sllm.run_id':'r','sllm.uid':'2147'}),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress='172.17.0.2'))))
        module.call=lambda *args:json.dumps([info])
        return root,module

    def test_only_finite_head_roles_with_supervisor_ttl(self):
        root,module=self.fixture()
        for name in ('headservice13a','headformal13'):
            spec=module.expected_spec(root,name)
            self.assertEqual(spec['argv'][-2:],['head',name.removeprefix('head')])
            self.assertEqual(spec['argv'][2],'/experiment/code/experiment/supervise_control13.py')
        with self.assertRaises(RuntimeError):module.expected_spec(root,'headnormal13')

    def test_both_workers_strip_audit_but_preserve_timing(self):
        root,module=self.fixture()
        for name in ('service13a','formal13'):
            spec=module.expected_spec(root,name)
            self.assertEqual(spec['argv'][-1],name);self.assertTrue(spec['phase_trace'])
            self.assertFalse(spec['raw_export_hook'])
            for key in ('SLLM_VERIFY_WEIGHTS','SLLM_EXPORT_RAW_STORE','VLLM_ALLOW_INSECURE_SERIALIZATION','SLLM_SERVICE_WEIGHT_AUDIT_DIR'):
                self.assertNotIn(key,spec['env'])
        formal=module.expected_spec(root,'formal13')
        self.assertEqual(formal['env']['SLLM_FORMAL_GATE_SHA256'],hashlib.sha256(b'{}').hexdigest())
        self.assertEqual(formal['env']['SLLM_PRIVATE_RAY_ADDRESS'],'172.17.0.2:26533')


class NormalQualification(unittest.TestCase):
    def fixture(self):
        import formal_gate
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        root=pathlib.Path(temporary.name)
        config=dict(run_id='r',runtime_identity=dict(boot_id='b'),image_id='i',g1_acceptance_sha256='g')
        (root/'run-manifest.json').write_text(json.dumps(config))
        (root/'deployment.json').write_text(json.dumps(dict(code_commit='c',input_manifest_sha256='m')))
        for kind in ('serverless_llm','safetensors'):
            target=root/'service-bootstrap-service13a'/kind;target.mkdir(parents=True)
            (target/'model-result.json').write_text(json.dumps(dict(status='normal-flags gold smoke lifecycle checks passed',cycles=2,weight_format=kind,weight_audit_performed=False)))
        self.artifacts={'kernel.so':{'sha256':'abc'}}
        batch=dict(begin_ns=1,work_end_ns=100,total_end_ns=200,head_end_ns=210)
        for role in ('service13a','headservice13a'):
            dispatch=dict(run_id='r',code_commit='c',manifest_sha256='m',boot_id='b',dispatch_monotonic_ns=1)
            (root/(role+'-dispatch.json')).write_text(json.dumps(dispatch))
            result=dict(dispatch,status='passed including cleanup',child_alive_at_end=False,
                absolute_end_ns=210 if role.startswith('head') else 200,end_ns=20,controlled_stop=True,forced_kill=False,terminate_sent_ns=15,child_returncode=1)
            (root/(role+'-supervisor-result.json')).write_text(json.dumps(result))
        patches=[patch.dict(sys.modules,compilation_guard_v6=types.SimpleNamespace(artifact_snapshot=lambda home:self.artifacts)),
                 patch('budget_control13.load_formal_batch',return_value=batch),
                 patch.object(time,'monotonic_ns',return_value=10)]
        for item in patches:item.start();self.addCleanup(item.stop)
        return root,formal_gate

    def test_seal_and_read_exact_normal_origin(self):
        root,module=self.fixture();module.seal_normal_cache(root,root,root)
        sha=hashlib.sha256((root/'normal-jit-qualification.json').read_bytes()).hexdigest()
        proof=module.verify_normal_cache(root,root,root,sha)
        self.assertEqual(len(proof['normal_flags_proofs']),2)

    def test_nonempty_but_changed_cache_cannot_pass(self):
        root,module=self.fixture();module.seal_normal_cache(root,root,root)
        sha=hashlib.sha256((root/'normal-jit-qualification.json').read_bytes()).hexdigest()
        self.artifacts={'different.so':{'sha256':'new'}}
        with self.assertRaisesRegex(ValueError,'cache changed'):module.verify_normal_cache(root,root,root,sha)

    def test_incomplete_normal_cycle_cannot_seal(self):
        root,module=self.fixture();(root/'service-bootstrap-service13a/safetensors/model-result.json').write_text('{}')
        with self.assertRaises(ValueError):module.seal_normal_cache(root,root,root)
        self.assertFalse((root/'normal-jit-qualification.json').exists())

    def test_seal_does_not_hide_later_cleanup_failure_or_missing_head_stop(self):
        for role in ('service13a','headservice13a'):
            root,module=self.fixture();module.seal_normal_cache(root,root,root)
            sha=hashlib.sha256((root/'normal-jit-qualification.json').read_bytes()).hexdigest()
            target=root/(role+'-supervisor-result.json');row=json.loads(target.read_text())
            if role.startswith('head'):row['terminate_sent_ns']=None
            else:row['status']='failed'
            target.write_text(json.dumps(row))
            with self.subTest(role=role),self.assertRaises(ValueError):module.verify_normal_cache(root,root,root,sha)


if __name__=='__main__':unittest.main()
