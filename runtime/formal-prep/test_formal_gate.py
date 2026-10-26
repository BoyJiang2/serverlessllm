"""Gate checks only; all runtime checks are stubbed, never claimed as proof."""
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import types
import threading
import time
import unittest
from unittest.mock import patch

import formal_gate


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FormalGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.assets=self.root/'assets'
        repo=pathlib.Path(__file__).resolve().parents[2]
        (self.assets/'protocol').mkdir(parents=True)
        (self.assets/'validation').mkdir()
        (self.assets/'protocol/measurement-preregistration.json').write_bytes((repo/'protocol/measurement-preregistration.json').read_bytes())
        (self.assets/'validation/native-gold.json').write_bytes((repo/'evidence/reference/native-gold.json').read_bytes())
        for original in (repo/'evidence/formal-history').glob('*--result.json'):
            target=self.assets/'results/formal-trials'/original.name.removesuffix('--result.json')
            target.mkdir(parents=True)
            (target/'result.json').write_bytes(original.read_bytes())
        self.code = 'a' * 40
        config = dict(run_id='own-formal-run',scope='formal continuation of original forty IDs',
                      formal_prior_execution_roots=[],store_import_sha256={},formal_required_env={},runtime_identity=dict(boot_id='test-boot'))
        required = ('formal_trials.py', 'formal_gate.py', 'attempt_state.py',
                    'bootstrap_control13.py', 'cache_state_formal.py', 'run-manifest.json', 'cleanup_guard13.sh', 'initial-ledger.json')
        rows = []
        for name in required:
            path = self.source / name
            if name=='run-manifest.json':
                save(path,config)
            elif name=='initial-ledger.json':
                path.write_bytes((repo/'runtime/active/initial-ledger.json').read_bytes())
            else:
                path.write_bytes(name.encode())
            rows.append(dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.manifest_sha = save(self.source / 'control-input-manifest.json', dict(files=rows))
        save(self.root / 'run-manifest.json', config)
        save(self.root / 'formal13-identity.json', dict(run_id='own-formal-run',code_commit=self.code,
             manifest_sha256=self.manifest_sha,spec=dict(name='formal13'),preflight=dict(run_id='own-formal-run',boot_id='test-boot',
             guard_script_sha256=hashlib.sha256((self.source/'cleanup_guard13.sh').read_bytes()).hexdigest())))
        r2_sha = save(self.root / 'r2-approval.json', dict(gate='R2', scope='formal-runtime',
                      verdict='approved', code_commit=self.code, manifest_sha256=self.manifest_sha))
        proofs = []
        for kind in ('serverless_llm', 'safetensors'):
            name = kind + '.json'
            sha = save(self.root / name, dict(status='normal-flags gold smoke lifecycle checks passed',
                       cycles=2, weight_audit_performed=False, weight_format=kind))
            proofs.append(dict(path=name, sha256=sha))
        self.gate = dict(decision='approved', scope='formal-runtime', code_commit=self.code,
                         source_manifest_sha256=self.manifest_sha,
                         preregistration_sha256=formal_gate.PREREG_SHA, gold_sha256=formal_gate.GOLD_SHA,
                         execution_run_id='own-formal-run', worker_label='formal13', prior_execution_roots=[],
                         r2_approval_sha256=r2_sha, normal_flags_proofs=proofs,jit_cache_proof_sha256='e'*64)
        save(self.root/'r2-approval.json',dict(gate='R2',scope='formal-runtime',verdict='approved',code_commit=self.code,manifest_sha256=self.manifest_sha,jit_cache_proof_sha256='e'*64,normal_flags_proofs=proofs))
        self.gate['r2_approval_sha256']=hashlib.sha256((self.root/'r2-approval.json').read_bytes()).hexdigest()
        patcher=patch.object(formal_gate,'verify_normal_cache',return_value=dict(normal_flags_proofs=proofs));patcher.start();self.addCleanup(patcher.stop)

    def check(self, extra_env=None, forbid_host_check=False, admission_only=False, reference_error=None):
        sha = save(self.root / 'formal-admission.json', self.gate)
        env = dict(SLLM_FORMAL_GATE_SHA256=sha, SLLM_FORMAL_CODE_COMMIT=self.code,
                   SLLM_FORMAL_SOURCE_MANIFEST_SHA256=self.manifest_sha)
        env.update(extra_env or {})
        workspace = types.SimpleNamespace(SOURCE=self.source, run_root=lambda: self.root,
                                          workspace=lambda: self.assets)
        def host_check(root):
            if forbid_host_check:
                raise RuntimeError('host-only preflight called inside container gate')
        preflight = types.SimpleNamespace(check=host_check)
        def container_import(expected):
            if admission_only:
                raise RuntimeError('host admission attempted container Store import')
        store = types.SimpleNamespace(verify_store_imports=container_import)
        real_read = formal_gate.checked_json
        def read(path, expected):
            if path.as_posix().startswith('/experiment/'):
                return {}  # Asset hashes are exercised by the separate real baseline tests.
            return real_read(path, expected)
        read_text = pathlib.Path.read_text
        def text(path, *args, **kwargs):
            if path.as_posix() == '/proc/sys/kernel/random/boot_id':
                return 'test-boot'
            return read_text(path, *args, **kwargs)
        def reference(config, assets):
            if reference_error:
                raise ValueError(reference_error)
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, dict(
                workspace=workspace, preflight_control13=preflight, run_store_server13=store)), \
                patch('formal_gate.checked_json', side_effect=read), \
                patch('formal_gate.verify_normal_reference', side_effect=reference), \
                patch.object(pathlib.Path,'read_text',autospec=True,side_effect=text):
            if admission_only:
                return formal_gate.verify_admission(self.root,self.source,self.assets,
                                                     self.code,self.manifest_sha,sha)
            return formal_gate.verify_gate()

    def test_changed_original_result_blocks_even_host_admission(self):
        path=self.assets/'results/formal-trials/store_pool_hit_01/result.json'
        path.write_bytes(path.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'changed frozen input'):self.check(admission_only=True)

    def test_incomplete_current_attempt_blocks_even_host_admission(self):
        path=self.root/'formal-trials/store_pool_hit_03';path.mkdir(parents=True)
        save(path/'origin.json',dict(attempt_id='store_pool_hit_03'))
        with self.assertRaisesRegex(ValueError,'consumed attempt'):self.check(admission_only=True)

    def test_missing_declared_prior_root_cannot_mean_no_attempts(self):
        self.gate['prior_execution_roots']=['validation/controls/sllm-glm51fp8-20261026T000000Z-12345678/formal-trials']
        with self.assertRaisesRegex(ValueError,'declared execution root'):self.check(admission_only=True)

    def test_absolute_or_escaped_prior_root_rejected(self):
        for value in ('/other/formal-trials','../other','results/formal-trials'):
            self.gate['prior_execution_roots']=[value]
            with self.subTest(path=value),self.assertRaises(ValueError):self.check(admission_only=True)

    def test_complete_synthetic_gate_reaches_stubbed_preflight(self):
        self.assertEqual(self.check()['code_commit'], self.code)

    def test_container_gate_does_not_call_host_only_preflight(self):
        self.assertEqual(self.check(forbid_host_check=True)['code_commit'], self.code)

    def test_shared_admission_requires_no_container_dispatch(self):
        (self.root/'formal13-identity.json').unlink()
        self.assertEqual(self.check(admission_only=True)['code_commit'],self.code)

    def test_formal_admission_rechecks_g1_reference(self):
        with self.assertRaisesRegex(ValueError,'missing actual G1 reference'):
            self.check(reference_error='missing actual G1 reference')

    def test_container_waits_for_own_dispatch_receipt(self):
        path=self.root/'formal13-identity.json';data=path.read_bytes();path.unlink()
        def publish():
            time.sleep(.2)
            path.write_bytes(data)
        thread=threading.Thread(target=publish);thread.start()
        try:
            self.assertEqual(self.check()['code_commit'],self.code)
        finally:
            thread.join()

    def test_missing_dispatch_receipt_has_bounded_failure(self):
        (self.root/'formal13-identity.json').unlink()
        with patch('formal_gate.time.monotonic',side_effect=[0,6]), \
                self.assertRaisesRegex(RuntimeError,'unavailable within5s'):
            self.check()

    def test_formal_worker_path_injection_rejected(self):
        self.gate['worker_label']='../other-run'
        with self.assertRaisesRegex(ValueError,'formal worker identity'):
            self.check()

    def test_stale_dispatch_boot_rejected(self):
        path=self.root/'formal13-identity.json'
        data=json.loads(path.read_bytes());data['preflight']['boot_id']='old-boot';save(path,data)
        with self.assertRaisesRegex(ValueError,'guarded host dispatch'):
            self.check()

    def test_wrong_actual_guard_rejected(self):
        path=self.root/'formal13-identity.json'
        data=json.loads(path.read_bytes());data['preflight']['guard_script_sha256']='bad';save(path,data)
        with self.assertRaisesRegex(ValueError,'guarded host dispatch'):
            self.check()

    def test_g1_cannot_authorize_formal(self):
        self.gate['scope'] = 'full-runtime'
        with self.assertRaisesRegex(ValueError, 'current formal admission'):
            self.check()

    def test_old_code_rejected(self):
        self.gate['code_commit'] = 'b' * 40
        with self.assertRaisesRegex(ValueError, 'current formal admission'):
            self.check()

    def test_wrong_r2_verdict_rejected(self):
        data = json.loads((self.root / 'r2-approval.json').read_text())
        data['gate'] = 'R1'
        self.gate['r2_approval_sha256'] = save(self.root / 'r2-approval.json', data)
        with self.assertRaisesRegex(ValueError, 'independent R2'):
            self.check()

    def test_changed_source_rejected(self):
        (self.source / 'formal_trials.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'formal source differs'):
            self.check()

    def test_changed_run_registration_rejected(self):
        path=self.root/'run-manifest.json'
        data=json.loads(path.read_bytes());data['formal_required_env']={'INJECTED':'value'};save(path,data)
        with self.assertRaisesRegex(ValueError,'run registration differs'):
            self.check(admission_only=True)

    def test_wrong_execution_identity_rejected(self):
        self.gate['execution_run_id'] = 'old-run'
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            self.check()

    def test_audit_flags_rejected(self):
        with self.assertRaisesRegex(ValueError, 'must be absent'):
            self.check(dict(SLLM_VERIFY_WEIGHTS='1'))

    def test_missing_normal_format_rejected(self):
        self.gate['normal_flags_proofs'].pop()
        with self.assertRaisesRegex(ValueError, 'both normal-flags'):
            self.check()

    def test_audited_proof_is_not_normal_flags(self):
        row = self.gate['normal_flags_proofs'][0]
        proof = json.loads((self.root / row['path']).read_text())
        proof['weight_audit_performed'] = True
        row['sha256'] = save(self.root / row['path'], proof)
        with self.assertRaisesRegex(ValueError, 'normal-flags lifecycle evidence'):
            self.check()


if __name__ == '__main__':
    unittest.main()
