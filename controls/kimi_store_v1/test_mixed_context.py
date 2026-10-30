import contextlib
import inspect
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import torch

import mixed_device_init as init
import mixed_postprocess as post
import test_vllm_cpu_hook


class MixedContextTests(unittest.TestCase):
    def initializer(self, events, fail=False):
        path = (Path(__file__).parent / 'fixtures/vllm_loader_utils.py').resolve()
        code = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True)
        @contextlib.contextmanager
        def current(config, **kwargs):
            events.append(('enter_config', kwargs))
            try:
                yield
            finally:
                events.append('exit_config')
        ns = dict(inspect=inspect, configure_quant_config=lambda q, c: events.append('configure_quant'),
                  set_current_vllm_config=current,
                  record_metadata_for_reloading=lambda m: events.append('record_metadata'))
        original = types.FunctionType(test_vllm_cpu_hook.code_in(code, 'initialize_model'), ns)
        class Tiny(torch.nn.Module):
            def __init__(self, vllm_config, prefix=''):
                super().__init__()
                events.append(('construct', str(torch.empty(1).device), prefix))
                if fail:
                    raise RuntimeError('constructor failure')
        config = types.SimpleNamespace(model_config=types.SimpleNamespace(dtype=torch.bfloat16),
                                       quant_config=object())
        return original, Tiny, config

    def test_pinned_initializer_preserves_config_quant_reload_and_cpu_scope(self):
        events = []
        original, cls, config = self.initializer(events)
        with patch.object(init, '_require_mixed_constructor', return_value=torch.device('cuda:0')), \
                patch.object(init, 'audit_mixed_registered_state', return_value={'runtime_admitted': False}):
            with torch.device('meta'):
                model, receipt = init.initialize_mixed_vllm_rank(original, cls, config,
                    device='cuda:0', prefix='test')
                self.assertEqual(str(torch.empty(1).device), 'meta')
        self.assertIsInstance(model, cls)
        self.assertEqual(events, ['configure_quant', ('enter_config', {'check_compile': True, 'prefix': 'test'}),
                                 ('construct', 'cpu', 'test'), 'record_metadata', 'exit_config'])
        self.assertFalse(receipt['runtime_admitted'])

    def test_initializer_failure_restores_context_and_skips_reload_audit(self):
        events = []
        original, cls, config = self.initializer(events, fail=True)
        with patch.object(init, '_require_mixed_constructor', return_value=torch.device('cuda:0')), \
                patch.object(init, 'audit_mixed_registered_state') as audit:
            with torch.device('meta'):
                with self.assertRaisesRegex(RuntimeError, 'constructor failure'):
                    init.initialize_mixed_vllm_rank(original, cls, config, device='cuda:0')
                self.assertEqual(str(torch.empty(1).device), 'meta')
            audit.assert_not_called()
        self.assertEqual(events[-1], 'exit_config')
        self.assertNotIn('record_metadata', events)

    def post_fixture(self, events):
        base, ns = test_vllm_cpu_hook.LifecycleTests().base(events)
        model = ns['initialize_model']()
        events.clear()
        model._kimi_independent_records_bound = True
        config = types.SimpleNamespace(dtype=torch.bfloat16, quantization='compressed-tensors')
        return ns['process_weights_after_loading'], model, config

    def test_pinned_postprocess_cuda_target_quant_before_attention_once(self):
        events, targets = [], []
        function, model, config = self.post_fixture(events)
        model.packed.data[0] = -1
        # Explicit CPU-only movement boundary: real utility ordering, no CUDA kernel.
        original_context = function.__globals__['device_loading_context']
        @contextlib.contextmanager
        def movement(module, target):
            targets.append(str(target))
            yield module
        movement.__wrapped__ = original_context.__wrapped__
        with patch.dict(function.__globals__, device_loading_context=movement), \
                patch.object(post, '_require_rank_device') as rank_check:
            receipt = post.process_restored_mixed_rank(function, model, config, device='cuda:0')
        self.assertEqual(events, ['quant_postprocess', 'attention_postprocess'])
        self.assertEqual(targets, ['cuda:0', 'cuda:0'])
        self.assertEqual(rank_check.call_count, 2)
        self.assertFalse(receipt['runtime_admitted'])
        self.assertEqual(model._kimi_mixed_postprocess_status, 'returned')
        with self.assertRaisesRegex(ValueError, 'one-shot'):
            post.process_restored_mixed_rank(function, model, config, device='cuda:0')

    def test_failure_marks_one_shot_state_and_refuses_retry(self):
        events = []
        function, model, config = self.post_fixture(events)
        # The tiny quant-method control intentionally fails on the zeroed payload.
        original_context = function.__globals__['device_loading_context']
        @contextlib.contextmanager
        def movement(module, target):
            yield module
        movement.__wrapped__ = original_context.__wrapped__
        with patch.dict(function.__globals__, device_loading_context=movement), \
                patch.object(post, '_require_rank_device'):
            with self.assertRaises(AssertionError):
                post.process_restored_mixed_rank(function, model, config, device='cuda:0')
            self.assertEqual(model._kimi_mixed_postprocess_status, 'failed')
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                post.process_restored_mixed_rank(function, model, config, device='cuda:0')
        self.assertEqual(events, [])

    def test_actual_cpu_rank_rejected_before_postprocessing(self):
        events = []
        function, model, config = self.post_fixture(events)
        with self.assertRaisesRegex(ValueError, 'admitted CUDA'):
            post.process_restored_mixed_rank(function, model, config, device='cuda:0')
        self.assertEqual(events, [])

    def test_missing_install_or_uva_or_old_anchor_rejected(self):
        for kind in ('missing', 'uva', 'anchor'):
            events = []
            function, model, config = self.post_fixture(events)
            if kind == 'missing': del model._kimi_independent_records_bound
            elif kind == 'uva': model.packed._vllm_is_uva_offloaded = True
            else: model._kimi_native_allocation_tensors = ()
            with patch.object(post, '_require_rank_device'):
                with self.assertRaises(ValueError):
                    post.process_restored_mixed_rank(function, model, config, device='cuda:0')
            self.assertEqual(events, [])

    def test_unbound_initializer_rejected_before_model_construction(self):
        calls = []
        def unknown(**kwargs): calls.append(kwargs)
        with self.assertRaises(ValueError):
            init.initialize_mixed_vllm_rank(unknown, object, object(), device='cuda:0')
        self.assertEqual(calls, [])
