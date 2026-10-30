import hashlib
import importlib.util
from pathlib import Path
import types
import unittest

import torch
import nonexpert_mapping as mapping


class NonexpertTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / 'fixtures/vllm_linear_loaders.py'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == 'c4abfe7452ee637e97333df2e41feb3e0ff48edd1a7f3efd9d8a4fe1d211942c'
        spec = importlib.util.spec_from_file_location('native_linear_methods', path)
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)

    def assertBits(self, actual, expected):
        self.assertEqual(actual.shape, expected.shape)
        self.assertTrue(torch.equal(actual.contiguous().reshape(-1).view(torch.uint8),
                                    expected.contiguous().reshape(-1).view(torch.uint8)))

    def source(self, shape):
        tensor = torch.arange(shape[0] * shape[1], dtype=torch.float32).reshape(shape).to(torch.bfloat16)
        tensor.view(torch.int16)[0, 0] = 0x7fc1
        return tensor

    def test_column_and_row_native_loaders_all_ranks(self):
        for name, cls, axis in [
                ('language_model.model.layers.1.self_attn.q_b_proj.weight', self.fixture.ColumnParallelLinear, 0),
                ('language_model.model.layers.1.self_attn.o_proj.weight', self.fixture.RowParallelLinear, 1)]:
            source, parts = self.source([64, 64]), []
            for rank in range(8):
                expected = mapping.checkpoint_shard_view(source, mapping.describe(name), rank)
                param = torch.nn.Parameter(torch.zeros_like(expected), requires_grad=False)
                setattr(param, 'output_dim' if axis == 0 else 'input_dim', axis)
                obj = cls()
                obj.tp_rank, obj.tp_size = rank, 8
                obj.weight_loader(param, source)
                self.assertBits(param, expected)
                parts.append(param.detach())
            self.assertBits(torch.cat(parts, dim=axis), source)

    def test_gate_up_native_merge_and_replicated_mla_a(self):
        for replicate in (False, True):
            gate, up = self.source([64, 16]), self.source([64, 16])
            up[1, 1] = -8
            for rank in range(8):
                if replicate:
                    names = ['language_model.model.layers.1.self_attn.q_a_proj.weight',
                             'language_model.model.layers.1.self_attn.kv_a_proj_with_mqa.weight']
                else:
                    names = ['language_model.model.layers.1.mlp.shared_experts.gate_proj.weight',
                             'language_model.model.layers.1.mlp.shared_experts.up_proj.weight']
                a, b = [mapping.checkpoint_shard_view(t, mapping.describe(n), rank)
                        for t, n in zip([gate, up], names)]
                param = torch.nn.Parameter(torch.zeros(a.shape[0] + b.shape[0], 16, dtype=torch.bfloat16), requires_grad=False)
                param.output_dim = 0
                obj = self.fixture.MergedColumnParallelLinear()
                obj.tp_rank, obj.tp_size = (0, 1) if replicate else (rank, 8)
                obj.output_sizes = [64, 64]
                for slot, source in enumerate((gate, up)):
                    obj.weight_loader(param, source, slot)
                self.assertBits(param, torch.cat([a, b], dim=0))

    def test_vocab_native_padding_is_zero_and_shard_values_match(self):
        source = self.source([64, 16])
        name = 'language_model.model.embed_tokens.weight'
        for rank in range(8):
            expected = mapping.checkpoint_shard_view(source, mapping.describe(name), rank)
            obj = self.fixture.VocabParallelEmbedding()
            obj.org_vocab_size, obj.num_embeddings_per_partition = 64, 12
            obj.shard_indices = types.SimpleNamespace(org_vocab_start_index=rank * 8, org_vocab_end_index=(rank + 1) * 8)
            param = torch.nn.Parameter(torch.full([12, 16], -9, dtype=torch.bfloat16), requires_grad=False)
            param.output_dim = 0
            obj.weight_loader(param, source)
            self.assertBits(param[:8], expected)
            self.assertTrue(torch.all(param[8:] == 0))

    def test_conditional_names_and_unknown_profile_rejection(self):
        self.assertEqual(mapping.describe('mm_projector.proj.2.weight')['target'], 'mm_projector.linear_2.weight')
        self.assertEqual(mapping.describe('vision_tower.encoder.blocks.26.wqkv.bias')['operation'], 'replicate')
        for name in ['unknown', 'language_model.model.layers.0.mlp.shared_experts.gate_proj.weight',
                     'vision_tower.encoder.blocks.27.wqkv.weight']:
            with self.assertRaises(ValueError):
                mapping.describe(name)
        for kwargs in [dict(encoder_tp_mode='weights'), dict(sequence_parallel_moe=True), dict(use_mla=False)]:
            with self.assertRaises(ValueError):
                mapping.describe('language_model.model.norm.weight', **kwargs)


if __name__ == '__main__':
    unittest.main()
