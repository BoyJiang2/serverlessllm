import ast
import hashlib
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import torch

import marlin_aux
import test_cpu_tensor_io

SOURCE = Path(__file__).parent / 'fixtures/vllm_w4a16_moe.py'


class AuxiliaryTests(unittest.TestCase):
    def method(self):
        raw = SOURCE.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), marlin_aux.SOURCE_SHA256)
        module_code = compile(raw, str(SOURCE.resolve()), 'exec', dont_inherit=True)
        cls = next(c for c in module_code.co_consts if isinstance(c, types.CodeType) and c.co_name == marlin_aux.METHOD_CLASS)
        method = next(c for c in cls.co_consts if isinstance(c, types.CodeType) and c.co_name == 'process_weights_after_loading')
        function = types.FunctionType(method, dict(torch=torch))
        # Source-bound isolated method, not an imported or initialized vLLM backend.
        fake_class = type(marlin_aux.METHOD_CLASS, (), {'__module__': marlin_aux.METHOD_MODULE,
            'process_weights_after_loading': function})
        result = fake_class()
        result.kernel_backend, result.actorder = 'Marlin', None
        return result

    def layer(self):
        layer = torch.nn.Module()
        layer.quant_method = self.method()
        for name in marlin_aux.NAMES:
            width = 7168 if name.startswith('w13') else 256
            layer.register_parameter(name, torch.nn.Parameter(torch.empty(384, width, dtype=torch.int32), requires_grad=False))
        layer.packed = torch.nn.Parameter(torch.tensor([1, -2], dtype=torch.int32), requires_grad=False)
        return layer

    def normalize(self, layer, **overrides):
        kwargs = dict(name='language_model.model.layers.1.mlp.experts', rank=0,
                      profile=marlin_aux.PROFILE, config_raw=test_cpu_tensor_io.CONFIG,
                      vllm_version='0.19.1', phase='checkpoint_loaded_before_postprocess')
        kwargs.update(overrides)
        return marlin_aux.normalize_cpu_auxiliary(layer, **kwargs)

    def test_source_branch_and_helper_recreate_identical_empty_metadata(self):
        layer = self.layer()
        pointers = {n: getattr(layer, n) for n in marlin_aux.NAMES}
        original = layer.packed.detach().clone()
        receipt = self.normalize(layer)
        self.assertFalse(receipt['scratch_payload_read'])
        self.assertFalse(receipt['runtime_admitted'])
        self.assertTrue(torch.equal(layer.packed, original))
        for name in marlin_aux.NAMES:
            self.assertIsNot(getattr(layer, name), pointers[name])
            self.assertEqual(list(getattr(layer, name).shape), [384, 0])
        # Execute only the exact pinned native no-actorder else-body, no GPU ops.
        raw = ast.parse(SOURCE.read_bytes())
        cls = next(n for n in raw.body if isinstance(n, ast.ClassDef) and n.name == marlin_aux.METHOD_CLASS)
        process = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'process_weights_after_loading')
        branch = next(n for n in process.body if isinstance(n, ast.If) and ast.unparse(n.test) == "self.actorder == 'group'")
        native = torch.nn.Module()
        exec(compile(ast.Module(body=branch.orelse, type_ignores=[]), str(SOURCE), 'exec'),
             dict(layer=native, torch=torch, num_experts=384, device=torch.device('cpu')))
        for name in marlin_aux.NAMES:
            self.assertEqual(receipt['after'][name]['shape'], list(getattr(native, name).shape))
            self.assertEqual(receipt['after'][name]['dtype'], str(getattr(native, name).dtype))

    def test_rejected_context_or_layout_does_not_modify_bindings(self):
        layer = self.layer()
        before = {n: getattr(layer, n) for n in marlin_aux.NAMES}
        for overrides in ({'rank': True}, {'phase': 'after_postprocess'}, {'vllm_version': 'other'},
                          {'profile': dict(marlin_aux.PROFILE, expert_parallel=True)}):
            with self.assertRaises(ValueError):
                self.normalize(layer, **overrides)
        for backend, actorder in [('Flashinfer', None), ('Marlin', 'group')]:
            layer.quant_method.kernel_backend, layer.quant_method.actorder = backend, actorder
            with self.assertRaises(ValueError):
                self.normalize(layer)
        self.assertTrue(all(getattr(layer, n) is t for n, t in before.items()))

    def test_unknown_method_and_tied_payload_refused(self):
        layer = self.layer()
        layer.quant_method = types.SimpleNamespace(kernel_backend='Marlin', actorder=None)
        with self.assertRaises(ValueError):
            self.normalize(layer)
        layer.quant_method = self.method()
        layer.payload_alias = layer.w13_weight_g_idx
        with self.assertRaisesRegex(ValueError, 'shares storage'):
            self.normalize(layer)

    def test_second_normalization_is_not_silently_accepted(self):
        layer = self.layer()
        self.normalize(layer)
        with self.assertRaises(ValueError):
            self.normalize(layer)

    def test_changed_source_or_last_aux_layout_fails_before_binding_changes(self):
        layer = self.layer()
        before = {n: getattr(layer, n) for n in marlin_aux.NAMES}
        with patch.object(Path, 'read_bytes', return_value=b'changed source'):
            with self.assertRaisesRegex(ValueError, 'source differs'):
                self.normalize(layer)
        self.assertTrue(all(getattr(layer, n) is t for n, t in before.items()))
        layer.w2_g_idx_sort_indices = torch.nn.Parameter(torch.empty(384, 0, dtype=torch.int32), requires_grad=False)
        with self.assertRaisesRegex(ValueError, 'layout differs'):
            self.normalize(layer)
        self.assertTrue(all(getattr(layer, n) is before[n] for n in marlin_aux.NAMES[:-1]))

    def test_changed_method_with_same_source_filename_rejected(self):
        layer = self.layer()
        before = {n: getattr(layer, n) for n in marlin_aux.NAMES}
        namespace = {}
        exec(compile('def process_weights_after_loading(self, layer):\n    return layer.w13_weight_g_idx[0, 0]\n',
                     str(SOURCE.resolve()), 'exec'), namespace)
        type(layer.quant_method).process_weights_after_loading = namespace['process_weights_after_loading']
        with self.assertRaisesRegex(ValueError, 'method code differs'):
            self.normalize(layer)
        self.assertTrue(all(getattr(layer, n) is t for n, t in before.items()))

    def test_instance_method_override_rejected(self):
        layer = self.layer()
        before = layer.w13_weight_g_idx
        layer.quant_method.process_weights_after_loading = lambda layer: None
        with self.assertRaisesRegex(ValueError, 'instance override'):
            self.normalize(layer)
        self.assertIs(layer.w13_weight_g_idx, before)


if __name__ == '__main__':
    unittest.main()
