import contextlib
import builtins
import hashlib
from pathlib import Path
import types
import unittest
from abc import ABC, abstractmethod
from unittest.mock import patch
import tempfile

import torch

import cpu_restore
import vllm_cpu_hook as hook
from auxiliary_manifest import validate_auxiliary_receipts
from marlin_aux import PROFILE
import test_cpu_tensor_io
import test_install_state
import test_marlin_aux
import test_buffer_codec
from buffer_codec import CODEC, VISION_CACHE

ROOT = Path(__file__).parent


def code_in(code, name):
    return next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)


class LifecycleTests(unittest.TestCase):
    def base(self, events):
        base_path = (ROOT / 'fixtures/vllm_base_loader.py').resolve()
        utils_path = (ROOT / 'fixtures/vllm_loader_utils.py').resolve()
        self.assertEqual(hashlib.sha256(base_path.read_bytes()).hexdigest(), hook.BASE_SHA256)
        self.assertEqual(hashlib.sha256(utils_path.read_bytes()).hexdigest(), hook.UTILS_SHA256)
        class QuantMethod:
            uses_meta_device = False
            def process_weights_after_loading(self, model):
                self_test.assertEqual(int(model.packed[0]), -1)
                events.append('quant_postprocess')  # Control marker, no real kernel emulation.
        class Attention(torch.nn.Module):
            def process_weights_after_loading(self, dtype):
                events.append('attention_postprocess')
        self_test = self
        def initialize_model(**kwargs):
            events.append('initialize')
            model = test_cpu_tensor_io.TensorIOTests().model()
            model.packed.data.zero_()
            model.quant_method = QuantMethod()
            model.attention = Attention()
            return model
        @contextlib.contextmanager
        def dtype_context(dtype):
            previous = torch.get_default_dtype()
            torch.set_default_dtype(dtype)
            try:
                yield
            finally:
                torch.set_default_dtype(previous)
        namespace = dict(__builtins__=builtins.__dict__, __name__='cpu_lifecycle_fixture',
            torch=torch, nn=torch.nn, ABC=ABC, abstractmethod=abstractmethod,
            LoadConfig=types.SimpleNamespace, ModelConfig=types.SimpleNamespace, VllmConfig=types.SimpleNamespace,
            instrument=lambda **kwargs: lambda f: f, initialize_model=initialize_model,
            set_default_torch_dtype=dtype_context, log_model_inspection=lambda model: None,
            current_platform=types.SimpleNamespace(is_cpu=lambda: True, is_cuda=lambda: False),
            logger=types.SimpleNamespace(debug=lambda *args: None),
            finalize_layerwise_processing=lambda *args: self.fail('unexpected online quantization'))
        base_code = compile(base_path.read_bytes(), str(base_path), 'exec', dont_inherit=True)
        utils_code = compile(utils_path.read_bytes(), str(utils_path), 'exec', dont_inherit=True)
        utility_globals = dict(__builtins__=builtins.__dict__, torch=torch, QuantizeMethodBase=QuantMethod, Attention=Attention, MLAAttention=Attention)
        utility_globals['device_loading_context'] = contextlib.contextmanager(
            types.FunctionType(code_in(utils_code, 'device_loading_context'), utility_globals))
        namespace['process_weights_after_loading'] = types.FunctionType(code_in(utils_code, 'process_weights_after_loading'), utility_globals)
        namespace['_has_online_quant'] = types.FunctionType(code_in(base_code, '_has_online_quant'), namespace)
        namespace['BaseModelLoader'] = builtins.__build_class__(
            types.FunctionType(code_in(base_code, 'BaseModelLoader'), namespace), 'BaseModelLoader', ABC)
        return namespace['BaseModelLoader'], namespace

    def candidate(self, base, directory, digest):
        return hook.make_cpu_store_loader(base, directory=directory, expected_sidecar_sha256=digest,
            rank=0, config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=(ROOT / 'checkpoint-targets.json').read_bytes(),
            profile=PROFILE, vllm_version='0.19.1')

    def configs(self):
        load = types.SimpleNamespace(device='cpu')
        return types.SimpleNamespace(load_config=load, device_config=types.SimpleNamespace(device='cpu')), \
            types.SimpleNamespace(dtype=torch.float32, quantization='compressed-tensors')

    def test_exact_base_lifecycle_restore_before_quant_and_attention_once(self):
        events = []
        base, _ = self.base(events)
        source = test_cpu_tensor_io.TensorIOTests().model()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            _, digest = test_install_state.InstallTests().export(directory, source)
            cls = self.candidate(base, directory, digest)
            config, model_config = self.configs()
            loader = cls(config.load_config)
            # Only name coverage and full-rank preparation boundaries are mocked;
            # pinned Base/utility code and CPU payload restore really execute.
            with patch.object(hook, 'normalize_rank_cpu_auxiliary', return_value=[]) as prepare, \
                    patch.object(cpu_restore, 'load_targets', return_value=set(dict(source.named_parameters()) | dict(source.named_buffers()))):
                model = loader.load_model(config, model_config)
            self.assertEqual(prepare.call_args.kwargs['phase'], 'target_initialized_before_restore')
            self.assertEqual(events, ['initialize', 'quant_postprocess', 'attention_postprocess'])
            self.assertFalse(model.training)
            self.assertEqual(loader.weights_status, 'installed')
            self.assertEqual(loader.lifecycle_status, 'returned')
            self.assertFalse(loader.restore_receipt['native_loader_verified'])
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_weights(model, model_config)
            with self.assertRaisesRegex(ValueError, 'before initialization'):
                loader.load_model(config, model_config)
            self.assertEqual(events.count('initialize'), 1)

    def test_device_or_platform_rejected_before_initialization(self):
        for kind in ('device', 'platform'):
            events = []
            base, namespace = self.base(events)
            cls = self.candidate(base, 'unused', 'unused')
            config, model_config = self.configs()
            if kind == 'device':
                config.load_config.device = 'cuda'
            else:
                namespace['current_platform'].is_cpu = lambda: False
            with self.assertRaisesRegex(ValueError, 'before initialization'):
                cls(config.load_config).load_model(config, model_config)
            self.assertFalse(events)

    def test_lazy_cache_restored_before_exact_base_postprocessing(self):
        self.cache_lifecycle(existing=False)

    def test_existing_cache_codec_forwarded_before_rank_preparation(self):
        self.cache_lifecycle(existing=True)

    def cache_lifecycle(self, existing):
        events = []
        base, namespace = self.base(events)
        source = test_cpu_tensor_io.TensorIOTests().model()
        source.vision_tower = test_buffer_codec.BufferCodecTests().model().vision_tower
        del source.vision_tower.encoder.rope_2d.cache_alias
        original_init = namespace['initialize_model']
        def initialize(**kwargs):
            model = original_init(**kwargs)
            model.vision_tower = test_buffer_codec.BufferCodecTests().model().vision_tower
            del model.vision_tower.encoder.rope_2d.cache_alias
            if existing:
                model.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32).zero_()
            else:
                del model.vision_tower.encoder.rope_2d.freqs_cis
            return model
        namespace['initialize_model'] = initialize
        function = base.load_model.__globals__['process_weights_after_loading']
        quant_class = function.__globals__['QuantizeMethodBase']
        original_postprocess = quant_class.process_weights_after_loading
        def postprocess(method, model):
            self.assertTrue(torch.equal(model.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32),
                source.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32)))
            self.assertNotIn(VISION_CACHE, model.state_dict())
            original_postprocess(method, model)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            _, digest = test_install_state.InstallTests().export(directory, source, {VISION_CACHE: CODEC})
            config, model_config = self.configs()
            loader = self.candidate(base, directory, digest)(config.load_config)
            with patch.object(hook, 'normalize_rank_cpu_auxiliary', return_value=[]) as prepare, \
                    patch.object(cpu_restore, 'load_targets', return_value=set(dict(source.named_parameters()))), \
                    patch.object(quant_class, 'process_weights_after_loading', postprocess):
                loader.load_model(config, model_config)
            self.assertEqual(prepare.call_args.kwargs['buffer_codecs'],
                {VISION_CACHE: CODEC} if existing else None)
        self.assertEqual(events, ['initialize', 'quant_postprocess', 'attention_postprocess'])
        self.assertEqual(loader.restore_receipt['created_lazy_buffers'], [] if existing else [VISION_CACHE])

    def test_corrupt_store_failure_never_reaches_postprocessing(self):
        events = []
        base, _ = self.base(events)
        source = test_cpu_tensor_io.TensorIOTests().model()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            _, digest = test_install_state.InstallTests().export(directory, source)
            with (directory / 'tensor.data_0').open('r+b') as stream:
                stream.write(b'bad!')
            config, model_config = self.configs()
            loader = self.candidate(base, directory, digest)(config.load_config)
            with patch.object(hook, 'normalize_rank_cpu_auxiliary', return_value=[]), \
                    patch.object(cpu_restore, 'load_targets', return_value=set(dict(source.named_parameters()) | dict(source.named_buffers()))):
                with self.assertRaises(ValueError):
                    loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize'])
            self.assertEqual(loader.weights_status, 'failed')

    def test_changed_base_code_and_target_receipt_as_source_rejected(self):
        events = []
        base, _ = self.base(events)
        def load_model(self):
            return None
        bad = type('BaseModelLoader', (base,), {'load_model': load_model})
        with self.assertRaises(ValueError):
            self.candidate(bad, 'unused', 'unused')
        helper = test_marlin_aux.AuxiliaryTests()
        layer = helper.layer()
        receipt = helper.normalize(layer, phase='target_initialized_before_restore')
        layouts = {receipt['layer'] + '.' + n: r for n, r in receipt['after'].items()}
        with self.assertRaisesRegex(ValueError, 'phase differs'):
            validate_auxiliary_receipts([receipt], layouts, 0)
        validate_auxiliary_receipts([receipt], layouts, 0, phases=('target_initialized_before_restore',))
        with self.assertRaisesRegex(ValueError, 'phase policy'):
            validate_auxiliary_receipts([receipt], layouts, 0, phases=('unreviewed',))

    def test_initialize_failure_consumes_lifecycle_before_second_attempt(self):
        events = []
        base, namespace = self.base(events)
        config, model_config = self.configs()
        loader = self.candidate(base, 'unused', 'unused')(config.load_config)
        def failed(**kwargs):
            events.append('initialize_failed')
            raise RuntimeError('initialization failed')
        namespace['initialize_model'] = failed
        with self.assertRaisesRegex(RuntimeError, 'initialization failed'):
            loader.load_model(config, model_config)
        self.assertEqual(loader.lifecycle_status, 'failed')
        with self.assertRaisesRegex(ValueError, 'before initialization'):
            loader.load_model(config, model_config)
        self.assertEqual(events, ['initialize_failed'])

    def test_postprocess_failure_marks_entire_lifecycle_failed(self):
        events = []
        base, _ = self.base(events)
        source = test_cpu_tensor_io.TensorIOTests().model()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            _, digest = test_install_state.InstallTests().export(directory, source)
            config, model_config = self.configs()
            loader = self.candidate(base, directory, digest)(config.load_config)
            # Keep exact utility function code, fail inside the test quant marker.
            function = base.load_model.__globals__['process_weights_after_loading']
            quant_class = function.__globals__['QuantizeMethodBase']
            with patch.object(quant_class, 'process_weights_after_loading', side_effect=RuntimeError('kernel marker failed')), \
                    patch.object(hook, 'normalize_rank_cpu_auxiliary', return_value=[]), \
                    patch.object(cpu_restore, 'load_targets', return_value=set(dict(source.named_parameters()) | dict(source.named_buffers()))):
                with self.assertRaisesRegex(RuntimeError, 'kernel marker failed'):
                    loader.load_model(config, model_config)
            self.assertEqual(loader.weights_status, 'installed')
            self.assertEqual(loader.lifecycle_status, 'failed')
            self.assertNotIn('attention_postprocess', events)


if __name__ == '__main__':
    unittest.main()
