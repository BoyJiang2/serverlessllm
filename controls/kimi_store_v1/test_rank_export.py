import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import weakref

import torch

import rank_export
from marlin_aux import NAMES, PROFILE
import test_marlin_aux
import test_cpu_tensor_io

CONTRACT = (Path(__file__).parent / 'checkpoint-targets.json').read_bytes()


class RankPreparationTests(unittest.TestCase):
    def model(self):
        # Tiny control-flow fixture, not a Kimi model or representative TP weights.
        model = torch.nn.Module()
        model.checkpoint = torch.nn.Parameter(torch.ones(1), requires_grad=False)
        model.language_model = torch.nn.Module()
        model.language_model.model = torch.nn.Module()
        model.language_model.model.layers = torch.nn.ModuleDict()
        for i in range(1, 61):
            layer = torch.nn.Module()
            layer.mlp = torch.nn.Module()
            layer.mlp.experts = torch.nn.Module()
            for leaf in NAMES:
                layer.mlp.experts.register_parameter(leaf, torch.nn.Parameter(torch.ones(1, dtype=torch.int32), requires_grad=False))
            model.language_model.model.layers[str(i)] = layer
        return model

    def context(self):
        return dict(checkpoint_contract=CONTRACT, rank=0, profile=PROFILE,
                    config_raw=test_cpu_tensor_io.CONFIG, vllm_version='0.19.1',
                    phase='checkpoint_loaded_before_postprocess')

    def plans(self):
        # Full shapes and method provenance are tested with the real CPU helper
        # elsewhere; here mock that boundary to test sixty-layer ordering only.
        layer = test_marlin_aux.AuxiliaryTests().layer()
        receipt = test_marlin_aux.AuxiliaryTests().normalize(layer)
        def prepare(module, name, **context):
            result = copy.deepcopy(receipt)
            result['layer'] = name
            replacements = {leaf: torch.nn.Parameter(torch.empty(384, 0, dtype=torch.int32), requires_grad=False)
                            for leaf in NAMES}
            return replacements, result
        return prepare

    def test_all_sixty_preflight_before_first_mutation(self):
        model = self.model()
        before = dict(model.named_parameters())
        prepared = []
        plan = self.plans()
        def checked(module, name, **context):
            self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))
            prepared.append(name)
            return plan(module, name, **context)
        with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}), \
                patch.object(rank_export, '_prepare_cpu_auxiliary', side_effect=checked):
            receipts = rank_export.normalize_rank_cpu_auxiliary(model, **self.context())
        self.assertEqual(len(receipts), 60)
        self.assertEqual(prepared[0], 'language_model.model.layers.1.mlp.experts')
        self.assertEqual(prepared[-1], 'language_model.model.layers.60.mlp.experts')
        self.assertTrue(all(t.numel() == 0 for n, t in model.named_parameters() if n != 'checkpoint'))

    def test_late_preflight_failure_leaves_all_bindings_unchanged(self):
        model = self.model()
        before = dict(model.named_parameters())
        plan = self.plans()
        def late(module, name, **context):
            if name == 'language_model.model.layers.60.mlp.experts':
                raise ValueError('late source mismatch')
            return plan(module, name, **context)
        with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}), \
                patch.object(rank_export, '_prepare_cpu_auxiliary', side_effect=late):
            with self.assertRaisesRegex(ValueError, 'late source'):
                rank_export.normalize_rank_cpu_auxiliary(model, **self.context())
        self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))

    def test_real_contract_refuses_fixture_and_missing_or_tied_modules(self):
        for kind in ('contract', 'missing', 'tied', 'storage'):
            model = self.model()
            if kind == 'missing':
                del model.language_model.model.layers['60']
            if kind == 'tied':
                model.language_model.model.layers['60'] = model.language_model.model.layers['59']
            if kind == 'storage':
                model.checkpoint = model.language_model.model.layers['1'].mlp.experts.w13_weight_g_idx
            with patch.object(rank_export, '_prepare_cpu_auxiliary', side_effect=AssertionError('unexpected preparation')):
                if kind == 'contract':
                    with self.assertRaisesRegex(ValueError, 'incomplete checkpoint'):
                        rank_export.normalize_rank_cpu_auxiliary(model, **self.context())
                else:
                    with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}):
                        with self.assertRaises(ValueError):
                            rank_export.normalize_rank_cpu_auxiliary(model, **self.context())

    def test_bad_export_budget_rejected_before_normalization(self):
        model = self.model()
        with patch.object(rank_export, 'normalize_rank_cpu_auxiliary', side_effect=AssertionError('unexpected mutation')):
            with self.assertRaises(ValueError):
                rank_export.export_rank_cpu('unused', model, **self.context(),
                                            torch_version='2.10.0', chunk_bytes=0)

    def test_export_does_not_retain_pre_normalization_tensor_list(self):
        model = torch.nn.Module()
        model.weight = torch.nn.Parameter(torch.ones(1), requires_grad=False)
        original = weakref.ref(model.weight)
        def normalize(*args, **kwargs):
            model.weight = torch.nn.Parameter(torch.zeros(1), requires_grad=False)
            return []
        def writer(*args, **kwargs):
            self.assertIsNone(original())
            raise ValueError('stop before fixture I/O')
        with patch.object(rank_export, 'normalize_rank_cpu_auxiliary', side_effect=normalize), \
                patch.object(rank_export, 'write_cpu_state', side_effect=writer):
            with self.assertRaisesRegex(ValueError, 'stop before fixture I/O'):
                rank_export.export_rank_cpu('unused', model, **self.context(), torch_version='2.10.0')

    def test_noncanonical_singleton_stride_rejected_before_preparation(self):
        model = self.model()
        tensor = torch.empty_strided((1,), (2,), dtype=torch.float32)
        self.assertTrue(tensor.is_contiguous())
        self.assertEqual(tensor.untyped_storage().nbytes(), 4)
        model.checkpoint = torch.nn.Parameter(tensor, requires_grad=False)
        before = dict(model.named_parameters())
        with patch.object(rank_export, 'load_targets', return_value={'checkpoint'}), \
                patch.object(rank_export, '_prepare_cpu_auxiliary', side_effect=AssertionError('unexpected preparation')):
            with self.assertRaisesRegex(ValueError, 'noncanonical stride'):
                rank_export.normalize_rank_cpu_auxiliary(model, **self.context())
        self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))

    def test_target_phase_export_refused_before_preparation_or_binding_changes(self):
        model = self.model()
        before = dict(model.named_parameters())
        context = self.context()
        context['phase'] = 'target_initialized_before_restore'
        with patch.object(rank_export, '_prepare_cpu_auxiliary', side_effect=AssertionError('unexpected preparation')):
            with self.assertRaisesRegex(ValueError, 'source-after-load'):
                rank_export.export_rank_cpu('unused', model, **context, torch_version='2.10.0')
        self.assertTrue(all(dict(model.named_parameters())[n] is t for n, t in before.items()))


if __name__ == '__main__':
    unittest.main()
