import unittest
from contract import IMAGE, MODEL_REVISION, MAX_SECONDS, SCOPE, budget, validate_scope


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.config=dict(scope=SCOPE,run_id='sllm-smoke-20261028T120000Z-12345678',ssh_host='qjhs003',
                         image_id=IMAGE,model_revision=MODEL_REVISION,tensor_parallel_size=8,
                         weight_format='serverless_llm',lifecycle_cycles=1,full_rank_weight_audit=True,
                         business_gold_required=True,fixed_raw_tokens=32,natural_eos_separate=True,
                         formal_ids_consumed=0)

    def test_full_budget_includes_observation_shutdown_release_and_evidence(self):
        self.assertEqual(MAX_SECONDS,2340)
        plan=budget(10,10**12,10**12+MAX_SECONDS*10**9)
        self.assertFalse(plan['runtime_admission'])
        self.assertEqual(plan['deadlines']['evidence_end_ns'],10+2340*10**9)
        self.assertEqual(plan['deadlines']['instance_idle_observation_end_ns']-
                         plan['deadlines']['bootstrap_and_business_end_ns'],60*10**9)

    def test_one_nanosecond_short_rejected(self):
        with self.assertRaises(TimeoutError):budget(10,10**12,10**12+MAX_SECONDS*10**9-1)

    def test_invalid_clock_or_boolean_rejected(self):
        for value in (True,0,-1,1.0):
            with self.assertRaises(ValueError):budget(value,10**12,10**15)

    def test_scope_keeps_official_fp8_and_all_correctness_checks(self):
        validate_scope(self.config)
        for key,value in [('tensor_parallel_size',1),('full_rank_weight_audit',False),
                          ('formal_ids_consumed',1),('natural_eos_separate',False),
                          ('run_id','store_pool_hit_03'),('ssh_host','qjhs001')]:
            config=dict(self.config);config[key]=value
            with self.assertRaises(ValueError):validate_scope(config)


if __name__=='__main__':unittest.main()
