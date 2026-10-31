import hashlib
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch

import vllm_mixed_hook as hook
from marlin_aux import PROFILE
from native_call import NativeCallFailure
import test_cpu_tensor_io
import test_vllm_native_hook
import test_vllm_cpu_hook


class MixedLifecycleTests(unittest.TestCase):
    def deferred_fixture(self, context=None, base=None):
        if base is None:
            base, _ = test_vllm_cpu_hook.LifecycleTests().base([])
        raw = (Path(__file__).parent / 'fixtures/vllm_loader_init.py').resolve()
        code = compile(raw.read_bytes(), str(raw), 'exec', dont_inherit=True)
        ns = dict(_LOAD_FORMAT_TO_MODEL_LOADER={'safetensors': object()}, BaseModelLoader=base, logger=Mock())
        for name in ('register_model_loader', 'get_model_loader'):
            child = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
            ns[name] = types.FunctionType(child, ns)
        registry = types.SimpleNamespace(**ns)
        options = dict(rank_options=[dict(model_path='rank-' + str(n), storage_path='fixture',
            expected_sidecar_sha256='a' * 64, server_address='127.0.0.1:18073',
            build_receipt='fixture', expected_build_sha256='b' * 64,
            max_allocation_bytes=1024, timeout_seconds=30) for n in range(8)],
            device_memory_budgets=list(range(1024, 1032)), config_raw=test_cpu_tensor_io.CONFIG,
            checkpoint_contract=b'fixture', profile=PROFILE, vllm_version='0.19.1', rank_context=context)
        return registry, base, options

    def test_deferred_real_registry_waits_for_rank_and_selects_rank_asset_device_budget(self):
        context = Mock(return_value=dict(rank=3, device_id=5, tp_size=8, pp_size=1))
        registry, base, options = self.deferred_fixture(context)
        original = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
        delegate = Mock()
        with patch.object(hook, 'make_mixed_store_loader', return_value=Mock(return_value=delegate)) as factory:
            cls = hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
            context.assert_not_called()
            factory.assert_not_called()
            options['rank_options'][3]['model_path'] = 'changed'
            options['device_memory_budgets'][5] = 999999
            config = types.SimpleNamespace(load_format='sllm_kimi_native_v1')
            loader = registry.get_model_loader(config)
            self.assertIsInstance(loader, cls)
            result = loader.load_model('config', 'model', 'prefix')
            self.assertIs(result, delegate.load_model.return_value)
            expected = dict(options['rank_options'][3], model_path='rank-3', rank=3, device_id=5)
            self.assertEqual(factory.call_args.kwargs['call_options'], expected)
            self.assertEqual(factory.call_args.kwargs['device_memory_budget_bytes'], 1029)
            delegate.load_model.assert_called_once_with('config', 'model', 'prefix')
            self.assertEqual(loader.lifecycle_status, 'returned')
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model('config', 'model')
            context.assert_called_once()
        self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], original)
        with self.assertRaisesRegex(ValueError, 'another loader'):
            hook.register_deferred_mixed_loader(registry, base, object(), object, **options)

    def test_deferred_invalid_actual_context_is_terminal_before_native_preflight(self):
        for changes in (dict(rank=True), dict(rank=-1), dict(rank=8), dict(device_id=8),
                        dict(tp_size=4), dict(pp_size=2), dict(extra=0)):
            context = Mock(return_value=dict(dict(rank=0, device_id=0, tp_size=8, pp_size=1), **changes))
            registry, base, options = self.deferred_fixture(context)
            cls = hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
            loader = cls(object())
            with patch.object(hook, 'make_mixed_store_loader') as factory:
                with self.assertRaisesRegex(ValueError, 'actual TP8'):
                    loader.load_model('config', 'model')
                self.assertEqual(loader.lifecycle_status, 'failed')
                self.assertTrue(loader.discard_worker_required)
                self.assertIsNone(loader.delegate)
                factory.assert_not_called()

    def test_deferred_default_context_refuses_uninitialized_cuda(self):
        registry, base, options = self.deferred_fixture()
        cls = hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
        with patch.object(torch.cuda, 'is_initialized', return_value=False), \
                patch.object(hook, 'make_mixed_store_loader') as factory:
            with self.assertRaisesRegex(ValueError, 'already be initialized'):
                cls(object()).load_model('config', 'model')
            factory.assert_not_called()

    def test_deferred_rejects_payload_and_opaque_before_copy_or_native(self):
        copied = []
        class Opaque:
            def __deepcopy__(self, memo):
                copied.append(True)
                return self
        for value in (torch.zeros(1), Opaque()):
            registry, base, options = self.deferred_fixture(Mock())
            options['rank_options'][0]['model_path'] = value
            with patch.object(hook, 'make_mixed_store_loader') as factory:
                with self.assertRaisesRegex(ValueError, 'metadata'):
                    hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
                factory.assert_not_called()
            self.assertNotIn('sllm_kimi_native_v1', registry._LOAD_FORMAT_TO_MODEL_LOADER)
        self.assertEqual(copied, [])

    def test_deferred_failure_retains_delegate_and_does_not_retry_or_bypass_budget(self):
        context = Mock(return_value=dict(rank=0, device_id=0, tp_size=8, pp_size=1))
        for phase in ('budget', 'load'):
            registry, base, options = self.deferred_fixture(context)
            cls = hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
            loader = cls(object())
            delegate = Mock()
            delegate.load_model.side_effect = RuntimeError('partial native failure')
            factory = Mock(side_effect=MemoryError('budget')) if phase == 'budget' else Mock(
                return_value=Mock(return_value=delegate))
            with patch.object(hook, 'make_mixed_store_loader', factory):
                with self.assertRaises((MemoryError, RuntimeError)):
                    loader.load_model('config', 'model')
                self.assertEqual(loader.lifecycle_status, 'failed')
                self.assertTrue(loader.discard_worker_required)
                self.assertIs(loader.delegate, None if phase == 'budget' else delegate)
                with self.assertRaisesRegex(ValueError, 'one-shot'):
                    loader.load_model('config', 'model')
                factory.assert_called_once()

    def test_deferred_registration_runs_existing_mixed_base_lifecycle_on_cpu_fixture(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            previous, config, model_config, ns = self.fixture(temp, events)
            base = type(previous).__mro__[1]
            context = Mock(return_value=dict(rank=0, device_id=0, tp_size=8, pp_size=1))
            registry, _, options = self.deferred_fixture(context, base)
            row = dict(storage_path=temp, model_path='rank0', expected_sidecar_sha256=hashlib.sha256(
                (Path(temp) / 'rank0/kimi_raw_state.json').read_bytes()).hexdigest(),
                max_allocation_bytes=1024, timeout_seconds=30, server_address='127.0.0.1:18073',
                build_receipt='fixture-build', expected_build_sha256='fixture-digest')
            options['rank_options'] = [dict(row) for _ in range(8)]
            cls = hook.register_deferred_mixed_loader(registry, base, object(), object, **options)
            loader = cls(config.load_config)
            state, boundaries = self.boundaries(previous, ns, events)
            process = boundaries['process_restored_mixed_rank']
            def check_process(*args, **kwargs):
                self.assertIsNone(loader.delegate.native_state)
                return process(*args, **kwargs)
            boundaries['process_restored_mixed_rank'] = check_process
            with patch.object(hook, 'load_targets', return_value={'packed'}), \
                    patch.object(hook, '_bound_runtime', return_value=({}, {})), \
                    patch.multiple(hook, **boundaries):
                model = loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize', 'auxiliary', 'native', 'install', 'postprocess'])
            self.assertEqual(loader.delegate.lifecycle_status, 'returned')
            self.assertEqual(loader.delegate.weights_status, 'installed')
            self.assertIsNone(loader.delegate.native_state)
            self.assertFalse(model.training)

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
