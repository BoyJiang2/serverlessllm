import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deploy_tree as t

REPO = HERE.parent
C23 = REPO/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313/executed-source-archive.json'
C23_COMMIT = 'f176e05607d04999ab28e8ea8cd49b41270cb528'


def receipt():
    return json.loads(C23.read_text(encoding='utf-8'))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class History(unittest.TestCase):
    def test_c23_executed_source_rebuilds_byte_for_byte(self):
        result = t.verify_receipt(receipt())
        self.assertEqual((result['code_commit'], result['recorded_files'], result['mismatched']), (C23_COMMIT, 81, []))
        self.assertTrue(result['manifest_sha256_match'])
        self.assertTrue(result['recorded_rows_equal_manifest'])
        self.assertTrue(result['identical'])

    def test_one_changed_recorded_hash_is_detected(self):
        record = receipt()
        record['files_checked_before_and_after'][0]['sha256'] = '0'*64
        result = t.verify_receipt(record)
        self.assertEqual(result['mismatched'], [record['files_checked_before_and_after'][0]['path']])
        self.assertFalse(result['identical'])

    def test_formal_rule_equals_the_commits_own_reviewed_files(self):
        files = t.commit_files(C23_COMMIT)
        rows = json.loads(files[t.MANIFEST])['files']
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)/'tree'
            for path, data in files.items():
                (root/path).parent.mkdir(parents=True, exist_ok=True)
                (root/path).write_bytes(data)
            frozen = load('frozen_package_inventory13', root/'package_inventory13.py')
            self.assertEqual(frozen.reviewed_files(root, rows), set(t.formal_tree(files)))
            (root/'stray.py').write_bytes(b'x = 1\n')
            with self.assertRaises(RuntimeError):
                frozen.reviewed_files(root, rows)
        files['stray.py'] = b'x = 1\n'
        self.assertFalse(t.reviewed_inventory_exact(files))
        with self.assertRaisesRegex(ValueError, 'reviewed inventory'):
            t.formal_tree(files)

    def test_tampered_bytes_fail_the_formal_rule(self):
        files = t.commit_files(C23_COMMIT)
        files['workspace.py'] += b'\n'
        with self.assertRaises(ValueError):
            t.formal_tree(files)


class GitBytes(unittest.TestCase):
    def test_trees_are_raw_git_objects(self):
        files = t.commit_files(C23_COMMIT)
        for name in ('workspace.py', 'run-manifest.json', 'control-input-manifest.json'):
            raw = subprocess.check_output(['git', 'cat-file', 'blob', C23_COMMIT+':runtime/active/'+name], cwd=REPO)
            with self.subTest(name=name):
                self.assertEqual(files[name], raw)

    def test_builds_are_deterministic(self):
        self.assertEqual(t.build(C23_COMMIT, 'formal')[1]['tree_sha256'], t.build(C23_COMMIT, 'formal')[1]['tree_sha256'])

    def test_non_regular_entries_are_rejected(self):
        for mode, kind in (('120000', 'blob'), ('160000', 'commit')):
            listing = ('%s %s %s\truntime/active/entry\0' % (mode, kind, 'a'*40)).encode()
            with self.subTest(mode=mode), patch.object(t.subprocess, 'check_output', return_value=listing), \
                 self.assertRaisesRegex(ValueError, 'regular'):
                t.commit_files('HEAD')


class Kinds(unittest.TestCase):
    def test_debug_tree_drops_identity_and_adds_entry_from_same_commit(self):
        tree, record = t.build('HEAD', 'debug')
        formal, _ = t.build('HEAD', 'formal')
        self.assertNotIn(t.FORMAL_IDENTITY, tree)
        self.assertEqual(tree['debug_service.py'], t.commit_file('HEAD', t.DEBUG_ENTRY))
        self.assertEqual({p: d for p, d in tree.items() if p != 'debug_service.py'},
                         {p: d for p, d in formal.items() if p != t.FORMAL_IDENTITY})
        self.assertTrue(record['reviewed_inventory_exact'])
        self.assertTrue(t.destination_for(record).startswith(t.HOST+'/code/source-debug-'+record['code_commit'][:12]))

    def test_formal_trees_are_never_pushed(self):
        _, record = t.build(C23_COMMIT, 'formal')
        with self.assertRaisesRegex(ValueError, 'deploy_control13'):
            t.destination_for(record)

    def test_debug_entry_must_come_from_the_same_commit(self):
        with self.assertRaisesRegex(ValueError, 'nothing under'):
            t.build(C23_COMMIT, 'debug')  # f176e05 predates the debug entry


class Snapshots(unittest.TestCase):
    def test_compare_reports_missing_extra_changed_and_links(self):
        _, built = t.build(C23_COMMIT, 'formal')
        files = {r['path']: [r['bytes'], r['sha256']] for r in built['files']}
        deployment = dict(code_commit=C23_COMMIT, input_manifest_sha256=built['manifest_sha256'])
        bad = dict(files)
        bad.pop('workspace.py')
        bad['stray.log'] = [1, '0'*64]
        bad['accept_control13.py'] = [1, '1'*64]
        cached = dict(files, **{'__pycache__/workspace.cpython-310.pyc': [10, '2'*64]})
        report = t.compare_snapshots(dict(
            good=dict(deployment=deployment, snapshots={'executed-source-f176e05': dict(files=cached, symlinks=[])}),
            bad=dict(deployment=deployment, snapshots={'executed-source-f176e05': dict(files=bad, symlinks=['link'])}),
            wrong_manifest=dict(deployment=dict(deployment, input_manifest_sha256='0'*64),
                                snapshots={'executed-source-f176e05': dict(files=files, symlinks=[])})))
        good = report['good/executed-source-f176e05']
        self.assertTrue(good['identical'])
        self.assertEqual((good['bytecode_cache_files'], good['extra']), (1, []))
        row = report['bad/executed-source-f176e05']
        self.assertEqual((row['missing'], row['extra'], row['mismatched'], row['symlinks']),
                         (['workspace.py'], ['stray.log'], ['accept_control13.py'], ['link']))
        self.assertFalse(row['identical'])
        self.assertFalse(report['wrong_manifest/executed-source-f176e05']['identical'])


class CommandLine(unittest.TestCase):
    def test_entry_points_parse(self):
        for args in (['--help'], ['build', '--help'], ['push', '--help']):
            result = subprocess.run([sys.executable, str(HERE/'deploy_tree.py'), *args], capture_output=True, text=True, timeout=60)
            with self.subTest(args=args):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
