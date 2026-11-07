"""Real Linux page-cache observations on tiny temporary files, never model inputs."""
import gzip
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import v4_cache_state as cache
from v4_evidence import cache_scan_checks


class CacheStateContracts(unittest.TestCase):
    def fixture(self, directory):
        path = Path(directory) / 'tiny-input'
        with path.open('wb') as stream:
            stream.write(b'x' * 8193)
            stream.flush()
            os.fsync(stream.fileno())
        return path, {str(path): cache.source_stat(path)}

    def test_actual_hot_cold_and_check_vectors_pass_existing_validator(self):
        with tempfile.TemporaryDirectory() as directory:
            path, files = self.fixture(directory)
            for mode in ('hot', 'cold', 'check'):
                output = Path(directory) / mode
                result = cache.run_cache(files, 'safetensors_inputs', mode, output,
                                         time.monotonic_ns() + 30_000_000_000, 'cpu-cache')
                vectors = {row['vector']: gzip.decompress((output / row['vector']).read_bytes())
                           for row in result['files']}
                checked = cache_scan_checks(result, {str(path): 8193}, vectors)
                self.assertEqual(checked['pages'], 3)
                self.assertEqual(result['cache_condition_passed'], None if mode == 'check' else True)
                preparation = json.loads((output / 'preparation.json').read_bytes())
                self.assertEqual(preparation['files'][0]['bytes_read'], 8193 if mode == 'hot' else 0)

    def test_source_changes_fail_before_payload_read_and_retain_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path, files = self.fixture(directory)
            path.write_bytes(b'changed')
            output = Path(directory) / 'changed'
            with mock.patch.object(cache.os, 'read') as read:
                with self.assertRaisesRegex(ValueError, 'identity changed'):
                    cache.run_cache(files, 'safetensors_inputs', 'hot', output,
                                    time.monotonic_ns() + 30_000_000_000, 'cpu-cache')
                read.assert_not_called()
            self.assertTrue((output / 'failure.json').exists())

    def test_failed_declared_residency_keeps_original_vector_and_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            _, files = self.fixture(directory)
            output = Path(directory) / 'failed'
            with mock.patch.object(cache, 'mincore_file', return_value=(4096, b'\0' * 3)):
                with self.assertRaisesRegex(ValueError, 'declared cache condition failed'):
                    cache.run_cache(files, 'safetensors_inputs', 'hot', output,
                                    time.monotonic_ns() + 30_000_000_000, 'cpu-cache')
            self.assertFalse(json.loads((output / 'summary.json').read_bytes())['cache_condition_passed'])
            self.assertEqual(gzip.decompress((output / '00000.mincore.gz').read_bytes()), b'\0' * 3)
            self.assertTrue((output / 'failure.json').exists())

    def test_expired_deadline_does_not_warm_or_evict_and_output_cannot_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            _, files = self.fixture(directory)
            output = Path(directory) / 'expired'
            with mock.patch.object(cache.os, 'posix_fadvise') as advise:
                with self.assertRaises(TimeoutError):
                    cache.run_cache(files, 'safetensors_inputs', 'cold', output, 1, 'cpu-cache')
                advise.assert_not_called()
            original = (output / 'failure.json').read_bytes()
            with self.assertRaises(FileExistsError):
                cache.run_cache(files, 'safetensors_inputs', 'cold', output, 1, 'cpu-cache')
            self.assertEqual((output / 'failure.json').read_bytes(), original)

    def test_store_pool_hit_cannot_be_claimed_by_hot_page_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            _, files = self.fixture(directory)
            with self.assertRaisesRegex(ValueError, 'pool residency'):
                cache.run_cache(files, 'store_inputs', 'hot', Path(directory) / 'invalid',
                                time.monotonic_ns() + 30_000_000_000, 'cpu-cache')


if __name__ == '__main__':
    unittest.main()
