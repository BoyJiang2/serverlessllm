from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import torch

from loader_registry import FORMATS, register_candidate_loader
from marlin_aux import PROFILE
import test_checkpoint_export
import test_cpu_tensor_io
import test_vllm_native_hook
import vllm_export_hook as hook


class ExportLifecycleTests(unittest.TestCase):
    def fixture(self, temp, events, context=None):
        root = Path(temp) / 'checkpoint'
        root.mkdir()
        output = Path(temp) / 'output'
        output.mkdir()
        native_loader, config, model_config, namespace = test_vllm_native_hook.NativeLifecycleTests().setup_fixture(root, events)
        base = type(native_loader).__bases__[0]
        default = type(test_checkpoint_export.CheckpointExportTests().fixture(root)[0])
        config.load_config.load_format = FORMATS['export']
        config.load_config.model_loader_extra_config = {}
        if context is None:
            context = dict(rank=0, tp_size=8, pp_size=1, device_id=0)
        with patch.object(hook, 'load_targets', return_value={'packed'}), \
                patch.object(hook, '_checkpoint_manifest', return_value=(root, set(), [])):
            cls = hook.make_checkpoint_export_loader(base, default, checkpoint_path=root, output_root=output,
                config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture', profile=PROFILE,
                vllm_version='0.19.1', torch_version='2.10.0', rank_context=lambda: dict(context), chunk_bytes=7)
        return cls(config.load_config), config, model_config, base, output

    def test_export_precedes_one_backend_postprocess_and_source_config_is_separate(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, base, output = self.fixture(temp, events)
            def convert(directory, source, model, model_config, **kwargs):
                events.append('convert')
                self.assertEqual(directory, output / 'rank-0')
                self.assertEqual(source.load_config.load_format, 'safetensors')
                self.assertEqual(config.load_config.load_format, FORMATS['export'])
                self.assertIsNot(source.load_config, config.load_config)
                self.assertEqual(kwargs['device'], 'cuda:0')
                model.packed.data[0] = -1
                return {'archive': {'store_created': False}}
            # GPU/device/conversion boundaries mocked; exact Base/utility ordering executes.
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'convert_checkpoint_rank', side_effect=convert):
                loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize', 'convert', 'quant_postprocess', 'attention_postprocess'])
            self.assertEqual(loader.weights_status, 'exported')
            self.assertEqual(loader.lifecycle_status, 'returned')
            self.assertFalse(loader.conversion_receipt['archive']['store_created'])
            self.assertTrue(loader.discard_worker_required)
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                loader.load_model(config, model_config)

    def test_rank_context_mismatch_refused_before_initializer(self):
        for field, value in (('rank', 8), ('device_id', True), ('tp_size', 4), ('pp_size', 2)):
            context = dict(rank=0, tp_size=8, pp_size=1, device_id=0)
            context[field] = value
            events = []
            with tempfile.TemporaryDirectory() as temp:
                loader, config, model_config, _, _ = self.fixture(temp, events, context)
                with self.assertRaisesRegex(ValueError, 'context required'):
                    loader.load_model(config, model_config)
            self.assertFalse(events)

    def test_conversion_failure_has_no_postprocess_and_is_terminal(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, model_config, _, _ = self.fixture(temp, events)
            with patch.object(hook, '_require_rank_device'), \
                    patch.object(hook, 'convert_checkpoint_rank', side_effect=RuntimeError('fixture export failure')):
                with self.assertRaisesRegex(RuntimeError, 'export failure'):
                    loader.load_model(config, model_config)
            self.assertEqual(events, ['initialize'])
            self.assertEqual(loader.lifecycle_status, 'failed')
            self.assertEqual(loader.weights_status, 'failed')

    def test_default_context_refuses_uninitialized_cuda_without_importing_vllm(self):
        with patch.object(torch.cuda, 'is_initialized', return_value=False):
            with self.assertRaisesRegex(ValueError, 'already be initialized'):
                hook.current_rank_context()

    def test_source_registry_idempotence_and_foreign_collision_without_builtin_changes(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            loader, config, _, base, _ = self.fixture(temp, events)
            raw = Path(__file__).parent / 'fixtures/vllm_loader_init.py'
            code = compile(raw.read_bytes(), str(raw.resolve()), 'exec', dont_inherit=True)
            namespace = dict(_LOAD_FORMAT_TO_MODEL_LOADER={'safetensors': object()}, BaseModelLoader=base, logger=Mock())
            for name in ('register_model_loader', 'get_model_loader'):
                child = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
                namespace[name] = types.FunctionType(child, namespace)
            registry = types.SimpleNamespace(**namespace)
            original = registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors']
            name = register_candidate_loader(registry, type(loader), mode='export')
            self.assertEqual(name, FORMATS['export'])
            self.assertEqual(register_candidate_loader(registry, type(loader), mode='export'), name)
            self.assertIsInstance(registry.get_model_loader(config.load_config), type(loader))
            foreign = type('Foreign', (type(loader),), {})
            with self.assertRaisesRegex(ValueError, 'another loader'):
                register_candidate_loader(registry, foreign, mode='export')
            self.assertIs(registry._LOAD_FORMAT_TO_MODEL_LOADER['safetensors'], original)
            self.assertFalse(events)


if __name__ == '__main__':
    unittest.main()
