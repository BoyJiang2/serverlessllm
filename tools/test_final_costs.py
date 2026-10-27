import unittest

from final_costs import counters, pss_summary, span


class CostsTests(unittest.TestCase):
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
