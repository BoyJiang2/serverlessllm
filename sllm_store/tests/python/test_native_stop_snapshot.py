"""CPU-only fake /proc coverage; no Docker, CUDA, signals or Store."""
import importlib.util
import pathlib
import tempfile
import unittest
from unittest import mock

MODULE = pathlib.Path(__file__).parents[1]/'gpu512_once_controller.py'
spec = importlib.util.spec_from_file_location('stop_snapshot_controller', MODULE)
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class StopSnapshotTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.proc = self.root/'7'
        self.proc.mkdir()
        self.owner = {'pid': 7, 'startticks': 123, 'cgroup': '0::/owned'}
        fields = ['S', '1'] + ['0']*17 + ['123']
        (self.proc/'stat').write_text('7 (test name) '+' '.join(fields))
        (self.proc/'cgroup').write_text('0::/owned\n')
        (self.proc/'status').write_text('Uid:\t2147\t2147\t2147\t2147\n')
        task = self.proc/'task/7'
        task.mkdir(parents=True)
        for name in ('comm', 'stat', 'wchan', 'syscall', 'stack'):
            (task/name).write_text('sample-'+name)
        (task/'stat').write_text((self.proc/'stat').read_text())
        (task/'status').write_text('Tgid: 7\nUid: 2147 2147 2147 2147\n')
        (task/'cgroup').write_text('0::/owned\n')
        real_path = pathlib.Path
        self.patch = mock.patch.object(controller.pathlib, 'Path', side_effect=lambda value: self.root if value == '/proc' else real_path(value))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_capture(self):
        result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['status'], 'CAPTURED_PROC_ONLY')
        self.assertEqual(result['threads'][0]['syscall'], 'sample-syscall')

    def test_missing_field_is_explicit(self):
        (self.proc/'task/7/stack').unlink()
        result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['threads'][0]['stack']['error_type'], 'FileNotFoundError')

    def test_reused_identity_is_discarded(self):
        self.owner['startticks'] = 124
        result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['status'], 'DIAGNOSTIC_ERROR')
        self.assertEqual(result['threads'], [])

    def test_wrong_uid_is_discarded(self):
        (self.proc/'status').write_text('Uid: 0 0 0 0')
        self.assertEqual(controller._read_process_stop_snapshot(self.owner, float('inf'))['threads'], [])

    def test_deadline_prevents_reads(self):
        self.assertEqual(controller._read_process_stop_snapshot(self.owner, 0)['status'], 'SKIPPED_ORIGINAL_DEADLINE')

    def test_missing_pin(self):
        self.assertEqual(controller._read_process_stop_snapshot(None, float('inf'))['status'], 'INITIAL_PIN_UNAVAILABLE')

    def test_gone(self):
        (self.proc/'stat').unlink()
        self.assertEqual(controller._read_process_stop_snapshot(self.owner, float('inf'))['status'], 'ALREADY_GONE')


    def test_reader_timeout_is_nonfatal(self):
        import subprocess
        with mock.patch.object(controller.subprocess, 'run', side_effect=subprocess.TimeoutExpired('reader', 1)) as run:
            result = controller.process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['status'], 'DIAGNOSTIC_UNAVAILABLE')
        self.assertEqual(result['error_type'], 'TimeoutExpired')
        self.assertEqual(run.call_args.kwargs['timeout'], 1.0)

    def test_expired_wrapper_does_not_spawn(self):
        with mock.patch.object(controller.subprocess, 'run') as run:
            result = controller.process_stop_snapshot(self.owner, 0)
        run.assert_not_called()
        self.assertEqual(result['status'], 'SKIPPED_ORIGINAL_DEADLINE')


    def test_real_stdlib_child_none_verifies_source_shipping_without_proc(self):
        import time
        result = controller.process_stop_snapshot(None, time.monotonic() + 2)
        self.assertEqual(result['status'], 'INITIAL_PIN_UNAVAILABLE')
        self.assertEqual(result['threads'], [])

    def test_task_uid_tgid_and_cgroup_mismatch_discard_task_details(self):
        task = self.proc/'task/7'
        for field, value in [('status', 'Tgid: 7\nUid: 0 0 0 0\n'),
                             ('status', 'Tgid: 8\nUid: 2147 2147 2147 2147\n'),
                             ('cgroup', '0::/foreign\n')]:
            with self.subTest(field=field, value=value):
                original = (task/field).read_text()
                (task/field).write_text(value)
                result = controller._read_process_stop_snapshot(self.owner, float('inf'))
                self.assertEqual(result['threads'][0]['capture_status'], 'THREAD_PIN_UNAVAILABLE')
                self.assertNotIn('syscall', result['threads'][0])
                (task/field).write_text(original)

    def test_task_reused_during_capture_discards_all_thread_fields(self):
        task = self.proc/'task/7'
        real_open = type(self.proc).open
        def changed(path, *args, **kwargs):
            if path == task/'stack':
                (task/'stat').write_text((task/'stat').read_text().replace('123', '124'))
            return real_open(path, *args, **kwargs)
        with mock.patch.object(type(self.proc), 'open', changed):
            result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['threads'][0]['capture_status'], 'THREAD_CHANGED_OR_GONE')
        self.assertNotIn('syscall', result['threads'][0])

    def test_main_identity_changed_after_capture_discards_all_details(self):
        task = self.proc/'task/7'
        real_open = type(self.proc).open
        def changed(path, *args, **kwargs):
            if path == task/'stack':
                (self.proc/'stat').write_text((self.proc/'stat').read_text().replace('123', '124'))
            return real_open(path, *args, **kwargs)
        with mock.patch.object(type(self.proc), 'open', changed):
            result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['status'], 'DIAGNOSTIC_ERROR')
        self.assertEqual(result['threads'], [])
        self.assertNotIn('process_status', result)

    def test_permission_denied_is_explicit_without_fallback(self):
        import errno
        task = self.proc/'task/7'
        real_open = type(self.proc).open
        def denied(path, *args, **kwargs):
            if path == task/'stack':
                raise PermissionError(errno.EACCES, 'denied')
            return real_open(path, *args, **kwargs)
        with mock.patch.object(type(self.proc), 'open', denied):
            result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['threads'][0]['stack'], {'error_type': 'PermissionError', 'errno': errno.EACCES})

    def test_thread_count_and_field_text_are_bounded(self):
        task = self.proc/'task/7'
        (task/'wchan').write_text('x'*5000)
        for tid in range(1000, 1256):
            other = self.proc/'task'/str(tid)
            other.mkdir()
            (other/'status').write_text('Tgid: 7\nUid: 2147 2147 2147 2147\n')
            (other/'cgroup').write_text('0::/owned\n')
            (other/'stat').write_text((task/'stat').read_text())
        result = controller._read_process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['thread_count'], 257)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['threads']), 256)
        self.assertEqual(len(result['threads'][0]['wchan']), 4096)

    def test_mid_snapshot_deadline_stops_additional_field_capture(self):
        with mock.patch.object(controller.time, 'monotonic', side_effect=[0, 0, 0, 2]):
            result = controller._read_process_stop_snapshot(self.owner, 1)
        self.assertTrue(result['deadline_reached'])
        self.assertIn('comm', result['threads'][0])
        self.assertNotIn('wchan', result['threads'][0])

    def test_source_inspection_error_is_nonfatal(self):
        with mock.patch.object(controller.inspect, 'getsource', side_effect=OSError('source unavailable')):
            result = controller.process_stop_snapshot(self.owner, float('inf'))
        self.assertEqual(result['status'], 'DIAGNOSTIC_UNAVAILABLE')
        self.assertEqual(result['error_type'], 'OSError')

    def test_deadline_expired_during_source_build_does_not_spawn(self):
        with mock.patch.object(controller.time, 'monotonic', side_effect=[0, 2]), mock.patch.object(controller.subprocess, 'run') as run:
            result = controller.process_stop_snapshot(self.owner, 1)
        run.assert_not_called()
        self.assertEqual(result['status'], 'SKIPPED_ORIGINAL_DEADLINE')

if __name__ == '__main__':
    unittest.main()
