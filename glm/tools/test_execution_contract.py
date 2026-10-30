import hashlib
import json
import pathlib
import shutil
import tempfile
import unittest

from check_execution_contract import audit


class ExecutionContract(unittest.TestCase):
    def setUp(self):
        repo = pathlib.Path(__file__).resolve().parents[1]
        temporary = repo.parent / 'work/cpu-test-tmp'
        temporary.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='execution-contract-', dir=temporary)
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        contract = json.loads((repo / 'protocol/execution-contract.json').read_text())
        paths = ['protocol/execution-contract.json', 'runtime/active/control-input-manifest.json']
        paths += [x['path'] for x in contract['pinned_files']]
        paths += ['runtime/active/' + n for n in contract['control_files']]
        paths += [p.relative_to(repo).as_posix() for p in
                  (repo / 'evidence/formal-history').glob('*--result.json')]
        for name in set(paths):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(repo / name, target)

    def rewrite(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def test_valid_control_does_not_authorize_formal(self):
        result = audit(self.root)
        self.assertEqual(result['baseline_counts'], {'success': 5, 'failed': 4, 'unstarted': 31})
        self.assertEqual(result['next_original_id'], 'store_pool_hit_03')
        self.assertFalse(result['formal_ready'])

    def test_changed_preregistration_rejected(self):
        (self.root / 'protocol/measurement-preregistration.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed contract input'):
            audit(self.root)

    def test_terminal_result_replacement_rejected(self):
        path = self.root / 'evidence/formal-history/store_pool_hit_01--result.json'
        data = json.loads(path.read_text())
        data['success'] = True
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'historical result mismatch'):
            audit(self.root)

    def test_missing_historical_driver_rejected(self):
        (self.root / 'runtime/historical-v11/formal_trials_v11.py').unlink()
        with self.assertRaises(FileNotFoundError):
            audit(self.root)

    def test_missing_guard_manifest_entry_rejected(self):
        name = 'runtime/active/control-input-manifest.json'
        data = json.loads((self.root / name).read_text())
        data['files'] = [x for x in data['files'] if x['path'] != 'cleanup_guard13.sh']
        self.rewrite(name, data)
        with self.assertRaisesRegex(ValueError, 'manifest entry'):
            audit(self.root)

    def test_changed_control_source_rejected(self):
        (self.root / 'runtime/active/validation_control13.py').write_text('# changed')
        with self.assertRaisesRegex(ValueError, 'manifest entry'):
            audit(self.root)

    def test_resealed_wrong_guard_identity_rejected(self):
        name = 'runtime/active/cleanup_guard13.sh'
        self.rewrite(name, {})
        manifest_name = 'runtime/active/control-input-manifest.json'
        data = json.loads((self.root / manifest_name).read_text())
        for row in data['files']:
            if row['path'] == 'cleanup_guard13.sh':
                row['sha256'] = hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        self.rewrite(manifest_name, data)
        with self.assertRaisesRegex(ValueError, 'guard/control identity mismatch'):
            audit(self.root)

    def test_control_cannot_claim_formal_scope(self):
        name = 'runtime/active/run-manifest.json'
        data = json.loads((self.root / name).read_text())
        data['scope'] = 'formal'
        self.rewrite(name, data)
        with self.assertRaisesRegex(ValueError, 'cannot authorize formal IDs'):
            audit(self.root)


if __name__ == '__main__':
    unittest.main()
