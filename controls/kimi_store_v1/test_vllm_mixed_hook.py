import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

import vllm_mixed_hook as hook
from marlin_aux import PROFILE
from native_call import NativeCallFailure
import test_cpu_tensor_io
import test_vllm_native_hook


class MixedLifecycleTests(unittest.TestCase):
    def fixture(self, root, events, budget=1024):
        legacy, config, model_config, ns = test_vllm_native_hook.NativeLifecycleTests().setup_fixture(root, events)
        config.model_config = model_config
        class DeviceScope:
            def __str__(self): return 'cuda:0'
            def __enter__(self):
                self.scope = torch.device('cpu')
                return self.scope.__enter__()
            def __exit__(self, *args): return self.scope.__exit__(*args)
        ns['torch'] = types.SimpleNamespace(device=lambda value: DeviceScope(),
            accelerator=types.SimpleNamespace(max_memory_allocated=lambda: 0))
        options = dict(storage_path=root, model_path='rank0', expected_sidecar_sha256=hashlib.sha256(
            (Path(root) / 'rank0/kimi_raw_state.json').read_bytes()).hexdigest(), rank=0, device_id=0,
            max_allocation_bytes=1024, timeout_seconds=30, server_address='127.0.0.1:18073',
            build_receipt='fixture-build', expected_build_sha256='fixture-digest')
        with patch.object(hook, 'load_targets', return_value={'packed'}), \
                patch.object(hook, '_bound_runtime', return_value=({}, {})):
            cls = hook.make_mixed_store_loader(ns['BaseModelLoader'], object(), object,
                call_options=options, config_raw=test_cpu_tensor_io.CONFIG,
                checkpoint_contract=b'fixture', profile=PROFILE, vllm_version='0.19.1',
                device_memory_budget_bytes=budget)
        return cls(config.load_config), config, model_config, ns

    def boundaries(self, loader, ns, events, fail=None):
        state = types.SimpleNamespace(device='cuda:0', tensors={})
        def initialize(*args, **kwargs):
            if fail == 'initialize': raise RuntimeError('initialize failed')
            return ns['initialize_model'](), {}
        def auxiliary(*args, **kwargs):
            events.append('auxiliary')
            if fail == 'auxiliary': raise RuntimeError('auxiliary failed')
            return []
        def load(*args, **kwargs):
            events.append('native')
            if fail == 'native': raise NativeCallFailure({'stage': 'gpu_request', 'worker_cleanup_required': True}, state)
            return state, {'allocation': {'device': 'cuda:0', 'alias_of': {}}}
        def install(directory, model, tensors, digest, **kwargs):
            events.append('install')
            self.assertIs(kwargs['independent_records'], state)
            if fail == 'install': raise RuntimeError('install failed')
            model.packed.data[0] = -1
            model._kimi_independent_records_bound = True
            return {'native_loader_verified': False}
        def process(*args, **kwargs):
            events.append('postprocess')
            self.assertIsNone(loader.native_state)
            if fail == 'postprocess': raise RuntimeError('postprocess failed')
            return {'runtime_admitted': False}
        return state, dict(initialize_mixed_vllm_rank=initialize,
            normalize_rank_mixed_auxiliary=auxiliary, mixed_state_devices=lambda *a: {},
            load_native_records=load, install_native_state=install,
            process_restored_mixed_rank=process)

    def test_private_frozen_base_orders_all_components_without_global_patch(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, ns = self.fixture(temp, events)
            original_init = ns['initialize_model']
            original_process = ns['process_weights_after_loading']
            state, boundaries = self.boundaries(loader, ns, events)
            with patch.multiple(hook, **boundaries):
                model = loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize', 'auxiliary', 'native', 'install', 'postprocess'])
            self.assertEqual(loader.lifecycle_status, 'returned')
            self.assertEqual(loader.weights_status, 'installed')
            self.assertIsNone(loader.native_state)
            self.assertFalse(model.training)
            self.assertIs(ns['initialize_model'], original_init)
            self.assertIs(ns['process_weights_after_loading'], original_process)
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model(config, model_config)

    def test_failure_at_each_boundary_is_terminal_and_preserves_cleanup_state(self):
        for phase in ('initialize', 'auxiliary', 'native', 'install', 'postprocess'):
            events = []
            with tempfile.TemporaryDirectory() as temp:
                loader, config, model_config, ns = self.fixture(temp, events)
                state, boundaries = self.boundaries(loader, ns, events, fail=phase)
                with patch.multiple(hook, **boundaries):
                    with self.assertRaises((RuntimeError, NativeCallFailure)):
                        loader.load_model(config, model_config)
                self.assertEqual(loader.lifecycle_status, 'failed')
                self.assertTrue(loader.discard_worker_required)
                if phase in ('native', 'install'):
                    self.assertIs(loader.native_state, state)
                with self.assertRaisesRegex(ValueError, 'one-shot'):
                    loader.load_model(config, model_config)
                if phase != 'postprocess': self.assertNotIn('postprocess', events)

    def test_conservative_budget_stop_precedes_construction(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(MemoryError, 'conservative'):
                self.fixture(temp, events, budget=1)
        self.assertEqual(events, [])

    def test_different_model_config_rejected_before_any_initialization(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, ns = self.fixture(temp, events)
            config.model_config = types.SimpleNamespace()
            with self.assertRaisesRegex(ValueError, 'same model config'):
                loader.load_model(config, model_config)
        self.assertEqual(events, [])

    def test_uncompiled_patch_rejected_before_payload_preflight(self):
        patch_path = Path(__file__).parent / 'native_patch/ledger.json'
        options = dict(storage_path='not-used', model_path='not-used', expected_sidecar_sha256='not-used',
            rank=0, device_id=0, max_allocation_bytes=1024, timeout_seconds=30,
            server_address='127.0.0.1:18073', build_receipt=str(patch_path),
            expected_build_sha256=hashlib.sha256(patch_path.read_bytes()).hexdigest())
        with patch.object(hook, 'load_targets', return_value={'packed'}), \
                patch.object(hook, 'read_index') as scan:
            with self.assertRaisesRegex(ValueError, 'compiled'):
                hook.make_mixed_store_loader(object, object(), object,
                    call_options=options, config_raw=test_cpu_tensor_io.CONFIG,
                    checkpoint_contract=b'fixture', profile=PROFILE, vllm_version='0.19.1',
                    device_memory_budget_bytes=1024)
            scan.assert_not_called()
