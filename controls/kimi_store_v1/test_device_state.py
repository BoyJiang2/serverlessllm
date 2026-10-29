import unittest
from unittest.mock import patch

import torch

from buffer_codec import encode_cpu_state
from device_state import encode_device_state, owned_device_layout
import marlin_aux
import rank_export
import test_buffer_codec
import test_cpu_tensor_io
import test_marlin_aux
import test_rank_export


class DeviceStateTests(unittest.TestCase):
    def test_cpu_descriptor_parity_raw_bits_and_zero_copy(self):
        model = test_buffer_codec.BufferCodecTests().model()
        before, layouts, encodings = encode_cpu_state(model, test_buffer_codec.CODECS)
        with patch.object(torch.Tensor, 'to', side_effect=AssertionError('unexpected transfer')):
            after, actual_layouts, actual_encodings = encode_device_state(
                model, device='cpu', buffer_codecs=test_buffer_codec.CODECS)
        self.assertEqual(layouts, actual_layouts)
        self.assertEqual(encodings, actual_encodings)
        for (name, expected), (actual_name, actual) in zip(before, after):
            self.assertEqual(name, actual_name)
            self.assertEqual(expected.data_ptr(), actual.data_ptr())
            self.assertTrue(torch.equal(expected, actual))

    def test_views_noncanonical_strides_wrong_device_or_unknown_dtype_refused(self):
        for tensor, device in ((torch.ones(4)[1:], 'cpu'),
                               (torch.empty_strided((1,), (2,)), 'cpu'),
                               (torch.ones(1), 'cuda:0'),
                               (torch.ones(1), 'cuda'),
                               (torch.ones(1, dtype=torch.float64), 'cpu')):
            with self.assertRaises(ValueError):
                owned_device_layout(tensor, device)

    def test_parameter_codec_or_missing_complex_codec_refused(self):
        model = test_buffer_codec.BufferCodecTests().model()
        for codecs in (None, {'packed': 'complex64-int32-bits-v1'}):
            with self.assertRaises(ValueError):
                encode_device_state(model, device='cpu', buffer_codecs=codecs)

    def test_cuda_auxiliary_entry_refuses_cpu_state_without_gpu_allocation(self):
        helper = test_marlin_aux.AuxiliaryTests()
        layer = helper.layer()
        before = dict(layer.named_parameters())
        context = dict(name='language_model.model.layers.1.mlp.experts', rank=0,
            profile=marlin_aux.PROFILE, config_raw=test_cpu_tensor_io.CONFIG,
            vllm_version='0.19.1', phase='target_initialized_before_restore')
        with patch.object(torch, 'empty', side_effect=AssertionError('unexpected allocation')):
            for device in ('cpu', 'cuda', 'cuda:0'):
                with self.assertRaises(ValueError):
                    marlin_aux._prepare_cuda_auxiliary(layer, device=device, **context)
        self.assertTrue(all(dict(layer.named_parameters())[n] is t for n, t in before.items()))

    def test_cuda_rank_entry_refuses_cpu_fixture_before_preparation(self):
        helper = test_rank_export.RankPreparationTests()
        model = helper.model()
        before = dict(model.named_parameters())
        with patch.object(rank_export, '_prepare_cuda_auxiliary', side_effect=AssertionError('unexpected preparation')):
            for device in ('cpu', 'cuda', 'cuda:0'):
                with self.assertRaises(ValueError):
                    rank_export.normalize_rank_cuda_auxiliary(model, device=device, **helper.context())
        self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))


if __name__ == '__main__':
    unittest.main()
