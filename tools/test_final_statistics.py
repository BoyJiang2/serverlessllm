import copy
import hashlib
import json
import random
import unittest

import final_statistics as s


def fixture():
    rows = []
    for block in range(1, 11):
        for condition in s.CONDITIONS:
            row = dict.fromkeys(s.STRATUM_FIELDS)
            row.update(attempt_id=f'{condition}_{block:02}', condition=condition, block=block,
                       status='success', execution_run_id=s.RUN if block >= 4 else None,
                       code_commit='new' if block >= 4 else None, metrics={'seconds': block})
            rows.append(row)
    return rows


class StatisticsTests(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(s.describe([1, 2, 3, 4, 5, 6]),
                         dict(n=6, mean=3.5, median=3.5, p90_nearest_rank=6, minimum=1, maximum=6))
        self.assertEqual(s.percentile([0, 10], .025), .25)
        for invalid in ([], [True], [float('nan')], [-1], [float('inf')]):
            with self.assertRaises(AssertionError):
                s.describe(invalid)

    def test_wilson(self):
        lo, hi = s.wilson(5, 10)
        self.assertAlmostEqual(lo, .236593090512564)
        self.assertAlmostEqual(hi, .763406909487436)
        self.assertAlmostEqual(s.wilson(0, 10)[0], 0)
        self.assertAlmostEqual(s.wilson(10, 10)[1], 1)

    def test_paired_shared_draws_and_boundary(self):
        rows = fixture()
        # The actual boundary has Store block4 only in C23.
        for row in rows:
            if row['block'] == 4 and row['condition'].startswith('safetensors'):
                row.update(execution_run_id='older', code_commit='old')
        accepted = {r['attempt_id'] for r in rows if r['execution_run_id'] == s.RUN}
        out = s.summarize(rows, accepted)
        self.assertEqual(len(out['rows']), 40)
        self.assertTrue(all(v['n'] == 10 for v in out['historical_condition_outcomes'].values()))
        self.assertEqual(sum(g['attempted'] for g in out['strata']), 40)
        current = [g for g in out['strata'] if g['stratum']['code_commit'] == 'new']
        self.assertEqual(sorted(g['attempted'] for g in current), [6, 6, 7, 7])
        self.assertTrue(any(g['stratum']['node'] is None for g in out['strata']))
        matched = out['c23_matched']
        rng = random.Random(20261024)
        draws = [[rng.randrange(6) for _ in range(6)] for _ in range(10000)]
        digest = hashlib.sha256(json.dumps(draws, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(matched['draw_indices_sha256'], digest)
        vectors = [matched['intervals'][c]['seconds'] for c in s.CONDITIONS]
        self.assertTrue(all(v['point']['n'] == 6 and v['point']['mean'] == 7.5 for v in vectors))
        self.assertTrue(all(v['ci95'] == vectors[0]['ci95'] for v in vectors))
        self.assertTrue(all('_04' not in r['attempt_id'] for v in vectors for r in v['raw']))

    def test_all_intervals_suppressed_on_missing_failed_unaccepted_or_duplicate(self):
        baseline = fixture()
        accepted = {r['attempt_id'] for r in baseline}
        for kind in ('missing', 'failed', 'unaccepted', 'duplicate'):
            rows = copy.deepcopy(baseline)
            target = next(r for r in rows if r['block'] == 7)
            allow = set(accepted)
            if kind == 'missing':
                rows.remove(target)
            elif kind == 'failed':
                target['status'] = 'failed'
            elif kind == 'unaccepted':
                allow.remove(target['attempt_id'])
            else:
                rows.append(dict(target))
            out = s.paired_analysis(rows, allow)
            self.assertIsNone(out['intervals'])
            self.assertTrue(out['ineligible_ids'])

    def test_historical_failure_stays_in_denominator(self):
        rows = fixture()
        rows[0]['status'] = 'failed'
        out = s.summarize(rows, {r['attempt_id'] for r in rows})
        self.assertEqual(out['historical_condition_outcomes'][s.CONDITIONS[0]]['success'], 9)
        self.assertEqual(out['historical_condition_outcomes'][s.CONDITIONS[0]]['failed'], 1)
        self.assertEqual(out['historical_condition_outcomes'][s.CONDITIONS[0]]['n'], 10)


if __name__ == '__main__':
    unittest.main()
