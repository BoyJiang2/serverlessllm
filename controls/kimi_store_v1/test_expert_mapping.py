import hashlib
import importlib.util
from pathlib import Path
import types
import unittest

import torch
import expert_mapping


class MappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).parent / 'fixtures/vllm_expert_loader.py'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == 'a9b8f29f9e0f67309e409c6ab5c44e1186c8ca75c8ce1379f060a23b0f61410b'
        spec = importlib.util.spec_from_file_location('pinned_expert_loader', path)
        cls.fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.fixture)

    def assertBitsEqual(self, actual, expected):
        self.assertEqual(actual.shape, expected.shape)
        self.assertEqual(actual.dtype, expected.dtype)
        self.assertTrue(torch.equal(actual.contiguous().reshape(-1).view(torch.uint8),
                                    expected.contiguous().reshape(-1).view(torch.uint8)))

    def loader(self, rank):
        obj = self.fixture.ExtractedFusedMoE()
        obj.quant_config = None
        obj.quant_method = type('CompressedTensorsWNA16MarlinMoEMethod', (), {})()
        obj.tp_rank, obj.tp_size = rank, 8
        obj.moe_config = types.SimpleNamespace(is_act_and_mul=True)
        obj._map_global_expert_id_to_local_expert_id = lambda expert: expert
        return obj

    def test_actual_pinned_loader_matches_all_rank_gate_up_down_values(self):
        # Tiny CPU loader-method fixtures; no vLLM engine, kernel, model or CUDA.
        for backend in ('Marlin', 'Flashinfer'):
            for kind in ('packed', 'scale'):
                dtype = torch.int32 if kind == 'packed' else torch.bfloat16
                factor = 8 if kind == 'packed' else 32
                shape_up, shape_down = (256, 64 // factor), (64, 256 // factor)
                gate = torch.arange(shape_up[0] * shape_up[1], dtype=torch.int32).reshape(shape_up).to(dtype)
                up = (gate.to(torch.int32) + 4096).to(dtype)
                down = torch.arange(shape_down[0] * shape_down[1], dtype=torch.int32).reshape(shape_down).to(dtype)
                if kind == 'scale':
                    gate.view(torch.int16)[0, 0] = 0x7fc1  # Preserve BF16 NaN payload bits.
                    down.view(torch.int16)[0, 0] = -32768  # Negative zero.
                else:
                    gate[0, 0], down[0, 0] = -1, -2147483648
                recovered = {n: [] for n in ('gate', 'up', 'down')}
                for rank in range(8):
                    loader = self.loader(rank)
                    gate_view = expert_mapping.checkpoint_shard_view(gate, 'gate', kind, rank, backend)
                    down_view = expert_mapping.checkpoint_shard_view(down, 'down', kind, rank, backend)
                    dim = 1 if backend == 'Marlin' else 0
                    merged_shape = list(gate_view.shape)
                    merged_shape[dim] *= 2
                    w13 = torch.nn.Parameter(torch.full([3] + merged_shape, -7, dtype=dtype), requires_grad=False)
                    w2 = torch.nn.Parameter(torch.full([3] + list(down_view.shape), -7, dtype=dtype), requires_grad=False)
                    for param in (w13, w2):
                        param.is_transposed = backend == 'Marlin'
                        param.quant_method = 'group'
                    for projection, source in [('gate', gate), ('up', up), ('down', down)]:
                        expected = expert_mapping.checkpoint_shard_view(source, projection, kind, rank, backend)
                        param, shard = (w2, 'w2') if projection == 'down' else (w13, expert_mapping.PROJECTIONS[projection][1])
                        target_name = ('w2' if projection == 'down' else 'w13') + '_weight_' + kind
                        success = loader.weight_loader(param, source, target_name, shard, 1, return_success=True)
                        self.assertTrue(success)
                        actual = param[1] if projection == 'down' else param[1].narrow(dim,
                            0 if projection == 'gate' else gate_view.shape[dim], gate_view.shape[dim])
                        self.assertBitsEqual(actual, expected)
                        recovered[projection].append(actual.t().contiguous() if backend == 'Marlin' else actual.clone())
                    self.assertTrue(torch.all(w13[0] == -7))
                    self.assertTrue(torch.all(w2[2] == -7))
                for projection, source in [('gate', gate), ('up', up), ('down', down)]:
                    self.assertBitsEqual(torch.cat(recovered[projection], dim=1 if projection == 'down' else 0), source)

    def test_weight_shape_is_replicated_and_native_dtype_can_differ(self):
        source = torch.tensor([2048, 7168], dtype=torch.int32)
        for rank in range(8):
            expected = expert_mapping.checkpoint_shard_view(source, 'gate', 'shape', rank, 'Marlin')
            param = torch.nn.Parameter(torch.zeros(3, 2, dtype=torch.float32), requires_grad=False)
            param.is_transposed = True
            self.assertTrue(self.loader(rank).weight_loader(param, source, 'w13_weight_shape', 'w1', 1, True))
            self.assertTrue(torch.equal(param[1].to(torch.int32), expected))
            self.assertEqual(source.dtype, torch.int32)

    def test_frozen_names_and_out_of_scope_inputs(self):
        result = expert_mapping.parse_expert_key('language_model.model.layers.60.mlp.experts.383.up_proj.weight_scale')
        self.assertEqual(result['shard'], 'w3')
        self.assertTrue(result['target'].endswith('experts.w13_weight_scale'))
        for name in ['language_model.model.layers.0.mlp.experts.0.gate_proj.weight_packed',
                     'language_model.model.layers.61.mlp.experts.0.gate_proj.weight_packed',
                     'language_model.model.layers.1.mlp.experts.384.gate_proj.weight_packed', 'random']:
            with self.assertRaises(ValueError):
                expert_mapping.parse_expert_key(name)
        for rank in [-1, 8, True]:
            with self.assertRaises(ValueError):
                expert_mapping.checkpoint_shard_view(torch.zeros(8, 8, dtype=torch.int32), 'gate', 'packed', rank, 'Marlin')


if __name__ == '__main__':
    unittest.main()
