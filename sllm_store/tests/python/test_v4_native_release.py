"""Synthetic release samples and clocks only; no GPU or physical release proof."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import v4_native_release as release


CID = 'b' * 64
BASELINE = {'GPU-fixture-' + str(i): 10 for i in range(8)}


def snapshot():
    return dict(sample_begin_ns=100, sample_end_ns=100,
        container_state=dict(Status='exited', Running=False, Pid=0), restart_count=0,
        gpu_raw='\n'.join(uuid + ', 10' for uuid in BASELINE), compute_apps_raw='',
        cgroup=dict(exists=True, files={'cgroup.procs': '', 'pids.current': '0'}), original_workers_live=[])


class NativeReleaseContracts(unittest.TestCase):
    def test_missing_optional_memory_peak_is_unknown_and_required_metrics_remain_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            group = Path(directory)
            for name, value in [('memory.current', '123'), ('memory.events', 'oom 0'), ('pids.current', '1')]:
                (group / name).write_text(value)
            self.assertIsNone(release.cgroup_telemetry(group)['memory.peak'])
            self.assertIsNone(release.cgroup_telemetry(group)['io.stat'])
            (group / 'memory.peak').write_text('456')
            self.assertEqual(release.cgroup_telemetry(group)['memory.peak'], '456')
            (group / 'memory.current').unlink()
            with self.assertRaises(FileNotFoundError):
                release.cgroup_telemetry(group)

    def test_empty_selected_gpu_sample_is_not_zero_memory(self):
        with self.assertRaisesRegex(ValueError, 'coverage'):
            release.check_snapshot(dict(snapshot(), gpu_raw=''), BASELINE)

    def test_memory_and_compute_process_limits(self):
        for change in (dict(gpu_raw=snapshot()['gpu_raw'].replace(', 10', ', 27', 1)),
                       dict(compute_apps_raw='123, GPU-fixture-0, 0')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                release.check_snapshot(dict(snapshot(), **change), BASELINE)

    def test_container_cgroup_and_original_process_must_all_be_gone(self):
        for change in (dict(container_state=dict(Status='running', Running=True, Pid=123)),
                       dict(restart_count=1), dict(original_workers_live=[dict(pid=123)]),
                       dict(cgroup=dict(exists=True, files={'cgroup.procs': '123', 'pids.current': '1'})),
                       dict(cgroup=dict(exists=True, files={'pids.current': '0'}))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                release.check_snapshot(dict(snapshot(), **change), BASELINE)

    def test_disappeared_bound_cgroup_is_recorded_not_fabricated_memory_zero(self):
        release.check_snapshot(dict(snapshot(), cgroup=dict(exists=False, files={})), BASELINE)

    def test_cpu_virtual_clock_sixty_seconds_and_failure_raw_preservation(self):
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as directory:
                now = [1_000_000_000]
                job = dict(identity=dict(container_id=CID), cgroup='/sys/fs/cgroup/docker/' + CID,
                    worker_processes=[dict(pid=123, start_ticks=456)], baseline_gpu_mib=BASELINE,
                    boot_id='fixture', time_namespace='fixture', preparation_begin_ns=1,
                    delete_ack_ns=1, deadline_ns=100_000_000_000)
                def sample(job):
                    return dict(snapshot(), sample_begin_ns=now[0], sample_end_ns=now[0],
                                gpu_raw='' if failed else snapshot()['gpu_raw'])
                def sleep(seconds):
                    now[0] += int(seconds * 1e9)
                output = Path(directory) / 'observations'
                with mock.patch.object(release, 'process_identity', return_value=dict(boot_id='fixture', time_namespace='fixture')), \
                        mock.patch.object(release.time, 'monotonic_ns', side_effect=lambda: now[0]), \
                        mock.patch.object(release.time, 'sleep', side_effect=sleep):
                    if failed:
                        with self.assertRaisesRegex(ValueError, 'coverage'):
                            release.collect(job, output, sample=sample)
                    else:
                        release.collect(job, output, sample=sample)
                lines = (output / 'samples.jsonl').read_text().splitlines()
                self.assertEqual(len(lines), 1 if failed else 61)
                receipt = json.loads((output / ('failure.json' if failed else 'result.json')).read_bytes())
                self.assertFalse(receipt['physical_cleanup_verified'])
                if not failed:
                    self.assertEqual(receipt['coverage']['sample_span_seconds'], 60)


if __name__ == '__main__':
    unittest.main()
