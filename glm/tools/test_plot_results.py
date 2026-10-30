"""Regression audit of the generated cohort, failures, cache evidence and hashes."""
import collections
import csv
import hashlib
import json
import statistics
import unittest

from final_statistics import ROOT, load_rows


class FigureAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_rows()
        cls.dest = ROOT/'results/figures'

    def records(self, name):
        with (self.dest/name).open(encoding='utf-8', newline='') as f:
            return list(csv.DictReader(f))

    def test_primary_cohort_not_matched_subset_or_history(self):
        table = self.records('c23-all26-metrics.csv')
        expected = [r for r in self.rows if r['protocol_revision'] == 'v13-canonical23-qualified-disk-jit-seed']
        self.assertEqual([r['attempt_id'] for r in table], [r['attempt_id'] for r in expected])
        self.assertEqual(collections.Counter(r['condition'] for r in expected),
                         dict(store_pool_hit=7, store_disk_cold=7, safetensors_disk_cold=6, safetensors_disk_hot=6))
        for exported, raw in zip(table, expected):
            for key, value in raw['metrics'].items():
                self.assertEqual(float(exported[key]), value)

    def test_protocol_summary_recomputed_and_failures_retained(self):
        raw = self.records('all40-stratified.csv')
        self.assertEqual(len(raw), 40)
        self.assertEqual(collections.Counter(r['status'] for r in raw), dict(success=35, failed=5))
        for r in raw:
            if r['status'] == 'failed':
                self.assertEqual(r['weight_ingestion_span_s'], '')
        for s in self.records('protocol-summary.csv'):
            cohort = [r for r in self.rows if (r['protocol_revision'] or 'UNKNOWN_RAW_METADATA') == s['protocol_revision']
                      and r['condition'] == s['condition']]
            values = [r['metrics'][s['metric']] for r in cohort if r['status'] == 'success']
            self.assertEqual(int(s['attempted']), len(cohort))
            self.assertEqual(int(s['success']), len(values))
            self.assertEqual(int(s['failed']), len(cohort)-len(values))
            if values:
                self.assertEqual(float(s['mean']), statistics.mean(values))
            else:
                self.assertEqual(s['mean'], '')

    def test_hot_low_value_preserved_and_endpoints_complete(self):
        hot = self.records('hot-seven-residency.csv')
        self.assertEqual([r['attempt_id'][-2:] for r in hot], ['04', '05', '06', '07', '08', '09', '10'])
        self.assertEqual(float(hot[4]['weight_s']), 65.660524814)
        for r in hot:
            for when in ('pre', 'post'):
                self.assertEqual(int(r[f'{when}_files']), 148)
                self.assertEqual(int(r[f'{when}_pages']), 184621464)
                self.assertEqual(r[f'{when}_pages'], r[f'{when}_resident_pages'])
                self.assertEqual(float(r[f'{when}_minimum_file_fraction']), 1)
            for interval in ('preparation', 'service_through_postscan'):
                for counter in ('pgscan_kswapd', 'pgscan_direct', 'pgsteal_kswapd', 'pgsteal_direct'):
                    self.assertEqual(int(r[f'{interval}_{counter}_global_delta']), 0)

    def test_source_and_all_tracked_hashes(self):
        m = json.loads((self.dest/'manifest.json').read_text())
        for p, sha in m['inputs'].items():
            self.assertEqual(hashlib.sha256((ROOT/p).read_bytes()).hexdigest(), sha, p)
        for p, sha in m['tracked_outputs'].items():
            self.assertEqual(hashlib.sha256((self.dest/p).read_bytes()).hexdigest(), sha, p)
        self.assertEqual(hashlib.sha256((ROOT/'tools/plot_results.py').read_bytes()).hexdigest(), m['source_sha256'])


if __name__ == '__main__':
    unittest.main()
