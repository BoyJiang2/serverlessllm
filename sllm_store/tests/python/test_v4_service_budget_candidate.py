"""CPU-only checks for the proposed service budget; never launch containers."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import v4_container_guard as guard
import v4_native_release as release
import v4_service_once as service
from test_v4_container_guard import CID, row
from test_v4_native_release import BASELINE, snapshot


class ServiceBudgetCandidate(unittest.TestCase):
    def test_guard_keeps_other_purposes_at_1200_and_service_at_1680(self):
        now = 1_000_000_000
        for purpose in ('native-reference', 'store-conversion', 'store-recovery', 'native-service'):
            limit = 1680 if purpose == 'native-service' else 1200
            with mock.patch.object(guard.time, 'monotonic_ns', return_value=now), \
                    mock.patch.object(guard, 'inspect') as inspect:
                with self.assertRaises(ValueError):
                    guard.watch(CID, now + limit * 1_000_000_000 + 1, 'unused', purpose)
                inspect.assert_not_called()
        initial = row()
        initial['Config']['Labels'][guard.LABEL] = 'native-service'
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(guard.time, 'monotonic_ns', return_value=now), \
                mock.patch.object(guard, 'inspect', side_effect=[initial, row('exited'), row('exited')]), \
                mock.patch.object(guard.signal, 'signal'):
            result = guard.watch(CID, now + 1680_000_000_000, Path(directory) / 'guard', 'native-service')
            self.assertEqual(result['deadline_ns'], now + 1680_000_000_000)

    def test_release_budget_binds_original_preparation_and_preserves_sixty_seconds(self):
        for purpose in ('native-reference', 'store-conversion', 'store-recovery', 'native-service'):
            limit = 1800 if purpose == 'native-service' else 1200
            job = dict(identity=dict(container_id=CID), cgroup='/sys/fs/cgroup/docker/' + CID,
                       worker_processes=[dict(pid=123, start_ticks=456)], baseline_gpu_mib=BASELINE,
                       boot_id='fixture', time_namespace='fixture', preparation_begin_ns=1,
                       delete_ack_ns=1, deadline_ns=1 + limit * 1_000_000_000, purpose=purpose)
            now = [1_000_000_000]
            def sleep(seconds):
                now[0] += int(seconds * 1e9)
            with tempfile.TemporaryDirectory() as directory, \
                    mock.patch.object(release, 'process_identity', return_value=dict(boot_id='fixture', time_namespace='fixture')), \
                    mock.patch.object(release.time, 'monotonic_ns', side_effect=lambda: now[0]), \
                    mock.patch.object(release.time, 'sleep', side_effect=sleep):
                invalid = dict(job, deadline_ns=job['deadline_ns'] + 1)
                with self.assertRaisesRegex(ValueError, 'original deadline'):
                    release.collect(invalid, Path(directory) / 'invalid', sample=lambda job: snapshot())
                output = Path(directory) / 'valid'
                release.collect(job, output, sample=lambda job: dict(
                    snapshot(), sample_begin_ns=now[0], sample_end_ns=now[0]))
                self.assertEqual(len((output / 'samples.jsonl').read_text().splitlines()), 61)
                result = json.loads((output / 'result.json').read_bytes())
                self.assertEqual(result['coverage']['sample_span_seconds'], 60)
                self.assertEqual(json.loads((output / 'job.json').read_bytes())['preparation_begin_ns'], 1)

    def test_extended_service_release_requires_actual_matching_container_label(self):
        container = row('exited')
        job = dict(identity=dict(container_id=CID, created=container['Created'], image=container['Image']),
                   purpose='native-service')
        with mock.patch.object(release, 'call', return_value=json.dumps([container])) as call:
            with self.assertRaisesRegex(ValueError, 'service container label'):
                release.snapshot(job)
            self.assertEqual(call.call_count, 1)

    def test_plan_and_peer_kill_share_the_same_absolute_budget(self):
        output = io.StringIO()
        with mock.patch('sys.argv', ['service', '/cpu/unused', 'a' * 40]), \
                mock.patch.object(service, 'call') as call, contextlib.redirect_stdout(output):
            service.main()
        call.assert_not_called()
        plan = json.loads(output.getvalue())
        self.assertEqual((plan['total_seconds'], plan['container_seconds']), (1800, 1680))
        self.assertEqual((plan['head_cpu'], plan['worker_cpu']), (4, 4))
        self.assertEqual(plan['memory_limit_total'], 1024**4)
        with mock.patch.object(service, 'CALL_DEADLINE', 110_000_000_000), \
                mock.patch.object(service.time, 'monotonic_ns', return_value=100_000_000_000), \
                mock.patch.object(service.subprocess, 'check_output', return_value='') as execute:
            service.call('docker', 'kill', 'fixture', timeout=90)
            self.assertEqual(execute.call_args.kwargs['timeout'], 10)


if __name__ == '__main__':
    unittest.main()
