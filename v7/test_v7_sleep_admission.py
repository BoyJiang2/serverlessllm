"""Linux CPU denial tests: invalid admission must not reach any Docker call."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'benchmarks'))
import v7_sleep_once as runner


class SleepAdmissionTest(unittest.TestCase):
    def test_invalid_booking_or_protocol_never_starts_external_processes(self):
        now = time.time_ns()
        protocol = dict(stage='frozen', vllm_commit='132765e3560659ff63ebd236203672e991b70e08',
                        booking_id='unit-only', runtime_source_hashes={})
        admission = dict(checked_at_wall_ns=now, booking=dict(id='unit-only', user='boyjiang',
                         resource_id='h20/qjhs003', start_ms=now//10**6-1000,
                         end_ms=now//10**6+7200000))
        for case in ('other_owner', 'stale_snapshot', 'expired_booking', 'draft_protocol'):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                p, a = copy.deepcopy(protocol), copy.deepcopy(admission)
                if case == 'other_owner': a['booking']['user'] = 'other'
                if case == 'stale_snapshot': a['checked_at_wall_ns'] -= 121*10**9
                if case == 'expired_booking': a['booking']['end_ms'] = now//10**6-1000
                if case == 'draft_protocol': p['stage'] = 'draft'
                root = Path(directory)
                (root/'sleep-protocol-v4.json').write_text(json.dumps(p))
                (root/'resource-admission.json').write_text(json.dumps(a))
                with mock.patch.object(runner, 'V7', root), \
                        mock.patch.object(runner.socket, 'gethostname', return_value='qjhs3'), \
                        mock.patch.object(runner, 'call') as call, \
                        mock.patch.object(runner.subprocess, 'Popen') as start:
                    with self.assertRaises(AssertionError):
                        runner.main('v4', 'unit-only-no-gpu')
                    call.assert_not_called()
                    start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
