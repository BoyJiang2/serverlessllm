"""Tiny real Linux mlock and owned-helper pipe contracts; no model or GPU."""
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import v4_cache_lease as lease
from v4_cache_state import source_stat, mincore_file


class CacheLeaseContracts(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
