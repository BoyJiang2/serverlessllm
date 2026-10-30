"""CPU-only admission tests; mock inspection, no Docker dispatch or model reads."""
import hashlib,json,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
import materialize_control13 as m

root=pathlib.Path(__file__).resolve().parent
run=json.loads((root/'run-manifest.json').read_text())['run_id']

class Admission(unittest.TestCase):
    def setUp(self):
        directory=tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.control_root=pathlib.Path(directory.name)
        manifest=json.loads((root/'run-manifest.json').read_text())
        manifest.update(scope='G1 independent untimed controls only; zero formal IDs',execution_purpose='G1',monitor_diagnostic_trace=False,grpc_shutdown_probe=False)
        (self.control_root/'run-manifest.json').write_text(json.dumps(manifest))

    def info(self,uid='2147',address='172.17.0.2'):
        return json.dumps([dict(State=dict(Running=True),Config=dict(Labels={'sllm.run_id':run,'sllm.uid':uid}),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress=address))))])
    def test_template_equal(self):
        with patch.object(m,'call',return_value=self.info()):
            spec=m.expected_spec(self.control_root,'service13a')
            m.validate_spec(self.control_root,spec)
            self.assertEqual(spec['env']['SLLM_PRIVATE_RAY_ADDRESS'],'172.17.0.2:26532')
    def test_unknown_name(self):
        with self.assertRaises(AssertionError): m.expected_spec(self.control_root,'otherjob')
    def test_uid(self):
        with patch.object(m,'call',return_value=self.info(uid='0')):
            with self.assertRaises(AssertionError): m.expected_spec(self.control_root,'ray13a')
    def test_nonprivate_ip(self):
        with patch.object(m,'call',return_value=self.info(address='8.8.8.8')):
            with self.assertRaises(AssertionError): m.expected_spec(self.control_root,'ray13a')
    def test_extra_env(self):
        with patch.object(m,'call',return_value=self.info()):
            spec=m.expected_spec(self.control_root,'ray13a')
            spec['env']['SLLM_FORMAL_GATE_FILE']='forbidden'
            with self.assertRaises(AssertionError): m.validate_spec(self.control_root,spec)
    def test_monitor_escape(self):
        with patch.object(m,'call',return_value='/other.slice'):
            with self.assertRaises(AssertionError): m.expected_spec(self.control_root,'monitor13')
    def test_mutated_head_rejected(self):
        spec=json.loads((root/'headray13a.json').read_text())
        spec['argv'].append('--num-gpus=8')
        with self.assertRaises(AssertionError): m.validate_spec(self.control_root,spec)
    def test_diagnostic_allows_only_monitor_and_bounded_cpu_head(self):
        from budget_control13 import require_budget
        with tempfile.TemporaryDirectory() as directory:
            target=pathlib.Path(directory)
            (target/'run-manifest.json').write_text(json.dumps(dict(run_id=run,scope='monitor diagnostics only; no model or pool',monitor_diagnostic_trace=True)))
            self.assertTrue(m.validate_scope(target,'monitor13'))
            with patch.object(m,'SOURCE',root):
                head=m.expected_spec(target,'headservice13a')
            self.assertEqual(head['argv'][:4],['/usr/bin/timeout','--signal=TERM','--kill-after=5s','175s'])
            self.assertFalse(head['gpus'])
            self.assertEqual((head['cpus'],head['memory']),(4,'8g'))
            self.assertEqual(require_budget('headservice13a',900,diagnostic=True),180)
            with self.assertRaises(RuntimeError):require_budget('headservice13a',899.999,diagnostic=True)
            for name in ('ray13a','ray13b','headray13a','headray13b','service13a','otherjob'):
                with self.subTest(name=name):
                    with self.assertRaisesRegex(RuntimeError,'diagnostic scope forbids'):m.expected_spec(target,name)
                    with self.assertRaises(RuntimeError):require_budget(name,10000,diagnostic=True)
            config=json.loads((target/'run-manifest.json').read_text());config['monitor_diagnostic_trace']=False
            (target/'run-manifest.json').write_text(json.dumps(config))
            with self.assertRaises(RuntimeError):m.expected_spec(target,'headservice13a')
            config.update(scope='G1 independent untimed controls only; zero formal IDs',monitor_diagnostic_trace=True)
            (target/'run-manifest.json').write_text(json.dumps(config))
            with self.assertRaises(RuntimeError):m.expected_spec(target,'headservice13a')

    def test_timeout_receipt_requires_exact_live_own_monitor(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            target=pathlib.Path(directory)
            manifest=dict(run_id=run,image_id='image',runtime_identity=dict(boot_id='boot'))
            deployment=dict(code_commit='a'*40,input_manifest_sha256='b'*64)
            identity=dict(container_id='c'*64,pid=123)
            output='timeout (GNU coreutils) 9.1'
            proof=dict(run_id=run,code_commit='a'*40,manifest_sha256='b'*64,boot_id='boot',container_id='c'*64,image_id='image',argv=['/usr/bin/timeout','--version'],returncode=0,stdout=output,stdout_sha256=hashlib.sha256(output.encode()).hexdigest())
            info=dict(Id='c'*64,Image='image',Config=dict(Labels={'sllm.run_id':run,'sllm.uid':'2147'}),State=dict(Running=True,Pid=123))
            for name,data in [('run-manifest.json',manifest),('deployment.json',deployment),('monitor13-identity.json',identity),('diagnostic-timeout-probe.json',proof)]:
                (target/name).write_text(json.dumps(data))
            m.verify_timeout_probe(target,info)
            for key,value in [('run_id','wrong'),('boot_id','old'),('returncode',1),('stdout_sha256','0'*64),('image_id','other')]:
                bad=dict(proof);bad[key]=value;(target/'diagnostic-timeout-probe.json').write_text(json.dumps(bad))
                with self.subTest(key=key),self.assertRaises(RuntimeError):m.verify_timeout_probe(target,info)
            (target/'diagnostic-timeout-probe.json').write_text(json.dumps(proof))
            for section,key,value in [('State','Running',False),('State','Pid',124),('Config','Labels',{'sllm.run_id':'other','sllm.uid':'2147'})]:
                bad=copy.deepcopy(info);bad[section][key]=value
                with self.subTest(key=key),self.assertRaises(RuntimeError):m.verify_timeout_probe(target,bad)

    def test_optimized_rejected(self):
        for name in ['materialize_control13.py','bootstrap_control13.py','monitor_control13.py','supervise_control13.py','launch_control13.py','ray_control13.py','validation_control13.py','release_control13.py','accept_control13.py','prepare_host13.py','deploy_control13.py','snapshot_root13.py','store_numa_exec_v13.py','run_store_server13.py','collect_pss13.py','cache_state_control13.py']:
            with self.subTest(entry=name):
                result=subprocess.run([sys.executable,'-O',str(root/name),'otherjob'],capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('optimized Python',result.stderr)
    def test_window_before_start_rejected(self):
        from resource_window_control13 import load_window
        with tempfile.TemporaryDirectory() as directory:
            temp=pathlib.Path(directory)
            data=(root/'run-manifest.json').read_bytes()
            (temp/'run-manifest.json').write_bytes(data)
            window=json.loads((root/'resource-window-extension-v3.json').read_text())
            for row in window['bookings']:
                row['start_ms']+=7*86400000
                row['end_ms']+=7*86400000
            window['original_manifest_sha256']=hashlib.sha256(data).hexdigest()
            (temp/'resource-window-extension-v3.json').write_text(json.dumps(window))
            with self.assertRaisesRegex(AssertionError,'outside authorized booking window'): load_window(temp)

if __name__=='__main__': unittest.main()
