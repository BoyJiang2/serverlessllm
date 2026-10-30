import hashlib
from contextlib import ExitStack
import json
import os
import pathlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import source_bundle as bundle
import test_source_plugin


class SourceBundleTests(unittest.TestCase):
    def git(self, root, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=' + str(root / 'no-hooks'),
            '-c', 'commit.gpgsign=false', '-c', 'user.name=CPU Fixture',
            '-c', 'user.email=cpu-fixture@example.invalid', *args], cwd=root, stderr=subprocess.DEVNULL)

    def fixture(self, temp):
        root = Path(temp)
        repo = root / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        source = repo / bundle.PREFIX
        source.mkdir(parents=True)
        # Tiny Git-object/inventory fixture, not the complete Kimi candidate.
        for name, data in {'source_plugin.py': b'# plugin\n', 'source_bundle.py': b'# verifier\n',
                'checkpoint-targets.json': b'{}\n', 'nested/helper.py': b'# helper\n'}.items():
            path = source / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        self.git(repo, 'add', '.')
        self.git(repo, 'commit', '-qm', 'CPU fixture')
        commit = self.git(repo, 'rev-parse', 'HEAD').decode().strip()
        target, manifest = root / 'tree', root / 'bundle.json'
        receipt = bundle.materialize_source_bundle(commit, target, manifest, repo=repo)
        return repo, source, target, manifest, commit, receipt

    def verify(self, target, manifest, commit, digest=None):
        if digest is None: digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        return bundle.verify_source_bundle(target, manifest, digest, expected_code_commit=commit)

    def test_exact_committed_bytes_ignore_dirty_and_untracked_worktree(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            (source / 'source_plugin.py').write_bytes(b'dirty')
            (source / 'untracked.py').write_bytes(b'not committed')
            files, raw = bundle.build_source_inventory(commit, repo)
            self.assertEqual(files['source_plugin.py'], b'# plugin\n')
            self.assertNotIn('untracked.py', files)
            self.assertEqual(raw, manifest.read_bytes())
            self.assertTrue(receipt['source_bytes_verified'])
            self.assertFalse(receipt['git_history_checked'])
            self.assertFalse(receipt['runtime_admitted'])

    def test_missing_extra_or_changed_source_refused(self):
        for kind in ('missing', 'extra', 'changed'):
            with tempfile.TemporaryDirectory() as temp:
                repo, source, target, manifest, commit, receipt = self.fixture(temp)
                if kind == 'missing': (target / 'nested/helper.py').unlink()
                elif kind == 'extra': (target / 'unexpected.pyc').write_bytes(b'extra')
                else: (target / 'source_plugin.py').write_bytes(b'changed and grown')
                with self.assertRaises(ValueError): self.verify(target, manifest, commit)

    def test_external_sha_and_expected_commit_are_both_required(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            with self.assertRaises(ValueError): self.verify(target, manifest, commit, '0' * 64)
            with self.assertRaisesRegex(ValueError, 'commit differs'):
                self.verify(target, manifest, 'f' * 40)
            manifest.write_bytes(manifest.read_bytes() + b' ')
            with self.assertRaises(ValueError): self.verify(target, manifest, commit, receipt['manifest_sha256'])

    def test_unsafe_duplicate_or_oversized_rows_refused_before_tree_scan(self):
        for kind in ('traversal', 'absolute', 'windows', 'duplicate', 'size', 'missing-required'):
            with tempfile.TemporaryDirectory() as temp:
                repo, source, target, manifest, commit, receipt = self.fixture(temp)
                document = json.loads(manifest.read_bytes())
                if kind == 'traversal': document['files'][0]['path'] = '../outside'
                elif kind == 'absolute': document['files'][0]['path'] = '/outside'
                elif kind == 'windows': document['files'][0]['path'] = 'C:outside'
                elif kind == 'duplicate': document['files'].append(document['files'][0])
                elif kind == 'size': document['files'][0]['bytes'] = bundle.MAX_FILE_BYTES + 1
                else: document['files'] = [r for r in document['files'] if r['path'] != 'source_plugin.py']
                manifest.write_text(json.dumps(document), encoding='utf-8')
                with patch.object(bundle.os, 'walk', side_effect=AssertionError('unexpected scan')):
                    with self.assertRaises(ValueError): self.verify(target, manifest, commit)

    def test_existing_tree_or_manifest_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            before = (target / 'source_plugin.py').read_bytes()
            with self.assertRaises(ValueError):
                bundle.materialize_source_bundle(commit, target, manifest, repo=repo)
            self.assertEqual((target / 'source_plugin.py').read_bytes(), before)

    def test_symlink_flag_refused_without_following_target(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            real = Path.is_symlink
            def marked(path):
                return path == target / 'source_plugin.py' or real(path)
            with patch.object(Path, 'is_symlink', marked):
                with self.assertRaisesRegex(ValueError, 'symlink'):
                    self.verify(target, manifest, commit)

    def test_manifest_stream_cannot_exceed_declared_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'large.json'
            path.write_bytes(b'x' * (bundle.MAX_MANIFEST_BYTES + 1))
            with self.assertRaisesRegex(ValueError, 'length'):
                bundle.verify_source_bundle(Path(temp) / 'not-scanned', path, '0' * 64,
                    expected_code_commit='a' * 40)

    def test_cli_verifies_tiny_tree_in_fresh_cpu_process(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            result = subprocess.run([sys.executable, '-B', str(Path(bundle.__file__)), 'verify',
                '--directory', str(target), '--manifest', str(manifest), '--commit', commit,
                '--sha256', receipt['manifest_sha256']], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['files_verified'], 4)

    def run_fixture(self, temp, repo, commit):
        spec, spec_path = test_source_plugin.SourcePluginTests().fixture(temp)
        spec['code_commit'] = commit
        raw = json.dumps(spec).encode('utf-8')
        spec_path.write_bytes(raw)
        target = Path(spec['run_root']) / 'code'
        manifest = Path(spec['run_root']) / 'source-code-manifest.json'
        receipt = bundle.materialize_source_bundle(commit, target, manifest, repo=repo)
        return spec, spec_path, hashlib.sha256(raw).hexdigest(), target, manifest, receipt

    def test_source_config_and_code_commit_bind_to_same_run_layout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, source, tree, unused_manifest, commit, initial = self.fixture(root)
            spec, spec_path, spec_sha, target, manifest, receipt = self.run_fixture(root, repo, commit)
            verified = bundle.verify_source_run(target, manifest, receipt['manifest_sha256'], spec_path, spec_sha)
            self.assertEqual(verified['holder'], spec['holder'])
            self.assertEqual(verified['code_commit'], commit)
            self.assertTrue(verified['identity_declared_only'])
            self.assertFalse(verified['runtime_admitted'])
            spec['code_commit'] = 'f' * 40
            raw = json.dumps(spec).encode('utf-8')
            spec_path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'commit differs'):
                bundle.verify_source_run(target, manifest, receipt['manifest_sha256'], spec_path,
                    hashlib.sha256(raw).hexdigest())

    def test_other_run_code_or_manifest_cannot_satisfy_this_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo, source, tree, unused_manifest, commit, initial = self.fixture(root)
            first = self.run_fixture(root, repo, commit)
            second_root = root / 'second'
            second_root.mkdir()
            second = self.run_fixture(second_root, repo, commit)
            spec, spec_path, spec_sha, target, manifest, receipt = first
            other_spec, other_path, other_sha, other_target, other_manifest, other_receipt = second
            self.assertEqual(receipt['manifest_sha256'], other_receipt['manifest_sha256'])
            with self.assertRaisesRegex(ValueError, 'another run'):
                bundle.verify_source_run(other_target, other_manifest, other_receipt['manifest_sha256'],
                    spec_path, spec_sha)
            with self.assertRaisesRegex(ValueError, 'another run'):
                bundle.verify_source_run(target, other_manifest, other_receipt['manifest_sha256'],
                    spec_path, spec_sha)

    def test_build_cli_reuses_bound_git_reader_without_pythonpath_setup(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            second_target = Path(temp) / 'cli-tree'
            second_manifest = Path(temp) / 'cli-manifest.json'
            result = subprocess.run([sys.executable, '-B', str(Path(bundle.__file__)), 'build',
                '--repo', str(repo), '--commit', commit, '--directory', str(second_target),
                '--manifest', str(second_manifest)], cwd=temp,
                capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['manifest_sha256'], receipt['manifest_sha256'])

    def test_git_size_preflight_rejects_large_blob_before_cat_file(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            (source / 'oversized.bin').write_bytes(b'x' * (bundle.MAX_FILE_BYTES + 1))
            self.git(repo, 'add', '.')
            self.git(repo, 'commit', '-qm', 'Oversize negative fixture')
            new_commit = self.git(repo, 'rev-parse', 'HEAD').decode().strip()
            original_run = subprocess.run
            def checked(command, *args, **kwargs):
                self.assertNotIn('cat-file', command)
                return original_run(command, *args, **kwargs)
            with patch.object(subprocess, 'run', side_effect=checked):
                with self.assertRaisesRegex(ValueError, 'before blob read'):
                    bundle.build_source_inventory(new_commit, repo)

    def test_unreadable_subdirectory_cannot_hide_extra_files(self):
        with tempfile.TemporaryDirectory() as temp:
            repo, source, target, manifest, commit, receipt = self.fixture(temp)
            hidden = target / 'unreadable'
            hidden.mkdir()
            (hidden / 'extra.py').write_bytes(b'not in approved inventory')
            original_scan = os.scandir
            def denied(path):
                if Path(path) == hidden:
                    raise PermissionError('injected source directory scan failure')
                return original_scan(path)
            with ExitStack() as stack:
                stack.enter_context(patch.object(os, 'scandir', side_effect=denied))
                # Python 3.13 pathlib caches the original scanner on its globber.
                globber = getattr(getattr(pathlib, '_local', None), '_StringGlobber', None)
                if globber is not None:
                    stack.enter_context(patch.object(globber, 'scandir', staticmethod(denied)))
                with self.assertRaises(PermissionError):
                    self.verify(target, manifest, commit)
