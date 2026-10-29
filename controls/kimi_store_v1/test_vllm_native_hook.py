from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

import native_call
import vllm_native_hook as hook
from marlin_aux import PROFILE
import test_cpu_tensor_io
import test_native_call
import test_vllm_cpu_hook


class NativeLifecycleTests(unittest.TestCase):
    def setup_fixture(self, root, events, cap=1024, device_budget=1024):
        helper = test_vllm_cpu_hook.LifecycleTests()
        base, namespace = helper.base(events)
        namespace['current_platform'].is_cuda = lambda: True
        namespace['current_platform'].is_cpu = lambda: False
        # Only Base framework device/peak APIs are stubbed. Never initialize CUDA.
        namespace['torch'] = types.SimpleNamespace(device=lambda value: torch.device('cpu'),
            accelerator=types.SimpleNamespace(max_memory_allocated=lambda: 0))
        namespace['logger'].debug_once = lambda *args, **kwargs: None
        namespace['format_gib'] = lambda value: str(value)
        runtime, digest, _ = test_native_call.NativeCallTests().runtime(Path(root) / 'rank0', [])
        options = dict(storage_path=root, model_path='rank0', expected_sidecar_sha256=digest,
                       rank=0, device_id=0, max_allocation_bytes=cap, timeout_seconds=30)
        with patch.object(hook, 'load_targets', return_value={'packed'}):
            cls = hook.make_native_store_loader(base, runtime, call_options=options,
                config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture-boundary',
                profile=PROFILE, vllm_version='0.19.1', device_memory_budget_bytes=device_budget)
        config, model_config = helper.configs()
        config.load_config.device = 'cuda:0'
        config.parallel_config = types.SimpleNamespace(tensor_parallel_size=8, pipeline_parallel_size=1,
            enable_expert_parallel=False, use_sequence_parallel_moe=False,
            data_parallel_size=1, prefill_context_parallel_size=1)
        config.speculative_config = None
        model_config.dtype = torch.bfloat16
        model_config.use_mla = True
        model_config.multimodal_config = types.SimpleNamespace(mm_encoder_tp_mode='data')
        return cls(config.load_config), config, model_config, namespace

    def test_pinned_base_orders_native_install_before_one_postprocess(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, _ = self.setup_fixture(temp, events)
            def load(*args, **kwargs):
                events.append('native_load')
                return {}, {'allocation': {}}
            def install(directory, model, state, digest, **kwargs):
                events.append('install')
                model.packed.data[0] = -1
                return {'native_loader_verified': False}
            # Rank preparation, native runtime and installation are explicit mocks;
            # actual pinned Base/utility sequencing executes on the tiny CPU model.
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'normalize_rank_cuda_auxiliary', side_effect=lambda *a, **k: events.append('prepare') or []), \
                    patch.object(hook, 'load_native_return', side_effect=load), \
                    patch.object(hook, 'install_native_state', side_effect=install):
                loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize', 'prepare', 'native_load', 'install',
                                      'quant_postprocess', 'attention_postprocess'])
            self.assertEqual(loader.lifecycle_status, 'returned')
            self.assertEqual(loader.weights_status, 'installed')
            self.assertTrue(loader.discard_worker_required)
            self.assertIsNone(loader.native_state)
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model(config, model_config)

    def test_wrong_profile_refused_before_initialization(self):
        for kind in ('tp', 'pp', 'dp', 'pcp', 'ep', 'sp', 'mla', 'encoder', 'mtp', 'dtype', 'platform'):
            events = []
            with tempfile.TemporaryDirectory() as temp:
                loader, config, model_config, namespace = self.setup_fixture(temp, events)
                if kind == 'tp': config.parallel_config.tensor_parallel_size = 4
                elif kind == 'pp': config.parallel_config.pipeline_parallel_size = 2
                elif kind == 'dp': config.parallel_config.data_parallel_size = 2
                elif kind == 'pcp': config.parallel_config.prefill_context_parallel_size = 2
                elif kind == 'ep': config.parallel_config.enable_expert_parallel = True
                elif kind == 'sp': config.parallel_config.use_sequence_parallel_moe = True
                elif kind == 'mla': model_config.use_mla = False
                elif kind == 'encoder': model_config.multimodal_config.mm_encoder_tp_mode = 'weights'
                elif kind == 'mtp': config.speculative_config = object()
                elif kind == 'dtype': model_config.dtype = torch.float32
                else: namespace['current_platform'].is_cuda = lambda: False
                with self.assertRaisesRegex(ValueError, 'before initialization'):
                    loader.load_model(config, model_config)
                self.assertEqual(loader.lifecycle_status, 'failed')
                self.assertFalse(events)

    def test_real_cpu_rank_refused_without_native_calls_or_postprocessing(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, _ = self.setup_fixture(temp, events)
            with patch.object(hook, 'load_native_return', side_effect=AssertionError('unexpected native call')):
                with self.assertRaisesRegex(ValueError, 'CUDA device'):
                    loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize'])
            self.assertEqual(loader.weights_status, 'failed')
            self.assertEqual(loader.lifecycle_status, 'failed')

    def test_native_failure_receipt_is_retained_and_no_postprocess_occurs(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, _ = self.setup_fixture(temp, events)
            receipt = {'stage': 'confirming', 'worker_cleanup_required': True, 'store_cleanup_required': True}
            failed_state = {'owner': object()}
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'normalize_rank_cuda_auxiliary', return_value=[]), \
                    patch.object(hook, 'load_native_return', side_effect=native_call.NativeCallFailure(receipt, failed_state)):
                with self.assertRaises(native_call.NativeCallFailure):
                    loader.load_model(config, model_config)
            self.assertIs(loader.native_call_receipt, receipt)
            self.assertIs(loader.native_state, failed_state)
            self.assertEqual(events, ['initialize'])
            self.assertEqual(loader.lifecycle_status, 'failed')

    def test_source_allocation_budget_refused_before_initializer(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'budget before initialization'):
                self.setup_fixture(temp, events, cap=1)
        self.assertFalse(events)

    def test_double_residency_floor_refused_before_initializer(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(MemoryError, 'two-copy checkpoint floor'):
                self.setup_fixture(temp, events, device_budget=1)
        self.assertFalse(events)

    def test_postprocess_failure_consumes_lifecycle_and_requires_worker_disposal(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, namespace = self.setup_fixture(temp, events)
            utility = namespace['process_weights_after_loading']
            method_class = utility.__globals__['QuantizeMethodBase']
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'normalize_rank_cuda_auxiliary', return_value=[]), \
                    patch.object(hook, 'load_native_return', return_value=({}, {'allocation': {}})), \
                    patch.object(hook, 'install_native_state', return_value={}), \
                    patch.object(method_class, 'process_weights_after_loading', side_effect=RuntimeError('postprocess failed')):
                with self.assertRaisesRegex(RuntimeError, 'postprocess failed'):
                    loader.load_model(config, model_config)
            self.assertEqual(loader.weights_status, 'installed')
            self.assertEqual(loader.lifecycle_status, 'failed')
            self.assertTrue(loader.discard_worker_required)
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model(config, model_config)


if __name__ == '__main__':
    unittest.main()
