import unittest
import json

from final_costs import counters, pss_summary, span, historical_monitor
from historical_costs import loading_rank_pair, c23_controller_audit


class CostsTests(unittest.TestCase):
    def test_real_archive_cannot_split_shared_controller_into_conditions(self):
        result = c23_controller_audit()
        self.assertFalse(result['controller_recoverable_by_condition'])
        self.assertEqual(len(result['c23_attempts']), 26)
        self.assertTrue(all(r['controller_elapsed_s'] is None for r in result['c23_attempts']))
        self.assertEqual(result['shared_parents']['head']['child_returncode'], 1)
        self.assertGreaterEqual(result['shared_final_release_observation_s'], 60)

    def test_loading_pair_missing_duplicate_or_wrong_process(self):
        first = dict(name='weights_pipeline_including_allocation_begin', rank=0,
                     pid=12, container_hostname='owned', monotonic_ns=10)
        last = dict(first, name='weights_pipeline_including_allocation_end', monotonic_ns=20)
        pair, error = loading_rank_pair([first, last], 0, 1, 30)
        self.assertEqual(pair, (first, last))
        self.assertIsNone(error)
        self.assertNotIn('process_start_ticks', pair[0])
        self.assertIsNone(loading_rank_pair([first], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, first, last], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, dict(last, pid=13)], 0, 1, 30)[0])
        self.assertIsNone(loading_rank_pair([first, dict(last, monotonic_ns=9)], 0, 1, 30)[0])

    def test_historical_windows_and_missing_gpu(self):
        def row(t, value):
            gpu = None if value is None else '\n'.join(
                f'{i}, GPU-{i}, 100 MiB, {value+i} MiB, 0 %' for i in range(8))
            return json.dumps(dict(monotonic_ns=t, gpu=gpu,
                                   cgroup_memory_current=t, foreign_gpu_pids=[])).encode()+b'\n'
        out = historical_monitor([row(1, 0), row(2, None), row(3, 20), b'bad'],
                                 [dict(begin_ns=1, end_ns=2), dict(begin_ns=2, end_ns=3)])
        self.assertEqual(out['whole']['samples'], 3)
        self.assertEqual(out['whole']['missing_gpu'], 1)
        self.assertEqual(out['windows'][0]['summary']['gpu_peak_MiB'], list(range(8)))
        self.assertEqual(out['windows'][1]['summary']['gpu_peak_MiB'], list(range(20, 28)))
        self.assertEqual(out['malformed_line_numbers'], [4])
        missing = historical_monitor([row(2, None)], [])
        self.assertEqual(missing['whole']['gpu_peak_MiB'], [None]*8)
        with self.assertRaises(AssertionError):
            historical_monitor([row(3, 0), row(1, 0)], [])

    def test_counter_units_and_span(self):
        self.assertEqual(counters('Pss: 7 kB\nLocked: 0 kB\n'), dict(Pss=7, Locked=0))
        self.assertEqual(span(100, 1000000100), 1)
        with self.assertRaises(AssertionError):
            span(2, 1)

    def test_unreadable_is_not_zero(self):
        text = '00400000-7fff ---p 00000000 00:00 0 [rollup]\nRss: 9 kB\nPss: 7 kB\nLocked: 0 kB\nPss_Shmem: 1 kB\n'
        snapshot = dict(store_pid=8, processes=[dict(pid=8, smaps_rollup=text), dict(pid=9, smaps_rollup=None)])
        out = pss_summary(snapshot)
        self.assertIsNone(out['all_readable_sum'])
        self.assertEqual(out['store']['Pss_bytes'], 7*1024)
        snapshot['processes'].pop()
        self.assertEqual(pss_summary(snapshot)['all_readable_sum']['Rss_bytes'], 9*1024)


if __name__ == '__main__':
    unittest.main()
