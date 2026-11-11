"""CPU tests of observation gaps, provenance and retained resource requests."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[3]/'benchmarks'))
import v7_observe
import v7_qualify_sleep as qualifier


class ObservationTest(unittest.TestCase):
    def setUp(self):
        self.uuids = ['GPU-'+str(uuid.UUID(int=i+1)) for i in range(8)]
        self.constraints = dict(NanoCpus=32*10**9, Memory=1536*1024**3,
                                DeviceRequests=[dict(DeviceIDs=self.uuids)], PidMode='host',
                                PidsLimit=4096, CpusetCpus='')
        self.samples = []
        for i in range(76):
            ns = i*10**9
            self.samples.append(dict(ns=ns, completed_ns=ns+300000000,
                boot_id='boot', time_namespace='time',
                gpu=dict(exit=0, started_ns=ns+100000000, completed_ns=ns+200000000,
                         stdout='\n'.join(f'{j}, {u}, 1000, 0' for j, u in enumerate(self.uuids))),
                apps=dict(exit=0, stdout='100, 1000\n'), app_cgroups={'100': 'docker/owned'},
                containers={'backend': dict(cid='owned', state=dict(Running=True), constraints=copy.deepcopy(self.constraints),
                    cgroup_path='docker/owned', cgroup={'cpu.max': '3200000 100000',
                    'memory.events': 'oom 0\noom_kill 0\n', 'memory.current': '1000000'})}))

    def check(self):
        return qualifier.gpu_window(self.samples, 0, 76*10**9, ('boot', 'time'), self.uuids,
                                    container=('backend', 'owned', self.constraints), minimum_seconds=60)

    def test_complete_real_time_window(self):
        result = self.check()
        self.assertGreaterEqual(result['seconds'], 60)
        self.assertEqual(result['missing'], [])

    def test_failed_query_is_missing_not_zero(self):
        self.samples[20]['gpu'].update(exit=-1, stdout=None)
        result = self.check()
        self.assertEqual(result['missing'], [20*10**9])
        self.assertEqual(len(result['valid']), 75)

    def test_large_gap_cannot_pass(self):
        for sample in self.samples[15:25]:
            sample['gpu']['exit'] = -1
        with self.assertRaises(AssertionError):
            self.check()

    def test_other_clock_resource_request_or_process_is_rejected(self):
        changes = [('clock', lambda s: s.update(boot_id='other')),
                   ('request', lambda s: s['containers']['backend']['constraints'].update(NanoCpus=1)),
                   ('foreign', lambda s: s['app_cgroups'].update({'100': 'docker/foreign'})),
                   ('oom', lambda s: s['containers']['backend']['cgroup'].update({'memory.events': 'oom 1\noom_kill 0\n'}))]
        original = copy.deepcopy(self.samples)
        for name, change in changes:
            with self.subTest(name=name):
                self.samples = copy.deepcopy(original)
                change(self.samples[20])
                with self.assertRaises(AssertionError):
                    self.check()

    def test_process_exit_race_is_missing(self):
        self.samples[20]['app_cgroups']['100'] = dict(process_race='gone')
        self.assertEqual(len(self.check()['missing']), 1)

    def test_query_timeout_is_preserved(self):
        with mock.patch.object(v7_observe.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['query'], 5)):
            result = v7_observe.observed_run(['query'])
        self.assertEqual(result['exit'], -1)
        self.assertIsNone(result['stdout'])
        self.assertIn('TimeoutExpired', result['stderr'])

    def test_native_full_load_requires_all_rank_endpoints(self):
        events, workers = [], []
        for rank in range(8):
            workers.append(dict(rank=rank, identity=dict(pid=rank+10, process_start_ticks=1)))
            for i, suffix in enumerate(('begin', 'end')):
                events.append(dict(name='full_weight_load_'+suffix, rank=rank, sequence=i,
                                   pid=rank+10, process_start_ticks=1, boot_id='boot', time_namespace='time',
                                   clock_id='CLOCK_MONOTONIC', monotonic_ns=100+rank*10+i))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output/'events').mkdir()
            trace = output/'events/test.jsonl'
            trace.write_text(''.join(json.dumps(e)+'\n' for e in events))
            self.assertEqual(len(qualifier.full_load_spans(output, workers, ('boot', 'time'), 0, 1000)), 8)
            trace.write_text(''.join(json.dumps(e)+'\n' for e in events[:-1]))
            with self.assertRaises(AssertionError):
                qualifier.full_load_spans(output, workers, ('boot', 'time'), 0, 1000)


if __name__ == '__main__':
    unittest.main()
