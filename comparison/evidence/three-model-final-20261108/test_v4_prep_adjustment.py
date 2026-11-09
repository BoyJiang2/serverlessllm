import copy
import json
from pathlib import Path
import unittest

from reproduce import target_cache_preparation


class TargetCachePreparation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / 'inputs/v4-prep/target-cache-timestamps.json'
        cls.rows = json.loads(path.read_bytes())['attempts']

    def test_all_36_use_individual_endpoints(self):
        self.assertEqual(len(self.rows), 36)
        for row in self.rows:
            result = target_cache_preparation(row)
            self.assertEqual(result['adjusted_prep_s'],
                             (result['admission_complete_ns'] - result['target_cache_begin_ns']) / 1e9)
            self.assertAlmostEqual(result['original_prep_s'],
                                   result['adjusted_prep_s'] + result['excluded_prefix_s'])

    def test_missing_begin_reports_exact_endpoint(self):
        row = copy.deepcopy(next(r for r in self.rows if r['condition'] == 'store_pool_hit'))
        del row['receipts']['trial-service-ready.json']['values']['pool_fill_begin_ns']
        with self.assertRaisesRegex(ValueError, 'pool_fill_begin_ns'):
            target_cache_preparation(row)

    def test_clock_mismatch_is_rejected(self):
        row = copy.deepcopy(self.rows[0])
        name = next(n for n in row['receipts'] if n.endswith('/summary.json'))
        row['receipts'][name]['values']['identity']['boot_id'] = 'different-boot'
        with self.assertRaises(AssertionError):
            target_cache_preparation(row)


if __name__ == '__main__':
    unittest.main()
