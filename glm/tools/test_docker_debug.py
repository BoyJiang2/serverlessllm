import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from docker_debug import IMAGE, bounded_spec, down, inspect_owned, up


class DebugTests(unittest.TestCase):
    def setUp(self):
        self.spec=dict(run_id='sllm-debug-20261028T110000Z-12345678',role='cpu',image_id=IMAGE,
                       memory='2g',cpus=2,env={},mounts=[],argv=['python3','--version'])
        self.identity=dict(container_id='a'*64,run_id=self.spec['run_id'],role='cpu')

    def test_lifetime_wrap_preserves_input(self):
        bounded=bounded_spec(self.spec,90)
        self.assertEqual(bounded['argv'][:4],['/usr/bin/timeout','--signal=TERM','--kill-after=10','90'])
        self.assertEqual(self.spec['argv'],['python3','--version'])
        for seconds in (0,301,True):
            with self.assertRaises(ValueError):bounded_spec(self.spec,seconds)

    def test_model_mount_or_large_resources_rejected(self):
        self.spec['memory']='1700g'
        with self.assertRaises(ValueError):bounded_spec(self.spec,90)
        self.spec['memory']='2g'
        self.spec['mounts']=[dict(source='/data/brick1/boyjiang/glm5.1-fp8/checkpoint',destination='/m',readonly=True)]
        with self.assertRaises(ValueError):bounded_spec(self.spec,90)

    def test_mount_cannot_replace_timeout(self):
        self.spec['mounts']=[dict(source='/data/brick1/boyjiang/glm5.1-fp8/code/timeout',destination='/usr/bin/timeout',readonly=True)]
        with self.assertRaises(ValueError):bounded_spec(self.spec,90)

    def test_loader_environment_cannot_modify_timeout(self):
        for key in ('LD_PRELOAD','LD_LIBRARY_PATH','PATH','GCONV_PATH'):
            self.spec['env']={key:'/debug/code'}
            with self.assertRaises(ValueError):bounded_spec(self.spec,90)

    def test_wrong_owner_never_removed(self):
        data=dict(Id='a'*64,Image=IMAGE,Name='/'+self.spec['run_id']+'-cpu',
                  Config=dict(Labels={'sllm.run_id':'someone-else','sllm.purpose':'DEBUG-UNREVIEWED'},User='2147:2147'))
        with patch('docker_debug.subprocess.run') as run:
            run.return_value.returncode=0;run.return_value.stdout=json.dumps([data])
            with self.assertRaises(ValueError):down(self.identity,Path('unused'))
            self.assertEqual(run.call_count,1)

    def test_inspect_failure_does_not_mean_release(self):
        with patch('docker_debug.subprocess.run') as run:
            run.return_value.returncode=1
            with self.assertRaises(RuntimeError):inspect_owned(self.identity)

    def test_existing_receipt_prevents_create(self):
        output=Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            receipt=Path(temporary)/'identity.json';receipt.write_text('{}')
            with patch('docker_debug.preflight'),patch('docker_debug.subprocess.check_output') as create:
                with self.assertRaises(FileExistsError):up(self.spec,receipt,90)
                create.assert_not_called()

    def test_create_timeout_cleans_only_recovered_holder(self):
        output=Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            receipt=Path(temporary)/'identity.json'
            def recover(identity):return dict(identity,container_id='b'*64)
            with patch('docker_debug.preflight'), \
                 patch('docker_debug.subprocess.check_output',side_effect=subprocess.TimeoutExpired('create',30)), \
                 patch('docker_debug.recover_identity',side_effect=recover), \
                 patch('docker_debug.inspect_owned'), patch('docker_debug.subprocess.run') as remove:
                with self.assertRaises(subprocess.TimeoutExpired):up(self.spec,receipt,90)
                self.assertEqual(remove.call_args.args[0],['docker','rm','-f','b'*64])
            self.assertIn('removed',json.loads(receipt.read_text())['status'])

    def test_unknown_create_outcome_does_not_remove(self):
        output=Path(__file__).resolve().parents[2]/'outputs'
        with tempfile.TemporaryDirectory(dir=output) as temporary:
            receipt=Path(temporary)/'identity.json'
            with patch('docker_debug.preflight'), \
                 patch('docker_debug.subprocess.check_output',side_effect=subprocess.TimeoutExpired('create',30)), \
                 patch('docker_debug.recover_identity',side_effect=ValueError('wrong holder')), \
                 patch('docker_debug.subprocess.run') as remove:
                with self.assertRaises(subprocess.TimeoutExpired):up(self.spec,receipt,90)
                remove.assert_not_called()
            self.assertIn('unverified',json.loads(receipt.read_text())['status'])


if __name__=='__main__':unittest.main()
