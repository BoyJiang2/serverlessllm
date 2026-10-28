import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import download_kimi_weights as subject


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


if __name__ == '__main__':
    unittest.main()
