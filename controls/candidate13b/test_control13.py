"""CPU-only admission tests; mock inspection, no Docker dispatch or model reads."""
import hashlib,json,pathlib,subprocess,sys,tempfile,unittest
from unittest.mock import patch
import materialize_control13 as m

root=pathlib.Path(__file__).resolve().parent
run=json.loads((root/'run-manifest.json').read_text())['run_id']

class Admission(unittest.TestCase):
    def info(self,uid='2147',address='172.17.0.2'):
        return json.dumps([dict(State=dict(Running=True),Config=dict(Labels={'sllm.run_id':run,'sllm.uid':uid}),NetworkSettings=dict(Networks=dict(bridge=dict(IPAddress=address))))])
    def test_template_equal(self):
        with patch.object(m,'call',return_value=self.info()):
            spec=m.expected_spec(root,'service13a')
            m.validate_spec(root,spec)
            self.assertEqual(spec['env']['SLLM_PRIVATE_RAY_ADDRESS'],'172.17.0.2:26532')
    def test_unknown_name(self):
        with self.assertRaises(AssertionError): m.expected_spec(root,'otherjob')
    def test_uid(self):
        with patch.object(m,'call',return_value=self.info(uid='0')):
            with self.assertRaises(AssertionError): m.expected_spec(root,'ray13a')
    def test_nonprivate_ip(self):
        with patch.object(m,'call',return_value=self.info(address='8.8.8.8')):
            with self.assertRaises(AssertionError): m.expected_spec(root,'ray13a')
    def test_extra_env(self):
        with patch.object(m,'call',return_value=self.info()):
            spec=m.expected_spec(root,'ray13a')
            spec['env']['SLLM_FORMAL_GATE_FILE']='forbidden'
            with self.assertRaises(AssertionError): m.validate_spec(root,spec)
    def test_monitor_escape(self):
        with patch.object(m,'call',return_value='/other.slice'):
            with self.assertRaises(AssertionError): m.expected_spec(root,'monitor13')
    def test_head_exact(self):
        m.validate_spec(root,json.loads((root/'headray13a.json').read_text()))
    def test_optimized_rejected(self):
        result=subprocess.run([sys.executable,'-O',str(root/'materialize_control13.py'),'otherjob'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('optimized Python is forbidden',result.stderr)
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
