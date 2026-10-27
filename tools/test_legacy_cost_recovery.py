import copy
import unittest

from legacy_cost_recovery import docker_ns, validate_probe_rows


class LegacyCostTests(unittest.TestCase):
    def test_nanoseconds_not_truncated(self):
        self.assertEqual(docker_ns('2026-10-24T08:11:59.260838414Z')-
                         docker_ns('2026-10-24T08:03:33.793396485Z'),505467441929)
        self.assertEqual(docker_ns('2026-10-24T08:00:00Z'),docker_ns('2026-10-24T08:00:00.000000000Z'))
        with self.assertRaises(AssertionError):
            docker_ns('2026-10-24T08:00:00')

    def test_probe_bounds_and_unique_devices(self):
        size = 100*1024**3
        before = size+5*1024**3
        rows = [dict(device=i,allocation_bytes=size,free_before_bytes=before,
                     free_while_allocated_bytes=before-size,free_after_bytes=before-32*1024**2,
                     begin_monotonic_ns=1,end_monotonic_ns=2) for i in range(8)]
        self.assertEqual(validate_probe_rows(rows),32*1024**2)
        for key,value in [('device',1),('free_after_bytes',before-65*1024**2),
                          ('free_while_allocated_bytes',before),('allocation_bytes',1)]:
            bad = copy.deepcopy(rows);bad[0][key]=value
            with self.assertRaises(AssertionError):
                validate_probe_rows(bad)


if __name__ == '__main__':
    unittest.main()
