import types
import unittest
from unittest.mock import patch

import torch

import mixed_device_init as mixed


class MixedInitializationTests(unittest.TestCase):
    def setUp(self):
        previous = torch.get_default_dtype()
        self.addCleanup(torch.set_default_dtype, previous)
        torch.set_default_dtype(torch.bfloat16)

    def test_reject_invalid_device_without_constructor(self):
        for device in ('cpu', 'cuda', 'cuda:8', 'meta'):
            with patch.object(mixed, '_verify_function') as verify:
                with self.assertRaises(ValueError):
                    mixed.initialize_mixed_rank(object, object(), device=device)
                verify.assert_not_called()

    def test_cpu_construction_context_and_restoration_on_failure(self):
        events = []
        class Tiny(torch.nn.Module):
            def __init__(self, **kwargs):
                super().__init__()
                events.append(str(torch.empty(1).device))
                raise RuntimeError('constructor failure')
        platform = types.SimpleNamespace(is_cuda=lambda: True, current_device=lambda: 'cuda:2')
        original = types.SimpleNamespace(__globals__={'current_platform': platform})
        config = types.SimpleNamespace(model_config=types.SimpleNamespace(dtype=torch.bfloat16))
        with patch.object(mixed, '_verify_function', return_value=original):
            with torch.device('meta'):
                with self.assertRaisesRegex(RuntimeError, 'constructor failure'):
                    mixed.initialize_mixed_rank(Tiny, config, device='cuda:2')
                self.assertEqual(str(torch.empty(1).device), 'meta')
        self.assertEqual(events, ['cpu'])
        torch.set_default_dtype(torch.float32)
        with patch.object(mixed, '_verify_function', return_value=original):
            with self.assertRaisesRegex(ValueError, 'active BF16'):
                mixed.initialize_mixed_rank(Tiny, config, device='cuda:2')
        self.assertEqual(events, ['cpu'])

    def test_platform_or_dtype_failure_precedes_construction(self):
        platform = types.SimpleNamespace(is_cuda=lambda: True, current_device=lambda: 'cuda:1')
        original = types.SimpleNamespace(__globals__={'current_platform': platform})
        config = types.SimpleNamespace(model_config=types.SimpleNamespace(dtype=torch.bfloat16))
        with patch.object(mixed, '_verify_function', return_value=original):
            with self.assertRaisesRegex(ValueError, 'current CUDA'):
                mixed.initialize_mixed_rank(object, config, device='cuda:0')
            config.model_config.dtype = torch.float32
            with self.assertRaisesRegex(ValueError, 'BF16'):
                mixed.initialize_mixed_rank(object, config, device='cuda:1')

    def test_real_cpu_state_cannot_pass_cuda_visual_audit(self):
        model = torch.nn.Module()
        for name in ('language_model', 'vision_tower', 'mm_projector'):
            model.add_module(name, torch.nn.Linear(1, 1))
        with self.assertRaisesRegex(ValueError, 'vision_tower'):
            mixed.audit_mixed_registered_state(model, 'cuda:0')
        model = torch.nn.Module()
        model.add_module('language_model', torch.nn.Linear(1, 1))
        with self.assertRaisesRegex(ValueError, 'all three'):
            mixed.audit_mixed_registered_state(model, 'cuda:0')
