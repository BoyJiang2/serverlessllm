import builtins
import hashlib
import json
from pathlib import Path
import types
import unittest

import torch

from contract import validate_config


ROOT = Path(__file__).parent


class NativeMemoryTests(unittest.TestCase):
    def test_mla_A_source_constructor_explicitly_disables_tp_partition(self):
        source = ROOT / 'fixtures/vllm_deepseek.py'
        raw = source.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'c0f27b22e0023d42861ed4763e2a23fe305d049c6c45f6bd3ac05299d8e76da2')
        class Parent:
            def __init__(self, input_size, output_sizes, **kwargs):
                self.arguments = (input_size, output_sizes, kwargs)
        code = compile(raw, str(source.resolve()), 'exec', dont_inherit=True)
        body = next(c for c in code.co_consts if isinstance(c, types.CodeType)
                    and c.co_name == 'DeepSeekV2FusedQkvAProjLinear')
        namespace = dict(__builtins__=builtins.__dict__, __name__=__name__,
                         torch=torch, QuantizationConfig=object)
        cls = builtins.__build_class__(types.FunctionType(body, namespace),
                                       'DeepSeekV2FusedQkvAProjLinear', Parent)
        layer = cls(7168, [1536, 576])
        self.assertIs(layer.arguments[2]['disable_tp'], True)
        self.assertFalse(layer._use_min_latency_gemm)  # No weight/kernel fixture.

    def test_conditional_double_residency_floor_exceeds_historical_capacity(self):
        config = validate_config((ROOT.parents[1] / 'evidence/followup-kimi-k26/config.json').read_bytes())['text_config']
        report = json.loads((ROOT / 'native-memory-lower-bound.json').read_bytes())
        mla = config['num_hidden_layers'] * config['hidden_size'] * (
            config['q_lora_rank'] + config['kv_lora_rank'] + config['qk_rope_head_dim']) * 2
        floor = (report['payload_bytes'] - report['excluded_source_shape_bytes']) // 8 + mla * 7 // 8
        self.assertFalse(config['tie_word_embeddings'])
        self.assertEqual(mla, report['replicated_mla_A_source_bytes'])
        self.assertEqual(floor, report['rank_weight_lower_bound_bytes'])
        self.assertEqual(2 * floor, report['two_copy_lower_bound_bytes'])
        self.assertGreater(2 * floor, report['historical_card_bytes'])
        self.assertFalse(report['runtime_verified'])
        self.assertTrue(report['requires_fresh_device_budget'])


if __name__ == '__main__':
    unittest.main()
