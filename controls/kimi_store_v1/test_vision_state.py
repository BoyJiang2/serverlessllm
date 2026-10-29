import ast
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

import torch

from cpu_tensor_io import write_cpu_state
from runtime_inventory import collect_inventory

SOURCE = Path(__file__).parent / 'fixtures/vllm_vision.py'
SOURCE_SHA256 = '2d4ff4817698cda5a8e3c9845c6c5e3e97fee8a78c7f1b4a5b05fb8ddd8c5a37'


class VisionStateTests(unittest.TestCase):
    def source(self):
        raw = SOURCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), SOURCE_SHA256)
        return ast.parse(raw)

    def rope(self):
        node = next(n for n in self.source().body if isinstance(n, ast.ClassDef) and n.name == 'Rope2DPosEmbRepeated')
        namespace = dict(torch=torch, nn=torch.nn)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(SOURCE), 'exec'), namespace)
        # Exact source class on a tiny CPU grid, not the actual 512x512 model cache.
        return namespace['Rope2DPosEmbRepeated'](8, 2, 3)

    def test_lazy_nonpersistent_complex_cache_and_actual_cpu_values(self):
        rope = self.rope()
        self.assertNotIn('freqs_cis', dict(rope.named_buffers()))
        grid = torch.tensor([[1, 2, 3]])
        output = rope.get_freqs_cis(grid, torch.device('cpu'))
        self.assertEqual(output.dtype, torch.complex64)
        self.assertEqual(list(output.shape), [6, 4])
        self.assertIn('freqs_cis', rope._non_persistent_buffers_set)
        self.assertNotIn('freqs_cis', rope.state_dict())
        row = collect_inventory(rope)['tensors']['freqs_cis']
        self.assertEqual(row['dtype'], 'torch.complex64')
        self.assertFalse(row['persistent'])
        self.assertTrue(torch.equal(output, rope.freqs_cis.reshape(6, 4)))
        original = rope.freqs_cis
        rope.get_freqs_cis(grid, torch.device('cpu'))
        self.assertIs(rope.freqs_cis, original)

    def test_present_complex_cache_is_rejected_before_any_output(self):
        rope = self.rope()
        rope.get_freqs_cis(torch.tensor([[1, 1, 1]]), torch.device('cpu'))
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            with self.assertRaisesRegex(ValueError, 'dtype'):
                write_cpu_state(directory, rope)
            self.assertFalse(directory.exists())

    def test_source_time_buffer_nonpersistence_and_runtime_dtype_conversion(self):
        cls = next(n for n in self.source().body if isinstance(n, ast.ClassDef)
                   and n.name == 'Learnable2DInterpPosEmbDivided_fixed')
        call = next(n for n in ast.walk(cls) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == 'register_buffer')
        self.assertEqual(ast.literal_eval(call.args[0]), 'time_weight')
        self.assertIs(next(k.value.value for k in call.keywords if k.arg == 'persistent'), False)
        # No NumPy constructor emulation: verify only Torch buffer cast semantics.
        model = torch.nn.Module()
        model.register_buffer('time_weight', torch.arange(8, dtype=torch.float32).reshape(2, 1, 4), persistent=False)
        model.to(dtype=torch.bfloat16)
        self.assertEqual(model.time_weight.dtype, torch.bfloat16)
        self.assertNotIn('time_weight', model.state_dict())
        self.assertIn('time_weight', collect_inventory(model)['tensors'])

    def test_archived_native_dtype_table_has_no_complex_support(self):
        source = Path(__file__).resolve().parents[1] / 'candidate13e/evidence/runtime/source-v7/sllm_store/csrc/checkpoint/checkpoint.cpp'
        raw = source.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
                         'eb731fab981dae01d752e0cd3a4539cbffeae415d50eabb030d0acb9f3de8099')
        table = set(re.findall(r'\{"(torch\.[^"]+)"', raw.decode('utf-8')))
        self.assertIn('torch.bfloat16', table)
        self.assertNotIn('torch.complex64', table)


if __name__ == '__main__':
    unittest.main()
