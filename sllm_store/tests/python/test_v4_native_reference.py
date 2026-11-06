"""Native reference CPU contracts; engine outputs here are explicit fixtures."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import v4_native_reference as native


ROOT = Path(__file__).resolve().parents[3]


def response(text="4", tokens=None, finish="stop"):
    return NS(outputs=[NS(text=text, token_ids=[22, 1] if tokens is None else tokens,
                          finish_reason=finish, stop_reason=None)])


class NativeReferenceContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / "native"
        self.requests = native.inputs(ROOT)
        self.engine = NS(generate=mock.Mock(side_effect=[[response()],
            [response("1, 2, 3", [19] * 32, "length")]]),
            llm_engine=NS(engine_core=NS(shutdown=mock.Mock()), vllm_config=NS(
                model_config=NS(revision=native.REVISION, tokenizer_revision=native.REVISION,
                    seed=20261105, quantization='deepseek_v4_fp8', dtype='torch.bfloat16',
                    max_model_len=2048, enforce_eager=True),
                parallel_config=NS(tensor_parallel_size=8, pipeline_parallel_size=1,
                    data_parallel_size=1, enable_expert_parallel=False, distributed_executor_backend='mp'),
                cache_config=NS(cache_dtype='fp8_ds_mla', enable_prefix_caching=False, gpu_memory_utilization=0.92),
                attention_config=NS(use_fp4_indexer_cache=False), speculative_config=None,
                scheduler_config=NS(max_num_seqs=1, max_num_batched_tokens=2048))))

    def run_reference(self):
        native.run_reference(native.engine_config(), self.requests, self.output,
                             mock.Mock(return_value=self.engine), lambda **kwargs: kwargs)

    def test_frozen_requests_and_approved_config(self):
        self.assertEqual([len(r["input_tokens"]) for r in self.requests], [30, 43])
        config = native.engine_config()
        self.assertEqual((config['seed'], config['distributed_executor_backend']), (20261105, 'mp'))
        self.assertEqual(config['kv_cache_dtype'], 'fp8')
        self.assertEqual([r['request']['sampling']['ignore_eos'] for r in self.requests], [False, False])

    def test_config_only_rejected_before_index_or_weights(self):
        root = Path(self.temp.name)
        (root / 'CONFIG_ONLY_NOT_RUNTIME_WEIGHTS').touch()
        with self.assertRaisesRegex(ValueError, 'config-only'):
            native.model_inventory(root)

    def test_malformed_business_durable_before_validation_and_fixed_skipped(self):
        self.engine.generate.side_effect = [[]]
        with self.assertRaisesRegex(ValueError, 'one native output'):
            self.run_reference()
        self.assertEqual(json.loads((self.output / 'business.raw.json').read_bytes())['outputs'], [])
        self.assertFalse((self.output / 'workload32.raw.json').exists())
        self.assertEqual(self.engine.generate.call_count, 1)
        self.engine.llm_engine.engine_core.shutdown.assert_called_once_with(timeout=60)

    def test_fixed_failure_retains_both_raw_responses(self):
        self.engine.generate.side_effect = [[response()], [response('bad', [19], 'length')]]
        with self.assertRaisesRegex(ValueError, 'fixed32'):
            self.run_reference()
        self.assertTrue((self.output / 'business.raw.json').is_file())
        self.assertTrue((self.output / 'workload32.raw.json').is_file())
        self.assertFalse((self.output / 'candidate.json').exists())

    def test_shutdown_failure_cannot_publish_candidate(self):
        self.engine.llm_engine.engine_core.shutdown.side_effect = RuntimeError('shutdown failed')
        with self.assertRaisesRegex(RuntimeError, 'shutdown failed'):
            self.run_reference()
        failure = json.loads((self.output / 'failure.json').read_bytes())
        self.assertEqual(failure['stage'], 'shutdown')
        self.engine.llm_engine.engine_core.shutdown.assert_called_once()
        self.assertFalse((self.output / 'candidate.json').exists())

    def test_success_is_only_unqualified_candidate_never_self_parity(self):
        self.run_reference()
        candidate = json.loads((self.output / 'candidate.json').read_bytes())
        self.assertIsNone(candidate['native_token_parity'])
        self.assertFalse(candidate['runtime_qualified'])
        self.assertFalse(candidate['physical_cleanup_verified'])

    def test_effective_seed_drift_stops_before_generation(self):
        self.engine.llm_engine.vllm_config.model_config.seed = 0
        with self.assertRaisesRegex(ValueError, 'effective'):
            self.run_reference()
        self.engine.generate.assert_not_called()
        self.assertTrue((self.output / 'effective-config.json').is_file())


if __name__ == '__main__':
    unittest.main()
