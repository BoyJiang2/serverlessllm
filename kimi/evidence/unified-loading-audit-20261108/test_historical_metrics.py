import csv, json, unittest
from pathlib import Path
from organize_historical_metrics import PRIMARY, CONDITIONS, summary
P=Path(__file__).resolve().parent
class HistoricalConsumerTest(unittest.TestCase):
    def test_missing_is_null_not_zero(self):
        self.assertEqual(summary([None]*8),dict(raw=[None]*8,n=0,mean=None,median=None))
    def test_observed_zero_remains_a_value(self):
        self.assertEqual(summary([None,0,2]),dict(raw=[None,0,2],n=2,mean=1,median=1))
    def test_cohort_and_primary_membership(self):
        d=json.loads((P/'attempts.json').read_text());r=d['attempts']
        self.assertEqual(len(r),46)
        self.assertEqual(sum(x['cohort']=='original' for x in r),40)
        self.assertEqual(sum(x['cohort']=='original' and x['original_qualified'] for x in r),38)
        self.assertEqual(sum(x['primary'] for x in r),32)
        self.assertEqual(sorted(set(x['block'] for x in r if x['primary'])),PRIMARY)
    def test_csv_consumer_matches_json_endpoints(self):
        js=json.loads((P/'rank-endpoints.json').read_text())['rows']
        with (P/'rank-endpoints.csv').open(newline='') as f: rows=list(csv.DictReader(f))
        self.assertEqual(len(js),len(rows));self.assertEqual(len(rows),368)
        for a,b in zip(js,rows):
            self.assertEqual(a['run_id'],b['run_id'])
            self.assertEqual(a['expected_rank'],int(b['expected_rank']))
            self.assertIsNone(a['rank_begin_ns']);self.assertEqual(b['rank_begin_ns'],'')
            self.assertIsNone(a['rank_end_ns']);self.assertEqual(b['rank_end_ns'],'')
    def test_actual_summary_consumer_preserves_nulls_and_denominators(self):
        d=json.loads((P/'condition-summaries.json').read_text())['conditions']
        for condition in CONDITIONS:
            r=d[condition];self.assertEqual(r['block_ids'],PRIMARY)
            self.assertEqual(len(r['run_ids']),8)
            for key in ['weight_load_host_all_rank_span_s','max_rank_weight_load_host_duration_s']:
                self.assertEqual(r[key],dict(raw=[None]*8,n=0,mean=None,median=None))
            self.assertEqual(r['specialized_single_f7_log_duration_s']['n'],8)
    def test_f7_never_supplies_endpoints(self):
        rows=json.loads((P/'attempts.json').read_text())['attempts']
        for r in rows:
            self.assertIsNone(r['weight_load_host_all_rank_span_s'])
            self.assertEqual(r['valid_rank_endpoint_pairs'],0)
            f7=r['specialized_f7_observation']
            if f7:
                self.assertIsNone(f7['rank']);self.assertIsNone(f7['pid'])
if __name__=='__main__':unittest.main(verbosity=2)
