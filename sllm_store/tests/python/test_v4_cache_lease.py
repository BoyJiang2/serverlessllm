"""Tiny real Linux mlock and owned-helper pipe contracts; no model or GPU."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import v4_cache_lease as lease
from v4_cache_state import source_stat, mincore_file, prepare_file, run_cache
import v4_trial as trial


class CacheLeaseContracts(unittest.TestCase):
    def test_shared_metadata_cannot_be_cold_after_sf_lock_and_counterpart_is_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); shared = root / 'shared-config'; shared.write_bytes(b'x' * 4096)
            payload = root / 'store-payload'; payload.write_bytes(b'y' * 4096)
            for path in (shared, payload):
                with path.open('rb') as stream: os.fsync(stream.fileno())
            sf = {str(shared): source_stat(shared)}
            store = dict(sf, **{str(payload): source_stat(payload)})
            inventory = dict(safetensors_inputs=sf, store_inputs=store)
            deadline = time.monotonic_ns() + 10_000_000_000
            for name, expected in store.items(): prepare_file(Path(name), expected, 'cold', deadline)
            with lease.locked_files(sf, deadline):
                with self.assertRaisesRegex(ValueError, 'declared cache condition failed'):
                    run_cache(store, 'store_inputs', 'cold', root / 'original-conflict', deadline, 'cpu')
                failed = json.loads((root / 'original-conflict/summary.json').read_bytes())
                self.assertEqual([row['source_path'] for row in failed['files'] if row['resident_pages']], [str(shared)])
                def scan(*args, **kwargs):
                    kwargs.pop('reader_cpus', None)
                    return run_cache(*args, **kwargs)
                output = root / 'fixed'; output.mkdir()
                spec = dict(condition='safetensors_disk_hot', run_id='cpu',
                            sf_hot_page_cache_lease=True, cache_inventory_sha256='cpu')
                with mock.patch.object(trial, 'checked_json', return_value=inventory), mock.patch.object(trial, 'run_cache', side_effect=scan):
                    trial.cache_on_worker(spec, deadline, output)
                self.assertFalse((output / 'cache-reclaim').exists())
                self.assertFalse((output / 'cache-target-reset').exists())
                self.assertTrue((output / 'cache-target/summary.json').exists())
                self.assertTrue(json.loads((output / 'cache-target/summary.json').read_bytes())['cache_condition_passed'])
                self.assertEqual(lease.memory_status()['VmLck'], 4096)
            self.assertEqual(lease.memory_status()['VmLck'], 0)

    def test_sf_cold_still_reclaims_store_under_the_global_lease_amendment(self):
        spec = dict(condition='safetensors_disk_cold', run_id='cpu', sf_hot_page_cache_lease=True,
                    cache_inventory_sha256='cpu')
        with mock.patch.object(trial, 'checked_json', return_value=dict(store_inputs={}, safetensors_inputs={})), mock.patch.object(trial, 'run_cache') as scan:
            trial.cache_on_worker(spec, time.monotonic_ns() + 10_000_000_000)
        self.assertEqual([(call.args[1], call.args[2]) for call in scan.call_args_list],
                         [('store_inputs', 'cold'), ('store_inputs', 'check'), ('safetensors_inputs', 'cold')])

    def test_real_lock_residency_release_and_partial_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a'; path.write_bytes(b'x' * 4096)
            files = {str(path): source_stat(path)}
            before = lease.memory_status()['VmLck']
            with lease.locked_files(files, time.monotonic_ns() + 10_000_000_000) as ready:
                self.assertEqual(ready['files'], 1)
                self.assertEqual(ready['bytes'], 4096)
                self.assertEqual(ready['VmLck'], before + 4096)
                self.assertEqual(mincore_file(path, files[str(path)])[1], b'\x01')
            self.assertEqual(lease.memory_status()['VmLck'], before)
            other = Path(directory) / 'z'; other.write_bytes(b'x' * 4096)
            wrong = dict(source_stat(other), bytes=8192)
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                with lease.locked_files(dict(files, **{str(other): wrong}), time.monotonic_ns() + 10_000_000_000):
                    self.fail('mismatched file accepted')
            self.assertEqual(lease.memory_status()['VmLck'], before)

    def test_ready_then_stdin_eof_release_and_premature_helper_exit(self):
        program = "import sys,json;print(json.dumps({'state':'locked'}),flush=True);assert sys.stdin.buffer.read()==b'';print(json.dumps({'state':'released'}),flush=True)"
        process = subprocess.Popen([sys.executable, '-c', program], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            deadline = time.monotonic_ns() + 10_000_000_000
            self.assertEqual(lease.wait_line(process, deadline), {'state': 'locked'})
            self.assertIsNone(process.poll())
            process.stdin.close()
            self.assertEqual(lease.wait_line(process, deadline), {'state': 'released'})
            self.assertEqual(process.wait(timeout=5), 0)
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close()
        process = subprocess.Popen([sys.executable, '-c', 'pass'], stdout=subprocess.PIPE)
        try:
            with self.assertRaisesRegex(ValueError, 'exited before receipt'):
                lease.wait_line(process, time.monotonic_ns() + 10_000_000_000)
            process.wait(timeout=5)
        finally:
            process.stdout.close()


class StoreExclusiveColdUnderLease(unittest.TestCase):
    def test_failed_counterpart_scan_leaves_partial_receipt_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);receipt=root/'failure.json';receipt.write_text('{}');receipt.chmod(0o600)
            with mock.patch.object(lease,'run_cache',side_effect=RuntimeError('scan failed')):
                with self.assertRaisesRegex(RuntimeError,'scan failed'):
                    lease.counterpart_scan({},'cold',root,time.monotonic_ns()+1_000_000_000,'cpu')
            self.assertEqual(receipt.stat().st_mode & 0o777,0o644)

    def test_split_excludes_shared_and_exclusive_stays_cold_under_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shared = root / 'shared-config'; shared.write_bytes(b'x' * 4096)
            payload = root / 'store-payload'; payload.write_bytes(b'y' * 4096)
            for path in (shared, payload):
                with path.open('rb') as stream: os.fsync(stream.fileno())
            sf = {str(shared): source_stat(shared)}
            store = dict(sf, **{str(payload): source_stat(payload)})
            inventory = dict(safetensors_inputs=sf, store_inputs=store,
                             shared_metadata=[str(shared)])
            exclusive, shared_paths = lease.split_store_counterpart(inventory)
            self.assertEqual(shared_paths, [str(shared)])
            self.assertEqual(list(exclusive), [str(payload)])
            deadline = time.monotonic_ns() + 10_000_000_000
            for name, expected in store.items(): prepare_file(Path(name), expected, 'cold', deadline)
            with lease.locked_files(sf, deadline):
                passed = run_cache(exclusive, 'store_inputs', 'check', root / 'exclusive', deadline, 'cpu-exclusive')
                self.assertTrue(all(row['resident_pages'] == 0 for row in passed['files']))
                with self.assertRaisesRegex(ValueError, 'declared cache condition failed'):
                    run_cache(store, 'store_inputs', 'cold', root / 'all', deadline, 'cpu-all')

    def test_shared_metadata_must_be_subset_of_store(self):
        inventory = dict(store_inputs={'/a': {}}, shared_metadata=['/b'])
        with self.assertRaisesRegex(ValueError, 'shared metadata missing'):
            lease.split_store_counterpart(inventory)

    def test_locked_receipt_names_shared_state_and_exclusive_count(self):
        inventory = dict(store_inputs={'/a': {}, '/b': {}, '/c': {}},
                         safetensors_inputs={'/b': {}}, shared_metadata=['/b'])
        exclusive, shared = lease.split_store_counterpart(inventory)
        self.assertEqual(sorted(exclusive), ['/a', '/c'])
        self.assertEqual(shared, ['/b'])
        self.assertEqual(len(exclusive), len(inventory['store_inputs']) - len(shared))

    def test_shared_metadata_must_equal_actual_intersection_and_identity(self):
        for declared in [[], ['/a'], ['/b', '/b']]:
            inventory = dict(store_inputs={'/a': {}, '/b': {}}, safetensors_inputs={'/b': {}}, shared_metadata=declared)
            with self.assertRaisesRegex(ValueError, 'actual SF/Store intersection'):
                lease.split_store_counterpart(inventory)
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            lease.split_store_counterpart(dict(store_inputs={'/b': {'bytes': 1}},
                safetensors_inputs={'/b': {'bytes': 2}}, shared_metadata=['/b']))


if __name__ == '__main__':
    unittest.main()
