import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_kimi_weights as subject
import supervise_kimi_download as supervisor


class DownloadTests(unittest.TestCase):
    def transfer(self, directory, body, size, digest):
        response = io.BytesIO(body)
        response.status = 200
        response.headers = {'Content-Length': str(size)}
        with patch.object(subject.urllib.request, 'urlopen', return_value=response), \
             patch.object(subject.os, 'posix_fadvise', create=True), \
             patch.object(subject.os, 'POSIX_FADV_DONTNEED', 4, create=True), \
             patch.object(subject.time, 'sleep'):
            return subject.fetch_file('https://example.invalid/file', Path(directory) / 'file', size, digest)

    def test_verified_file_is_published(self):
        with tempfile.TemporaryDirectory() as directory:
            digest = hashlib.sha256(b'fixture').hexdigest()
            result = self.transfer(directory, b'fixture', 7, digest)
            self.assertEqual(result['sha256'], digest)
            self.assertEqual((Path(directory) / 'file').read_bytes(), b'fixture')
            self.assertFalse((Path(directory) / 'file.partial').exists())

    def test_digest_failure_preserves_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.transfer(directory, b'fixture', 7, '0' * 64)
            self.assertFalse((Path(directory) / 'file').exists())
            self.assertTrue((Path(directory) / 'file.partial').exists())

    def test_short_response_is_not_published(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                self.transfer(directory, b'x', 7, None)
            self.assertFalse((Path(directory) / 'file').exists())

    def test_existing_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'file'
            target.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                self.transfer(directory, b'fixture', 7, None)
            self.assertEqual(target.read_bytes(), b'preserve')

    def test_existing_partial_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'file.partial'
            target.write_bytes(b'preserve')
            with self.assertRaises(FileExistsError):
                self.transfer(directory, b'fixture', 7, None)
            self.assertEqual(target.read_bytes(), b'preserve')

    def test_mirror_manifest_matches_all_frozen_inputs(self):
        root = Path(__file__).resolve().parent.parent
        evidence = root / 'evidence' / 'followup-kimi-k26'
        if not evidence.exists():
            evidence = Path(__file__).resolve().parent
        manifest = json.loads((evidence / 'official-metadata.json').read_text())
        transport = json.loads((evidence / 'modelscope-transport-manifest.json').read_text())
        self.assertEqual(len(subject.mirror_entries(manifest, transport)), 96)
        # Corrupt both mirror declarations: the independent official weight digest must reject it.
        item = next(x for x in transport['files'] if x['path'].endswith('.safetensors'))
        item.update(sha256='0' * 64, modelscope_sha256='0' * 64, match=True)
        with self.assertRaises(ValueError):
            subject.mirror_entries(manifest, transport)

    def test_supervisor_records_deadline_and_timeout_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'download_kimi_weights.py').write_bytes(b'fixture')
            (run / 'launch.json').write_text(json.dumps({
                'source_sha256': hashlib.sha256(b'fixture').hexdigest()}))
            original_read = Path.read_text
            def read_identity(path):
                if path.name == 'boot_id':
                    return 'boot'
                if path.name == 'stat':
                    return '1 (test) ' + ' '.join(['0'] * 20)
                return original_read(path)
            with patch.object(supervisor, '__file__', str(run / 'supervise.py')), \
                 patch.object(supervisor.subprocess, 'Popen') as spawn, \
                 patch.object(supervisor.Path, 'read_text', autospec=True,
                              side_effect=read_identity):
                spawn.return_value.pid = os.getpid()
                spawn.return_value.wait.return_value = 124
                supervisor.main()
            record = json.loads((run / 'registration.json').read_text())
            self.assertEqual(record['deadline_epoch'] - record['start_epoch'], 21600)
            self.assertIn('--kill-after=30', record['command'])
            self.assertEqual(record['command'][4:10],
                             ['/usr/bin/nice', '-n', '19', '/usr/bin/ionice', '-c', '3'])
            self.assertEqual(json.loads((run / 'exit.json').read_text())['exit_code'], 124)

    def test_supervisor_rejects_changed_source_before_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'download_kimi_weights.py').write_bytes(b'changed')
            (run / 'launch.json').write_text(json.dumps({'source_sha256': '0' * 64}))
            with patch.object(supervisor, '__file__', str(run / 'supervise.py')), \
                 patch.object(supervisor.subprocess, 'Popen') as spawn:
                with self.assertRaises(AssertionError):
                    supervisor.main()
                spawn.assert_not_called()

    def test_existing_registration_prevents_spawn(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'download_kimi_weights.py').write_bytes(b'fixture')
            (run / 'launch.json').write_text(json.dumps({
                'source_sha256': hashlib.sha256(b'fixture').hexdigest()}))
            (run / 'registration.json').write_text('{}')
            with patch.object(supervisor, '__file__', str(run / 'supervise.py')), \
                 patch.object(supervisor.subprocess, 'Popen') as spawn:
                with self.assertRaises(FileExistsError):
                    supervisor.main()
                spawn.assert_not_called()

    def test_identity_failure_reaps_own_group(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run / 'download_kimi_weights.py').write_bytes(b'fixture')
            (run / 'launch.json').write_text(json.dumps({
                'source_sha256': hashlib.sha256(b'fixture').hexdigest()}))
            original_read = Path.read_text
            def fail_identity(path):
                if path.name == 'stat':
                    raise PermissionError('fixture')
                return original_read(path)
            with patch.object(supervisor, '__file__', str(run / 'supervise.py')), \
                 patch.object(supervisor.subprocess, 'Popen') as spawn, \
                 patch.object(supervisor.Path, 'read_text', autospec=True, side_effect=fail_identity), \
                 patch.object(supervisor.os, 'killpg', create=True) as kill:
                spawn.return_value.pid = 42
                spawn.return_value.poll.return_value = None
                with self.assertRaises(PermissionError):
                    supervisor.main()
                kill.assert_called_once_with(42, supervisor.signal.SIGTERM)
                spawn.return_value.wait.assert_called_once_with(timeout=5)
            self.assertEqual(json.loads((run / 'exit.json').read_text())['status'],
                             'identity publication failed')


if __name__ == '__main__':
    unittest.main()
