"""Exercise continuation with the nine actual preserved result files."""
import json
import pathlib
import tempfile
import unittest

from attempt_state import collect

REPO = pathlib.Path(__file__).resolve().parents[2]


class Continuity(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = pathlib.Path(self.temporary.name)
        self.prereg = self.root / 'prereg.json'
        self.prereg.write_bytes((REPO / 'protocol/measurement-preregistration.json').read_bytes())
        self.baseline = self.root / 'baseline.json'
        self.baseline.write_bytes((REPO / 'runtime/active/initial-ledger.json').read_bytes())
        self.history = self.root / 'history'
        self.history.mkdir()
        for path in (REPO / 'evidence/formal-history').glob('*--result.json'):
            (self.history / path.name).write_bytes(path.read_bytes())
        self.execution = self.root / 'formal-trials'
        self.execution.mkdir()

    def read(self, roots=None):
        return collect(self.prereg, self.baseline, self.history,
                       [self.execution] if roots is None else roots)

    def started(self, name='store_pool_hit_03', terminal=True):
        path = self.execution / name
        path.mkdir()
        (path / 'origin.json').write_text(json.dumps(dict(attempt_id=name)))
        if terminal:
            (path / 'result.json').write_text(json.dumps(dict(attempt_id=name, success=False)))
        return path

    def test_empty_execution_skips_all_nine_original_terminals(self):
        rows = self.read()
        self.assertEqual(len(rows), 40)
        self.assertEqual(sum(row['status'] == 'success' for row in rows), 5)
        self.assertEqual(sum(row['status'] == 'failed' for row in rows), 4)
        pending = [row['attempt_id'] for row in rows if row['status'] == 'unstarted']
        self.assertEqual(len(pending), 31)
        self.assertEqual(pending[0], 'store_pool_hit_03')

    def test_new_failure_consumes_id(self):
        self.started()
        rows = self.read()
        self.assertEqual(sum(row['status'] == 'failed' for row in rows), 5)
        self.assertEqual(next(row['attempt_id'] for row in rows if row['status'] == 'unstarted'),
                         'safetensors_disk_cold_03')

    def test_interrupted_attempt_cannot_be_reused(self):
        self.started(terminal=False)
        with self.assertRaisesRegex(ValueError, 'consumed attempt'):
            self.read()

    def test_original_terminal_cannot_be_rerun(self):
        self.started('store_pool_hit_01')
        with self.assertRaisesRegex(ValueError, 'terminal ID was rerun'):
            self.read()

    def test_changed_original_failure_blocks(self):
        path = self.history / 'store_pool_hit_01--result.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'changed frozen input'):
            self.read()

    def test_duplicate_execution_roots_block(self):
        with self.assertRaisesRegex(ValueError, 'duplicate execution root'):
            self.read([self.execution, self.execution])

    def test_duplicate_attempt_across_runs_blocks(self):
        self.started()
        other = self.root / 'other-formal-trials'
        other.mkdir()
        (other / 'store_pool_hit_03').mkdir()
        with self.assertRaisesRegex(ValueError, 'multiple executions'):
            self.read([self.execution, other])

    def test_origin_identity_mismatch_blocks(self):
        path = self.started()
        (path / 'origin.json').write_text(json.dumps(dict(attempt_id='wrong')))
        with self.assertRaisesRegex(ValueError, 'origin ID differs'):
            self.read()

    def test_changed_prereg_blocks(self):
        self.prereg.write_bytes(self.prereg.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'changed frozen input'):
            self.read()


if __name__ == '__main__':
    unittest.main()
