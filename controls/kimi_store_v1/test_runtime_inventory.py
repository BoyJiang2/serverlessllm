from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch
import cpu_tensor_io
import runtime_inventory as inventory


class InventoryTests(unittest.TestCase):
    def test_parameters_buffers_and_direct_tensor_attrs_are_all_visible(self):
        model = torch.nn.Module()
        model.weight = torch.nn.Parameter(torch.ones(4))
        model.tied = model.weight
        model.register_buffer('time_weight', torch.ones(4), persistent=False)
        model.cache = torch.zeros(2)
        with patch.object(torch.Tensor, 'tolist', side_effect=AssertionError('payload access')):
            result = inventory.collect_inventory(model, {'weight', 'missing'})
        self.assertEqual(result['missing_checkpoint_targets'], ['missing'])
        self.assertEqual(result['tensors']['weight']['role'], 'checkpoint')
        self.assertEqual(result['tensors']['tied']['alias_of'], 'weight')
        self.assertFalse(result['tensors']['time_weight']['persistent'])
        self.assertEqual(result['tensors']['cache']['kind'], 'unregistered_tensor')
        self.assertFalse(result['payload_read'])
        with self.assertRaises(ValueError):
            inventory.require_safe_byte_export(result)

    def auxiliary_model(self):
        model = torch.nn.Module()
        node = model
        for name in ['language_model', 'model', 'layers', '1', 'mlp', 'experts']:
            child = torch.nn.Module()
            node.add_module(name, child)
            node = child
        node.register_parameter('w13_weight_g_idx', torch.nn.Parameter(torch.empty(2, 8, dtype=torch.int32), requires_grad=False))
        return model, node

    def test_suspect_uninitialized_aux_not_serialized(self):
        model, _ = self.auxiliary_model()
        result = inventory.collect_inventory(model)
        name = 'language_model.model.layers.1.mlp.experts.w13_weight_g_idx'
        self.assertEqual(result['tensors'][name]['role'], 'uninitialized_aux_candidate')
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'rank'
            with self.assertRaises(ValueError):
                cpu_tensor_io.write_cpu_state(target, model)
            self.assertFalse(target.exists())

    def test_phase_diff_records_recreated_empty_aux_without_value_claim(self):
        model, node = self.auxiliary_model()
        before = inventory.collect_inventory(model)
        node.w13_weight_g_idx = torch.nn.Parameter(torch.empty(2, 0, dtype=torch.int32), requires_grad=False)
        after = inventory.collect_inventory(model, phase='after_postprocess')
        changes = inventory.compare_inventory(before, after)
        self.assertEqual(len(changes['changed']), 1)
        self.assertFalse(changes['payload_verified'])
        inventory.require_safe_byte_export(after)
        with self.assertRaises(ValueError):
            inventory.compare_inventory(after, before)

    def test_unregistered_cache_export_requires_policy(self):
        model = torch.nn.Module()
        model.weight = torch.nn.Parameter(torch.ones(2))
        model.cached_tensor = torch.ones(2)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'rank'
            with self.assertRaises(ValueError):
                cpu_tensor_io.write_cpu_state(target, model)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
