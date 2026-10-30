import builtins
import dataclasses
from pathlib import Path
import sys
import tempfile
import time
import types
import typing
import unittest
from unittest.mock import Mock, patch

import torch

import checkpoint_export as bridge
import device_export
from contract import REVISION
from marlin_aux import PROFILE
import test_cpu_tensor_io


ROOT = Path(__file__).parent / 'fixtures'


def code_at(path, *names):
    code = compile(path.read_bytes(), str(path.resolve()), 'exec', dont_inherit=True)
    for name in names:
        code = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)
    return code


class CheckpointExportTests(unittest.TestCase):
    def fixture(self, root, mode=None):
        source = dict(packed=torch.tensor([-1, -2147483648], dtype=torch.int32),
                      scale=torch.tensor([0x8000, 0x7fc1], dtype=torch.uint16).view(torch.bfloat16))
        class Base:
            def __init__(self, load_config):
                self.load_config = load_config
        def iterator(*args, **kwargs):
            self.assertIsNone(kwargs['local_expert_ids'])
            rows = list(source.items())
            if mode == 'missing_input': rows.pop()
            if mode == 'duplicate': rows.append(rows[0])
            return iter(rows)
        namespace = dict(__builtins__=builtins.__dict__, __name__=__name__,
            BaseModelLoader=Base, dataclasses=dataclasses, torch=torch, nn=torch.nn,
            LoadConfig=types.SimpleNamespace, ModelConfig=types.SimpleNamespace,
            Generator=typing.Generator, Iterable=typing.Iterable, cast=typing.cast,
            instrument=lambda **kwargs: lambda f: f, logger=Mock(), time=time,
            safetensors_weights_iterator=iterator)
        default = builtins.__build_class__(types.FunctionType(
            code_at(ROOT / 'vllm_default_loader.py', 'DefaultModelLoader'), namespace), 'DefaultModelLoader', Base)
        namespace['DefaultModelLoader'] = default
        load_config = types.SimpleNamespace(load_format='safetensors', safetensors_load_strategy='lazy',
            model_loader_extra_config={}, use_tqdm_on_load=False)
        loader = default(load_config)
        class AutoLoader:
            def __init__(self, model):
                self.model = model
            def load_weights(self, weights, mapper):
                loaded = set()
                for name, tensor in weights:
                    if mode == 'throw': raise RuntimeError('fixture loader failed')
                    getattr(self.model, name).data.copy_(tensor)
                    loaded.add(name)
                return {'packed'} if mode == 'missing_target' else loaded
        function = types.FunctionType(code_at(ROOT / 'vllm_kimi_k25.py',
            'KimiK25ForConditionalGeneration', 'load_weights'), {'AutoWeightsLoader': AutoLoader})
        model_cls = type('KimiK25ForConditionalGeneration', (torch.nn.Module,), {'load_weights': function})
        model = model_cls()
        for name, tensor in source.items():
            model.register_parameter(name, torch.nn.Parameter(torch.zeros_like(tensor), requires_grad=False))
        model.hf_to_vllm_mapper = None
        model_config = types.SimpleNamespace(model=str(root), revision=REVISION,
            quantization='compressed-tensors', is_moe=True)
        config_module = types.ModuleType('vllm.config')
        config_module.get_current_vllm_config = lambda: types.SimpleNamespace(
            parallel_config=types.SimpleNamespace(enable_expert_parallel=False))
        patches = dict(_checkpoint_manifest=Mock(return_value=(Path(root), set(source), [str(Path(root) / 'fixture.safetensors')])),
                       load_targets=Mock(return_value=set(source)))
        return loader, model, model_config, source, config_module, patches

    def load(self, loader, model, config, root, module, patches):
        with patch.multiple(bridge, **patches), patch.dict(sys.modules, {'vllm.config': module}):
            return bridge.load_official_rank(loader, model, config, checkpoint_path=root,
                config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture')

    def test_pinned_default_and_kimi_loaders_preserve_bits_and_restore_instance_methods(self):
        with tempfile.TemporaryDirectory() as temp:
            loader, model, config, source, module, patches = self.fixture(temp)
            original_model = model.load_weights.__func__
            original_prepare = loader._prepare_weights.__func__
            receipt = self.load(loader, model, config, temp, module, patches)
            self.assertTrue(torch.equal(model.packed, source['packed']))
            self.assertTrue(torch.equal(model.scale.view(torch.int16), source['scale'].view(torch.int16)))
            self.assertIs(model.load_weights.__func__, original_model)
            self.assertIs(loader._prepare_weights.__func__, original_prepare)
            self.assertEqual(model._kimi_source_export_status, 'checkpoint_loaded')
            self.assertEqual(receipt['source_name_count'], 2)
            self.assertFalse(receipt['checkpoint_payload_verified'])
            self.assertFalse(receipt['postprocessing_performed'])
            with self.assertRaisesRegex(ValueError, 'one-shot'):
                self.load(loader, model, config, temp, module, patches)

    def test_missing_or_duplicate_source_target_or_loader_error_refuses_export(self):
        for mode in ('missing_input', 'duplicate', 'missing_target', 'throw'):
            with tempfile.TemporaryDirectory() as temp:
                loader, model, config, source, module, patches = self.fixture(temp, mode)
                with self.assertRaises((ValueError, RuntimeError)):
                    self.load(loader, model, config, temp, module, patches)
                self.assertEqual(model._kimi_source_export_status, 'failed')
                self.assertNotIn('load_weights', vars(model))
                self.assertNotIn('_prepare_weights', vars(loader))

    def test_wrong_revision_or_multithread_mode_refused_before_model_mutation(self):
        for kind in ('revision', 'thread'):
            with tempfile.TemporaryDirectory() as temp:
                loader, model, config, source, module, patches = self.fixture(temp)
                if kind == 'revision': config.revision = 'other'
                else: loader.load_config.model_loader_extra_config['enable_multithread_load'] = True
                with self.assertRaises(ValueError):
                    self.load(loader, model, config, temp, module, patches)
                self.assertFalse(hasattr(model, '_kimi_source_export_status'))
                self.assertTrue(torch.equal(model.packed, torch.zeros_like(model.packed)))

    def test_component_conversion_writes_archive_without_postprocessing(self):
        with tempfile.TemporaryDirectory() as temp:
            loader, model, config, source, module, patches = self.fixture(temp)
            output = Path(temp) / 'rank'
            with patch.multiple(bridge, **patches), patch.dict(sys.modules, {'vllm.config': module}), \
                    patch.object(device_export, 'normalize_rank_cpu_auxiliary', return_value=[]), \
                    patch.object(device_export, 'load_targets', return_value=set(source)):
                receipt = bridge.convert_checkpoint_rank(output, loader, model, config,
                    checkpoint_path=temp, config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture',
                    device='cpu', rank=0, profile=PROFILE, vllm_version='0.19.1', torch_version='2.10.0', chunk_bytes=7)
            self.assertEqual(model._kimi_source_export_status, 'exported')
            self.assertTrue((output / 'tensor_index.json').is_file())
            self.assertFalse(receipt['source']['postprocessing_performed'])
            self.assertFalse(receipt['archive']['store_created'])


if __name__ == '__main__':
    unittest.main()
