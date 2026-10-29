"""Run the pinned pure shape method; no torch/vLLM runtime or tensor payload."""
import importlib.util
import math
from pathlib import Path
import types
import unittest


class ShapeTests(unittest.TestCase):
    def test_tp8_fused_shapes_for_both_candidate_backends(self):
        path = Path(__file__).parent / 'fixtures/vllm_weight_shape.py'
        spec = importlib.util.spec_from_file_location('shape_fixture', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        expected = {
            'Marlin': [(384, 896, 512), (384, 32, 7168), (384, 224, 512), (384, 8, 7168)],
            'Flashinfer': [(384, 512, 896), (384, 7168, 32), (384, 512, 224), (384, 7168, 8)]}
        for backend, shapes in expected.items():
            state = types.SimpleNamespace(moe=types.SimpleNamespace(is_act_and_mul=True),
                                          packed_factor=8, kernel_backend=backend)
            observed = [module.get_weight_shape(state, name, 384, 7168, 256,
                        num_groups_w13=224, num_groups_w2=8)
                        for name in ['w13_weight', 'w2_weight', 'w13_scale', 'w2_scale']]
            self.assertEqual(observed, shapes)
            # Pure TP without expert parallelism: sum over 8 ranks and 60 MoE layers.
            self.assertEqual(sum(math.prod(x) * 4 for x in observed[:2]) * 8 * 60, 507343011840)
            self.assertEqual(sum(math.prod(x) * 2 for x in observed[2:]) * 8 * 60, 63417876480)


if __name__ == '__main__':
    unittest.main()
