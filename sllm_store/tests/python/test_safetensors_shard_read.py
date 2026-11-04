import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('shard_read', pathlib.Path(__file__).parents[1] / 'safetensors_shard_read.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HeaderTest(unittest.TestCase):
    def test_valid_mixed_raw_layout(self):
        rows = module.validate_header({'packed': {'dtype': 'I32', 'shape': [2], 'data_offsets': [0, 8]}, 'scale': {'dtype': 'BF16', 'shape': [2], 'data_offsets': [8, 12]}}, 12)
        self.assertEqual([row[2] for row in rows], ['packed', 'scale'])

    def test_bad_shape_span_rejected(self):
        with self.assertRaises(AssertionError):
            module.validate_header({'x': {'dtype': 'I32', 'shape': [2], 'data_offsets': [0, 4]}}, 4)

    def test_overlap_gap_and_unknown_dtype_rejected(self):
        for row in [{'dtype': 'BF16', 'shape': [2], 'data_offsets': [2, 6]}, {'dtype': 'F32', 'shape': [1], 'data_offsets': [0, 4]}]:
            with self.assertRaises(AssertionError):
                module.validate_header({'x': row}, 6)
