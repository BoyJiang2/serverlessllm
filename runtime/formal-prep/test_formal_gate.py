"""Gate checks only; all runtime checks are stubbed, never claimed as proof."""
import hashlib
import json
import os
import pathlib
import sys
import tempfile
import types
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
        self.code = 'a' * 40
        required = ('formal_trials.py', 'formal_gate.py', 'attempt_state.py',
                    'bootstrap_formal.py', 'cache_state_formal.py', 'run-manifest.json')
        rows = []
        for name in required:
            path = self.source / name
            path.write_bytes(name.encode())
            rows.append(dict(path=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        self.manifest_sha = save(self.source / 'formal-input-manifest.json', dict(files=rows))
        save(self.root / 'run-manifest.json', dict(run_id='own-formal-run',
             scope='formal continuation of original forty IDs', store_import_sha256={}, formal_required_env={}))
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
                         execution_run_id='own-formal-run', r2_approval_sha256=r2_sha, normal_flags_proofs=proofs)

    def check(self, extra_env=None):
        sha = save(self.root / 'formal-admission.json', self.gate)
        env = dict(SLLM_FORMAL_GATE_SHA256=sha, SLLM_FORMAL_CODE_COMMIT=self.code,
                   SLLM_FORMAL_SOURCE_MANIFEST_SHA256=self.manifest_sha)
        env.update(extra_env or {})
        workspace = types.SimpleNamespace(SOURCE=self.source, run_root=lambda: self.root)
        preflight = types.SimpleNamespace(check=lambda root: None)
        store = types.SimpleNamespace(verify_store_imports=lambda expected: None)
        real_read = formal_gate.checked_json
        def read(path, expected):
            if path.as_posix().startswith('/experiment/'):
                return {}  # Asset hashes are exercised by the separate real baseline tests.
            return real_read(path, expected)
        with patch.dict(os.environ, env, clear=True), patch.dict(sys.modules, dict(
                workspace=workspace, preflight_control13=preflight, run_store_server13=store)), \
                patch('formal_gate.checked_json', side_effect=read):
            return formal_gate.verify_gate()

    def test_complete_synthetic_gate_reaches_stubbed_preflight(self):
        self.assertEqual(self.check()['code_commit'], self.code)

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
