import unittest
from unittest.mock import patch

import torch

from device_state import encode_device_state
import mixed_device_init
import rank_export
import test_rank_export


class MixedAuxiliaryTests(unittest.TestCase):
    def model_and_map(self):
        helper = test_rank_export.RankPreparationTests()
        model = helper.model()
        devices = {n: 'cpu' for n, _ in model.named_parameters(remove_duplicate=False)}
        return helper, model, devices

    def test_explicit_device_preflight_preserves_all_sixty_layer_ordering(self):
        helper, model, devices = self.model_and_map()
        before = dict(model.named_parameters())
        calls = []
        prepare = helper.plans()
        def checked(module, name, **context):
            self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))
            calls.append(name)
            return prepare(module, name, **context)
        # All devices really are CPU. Only the method/target-contract boundaries
        # are tiny stubs, as in the existing sixty-layer control-flow tests.
        context = helper.context()
        checkpoint_contract = context.pop('checkpoint_contract')
        with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}):
            receipts = rank_export._normalize_rank_auxiliary(model,
                checkpoint_contract=checkpoint_contract, buffer_codecs=None, device='cpu',
                device_map=devices, describe=lambda m, c: encode_device_state(m, device='cpu',
                    device_map=devices, buffer_codecs=c), prepare=checked, context=context)
        self.assertEqual(len(receipts), 60)
        self.assertEqual(calls[0], 'language_model.model.layers.1.mlp.experts')
        self.assertEqual(calls[-1], 'language_model.model.layers.60.mlp.experts')
        self.assertTrue(all(t.numel() == 0 for n, t in model.named_parameters() if n != 'checkpoint'))

    def test_late_failure_does_not_mutate_any_layer_with_device_map(self):
        helper, model, devices = self.model_and_map()
        before = dict(model.named_parameters())
        prepare = helper.plans()
        def late(module, name, **context):
            if name == 'language_model.model.layers.60.mlp.experts':
                raise ValueError('late source failure')
            return prepare(module, name, **context)
        context = helper.context()
        checkpoint_contract = context.pop('checkpoint_contract')
        with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}):
            with self.assertRaisesRegex(ValueError, 'late source failure'):
                rank_export._normalize_rank_auxiliary(model,
                    checkpoint_contract=checkpoint_contract, buffer_codecs=None, device='cpu',
                    device_map=devices, describe=lambda m, c: encode_device_state(m, device='cpu',
                        device_map=devices, buffer_codecs=c), prepare=late, context=context)
        self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))

    def test_real_cpu_visual_branches_refused_before_auxiliary_preparation(self):
        model = torch.nn.Module()
        for root in ('language_model', 'vision_tower', 'mm_projector'):
            model.add_module(root, torch.nn.Linear(1, 1))
        with patch.object(rank_export, '_normalize_rank_auxiliary') as normalize:
            with self.assertRaisesRegex(ValueError, 'vision_tower'):
                rank_export.normalize_rank_mixed_auxiliary(model, device='cuda:0',
                    checkpoint_contract=b'fixture', phase='target_initialized_before_restore')
            normalize.assert_not_called()
        with patch.object(mixed_device_init, 'mixed_state_devices') as devices:
            with self.assertRaisesRegex(ValueError, 'target-before-restore'):
                rank_export.normalize_rank_mixed_auxiliary(model, device='cuda:0',
                    checkpoint_contract=b'fixture', phase='checkpoint_loaded_before_postprocess')
            devices.assert_not_called()
        with self.assertRaises(ValueError):
            mixed_device_init.mixed_state_devices(model, 'cpu')
