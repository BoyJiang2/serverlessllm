import tempfile
import unittest
from pathlib import Path

import torch

from cpu_tensor_io import write_cpu_state
from state_audit import compare_restored_state, fingerprint_state
import test_cpu_tensor_io


class StateAuditTests(unittest.TestCase):
    def snapshot(self, model, **kwargs):
        return fingerprint_state(model, rank=kwargs.pop('rank', 0),
                                 phase=kwargs.pop('phase', 'before_postprocess'), **kwargs)

    def test_real_raw_file_restore_all_eight_ranks(self):
        for rank in range(8):
            source, restored = test_cpu_tensor_io.TensorIOTests().model(), test_cpu_tensor_io.TensorIOTests().model()
            reference = self.snapshot(source, rank=rank, chunk_bytes=7)
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                records, _, _ = write_cpu_state(directory, source, chunk_bytes=7)
                named = list(restored.named_parameters(remove_duplicate=False))
                named.extend(restored.named_buffers(remove_duplicate=False))
                for name, tensor in named:
                    tensor.data.zero_()
                    row = records[name]
                    with (directory / ('tensor.data_' + str(row['partition']))).open('rb') as stream:
                        stream.seek(row['local_offset'])
                        raw = bytearray(stream.read(row['bytes']))
                    if raw:
                        tensor.detach().view(torch.uint8).reshape(-1).copy_(torch.frombuffer(raw, dtype=torch.uint8))
                result = compare_restored_state(reference, self.snapshot(restored, rank=rank, chunk_bytes=4))
                self.assertTrue(result['same_phase_cpu_bytes_equal'])
                self.assertFalse(result['native_loader_verified'])

    def test_same_shape_packed_scale_and_buffer_corruption(self):
        for name in ('packed', 'scale', 'weight_shape', 'router_bias'):
            model = test_cpu_tensor_io.TensorIOTests().model()
            reference = self.snapshot(model)
            tensor = getattr(model, name)
            tensor.detach().view(torch.uint8).reshape(-1)[0] ^= 1
            result = compare_restored_state(reference, self.snapshot(model))
            self.assertIn(name, result['changed'])
            self.assertFalse(result['same_phase_cpu_bytes_equal'])

    def test_rank_phase_version_and_coverage_mismatch_rejected(self):
        reference = self.snapshot(test_cpu_tensor_io.TensorIOTests().model())
        for field, value in [('rank', 1), ('phase', 'after_postprocess'),
                             ('source_torch_version', 'other'), ('checkpoint_name_coverage_verified', True)]:
            changed = dict(reference, **{field: value})
            with self.assertRaises(ValueError):
                compare_restored_state(reference, changed)
        for rank in (-1, 8, True):
            with self.assertRaises(ValueError):
                self.snapshot(test_cpu_tensor_io.TensorIOTests().model(), rank=rank)

    def test_buffers_and_alias_changes_are_not_ignored(self):
        model = test_cpu_tensor_io.TensorIOTests().model()
        reference = self.snapshot(model)
        model.packed_alias = torch.nn.Parameter(model.packed.detach().clone(), requires_grad=False)
        self.assertFalse(compare_restored_state(reference, self.snapshot(model))['inventory_equal'])
        del model.empty_scratch
        result = compare_restored_state(reference, self.snapshot(model))
        self.assertEqual(result['removed'], ['empty_scratch'])

    def test_empty_and_invalid_chunk_rejected(self):
        with self.assertRaises(ValueError):
            self.snapshot(torch.nn.Module())
        for chunk in (0, True, 8 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError):
                self.snapshot(test_cpu_tensor_io.TensorIOTests().model(), chunk_bytes=chunk)


if __name__ == '__main__':
    unittest.main()
