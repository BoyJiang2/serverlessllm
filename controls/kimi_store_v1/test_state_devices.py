from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from device_state import encode_device_state
import native_install
from runtime_inventory import collect_inventory
from state_devices import state_devices
import test_cpu_tensor_io
import test_independent_install


class StateDevicesTests(unittest.TestCase):
    def model(self):
        return test_cpu_tensor_io.TensorIOTests().model()

    def declared(self, model):
        return {n: 'cpu' for n, _ in list(model.named_parameters(remove_duplicate=False))
                + list(model.named_buffers(remove_duplicate=False))}

    def test_exact_cpu_map_preserves_inventory_layout_and_alias_results(self):
        model = self.model()
        devices = self.declared(model)
        self.assertEqual(collect_inventory(model), collect_inventory(model, device_map=devices))
        before = encode_device_state(model, device='cpu')
        after = encode_device_state(model, device='cuda:0', device_map=devices)
        self.assertEqual(before[1:], after[1:])
        for (a, ta), (b, tb) in zip(before[0], after[0]):
            self.assertEqual(a, b)
            self.assertEqual(ta.data_ptr(), tb.data_ptr())

    def test_incomplete_extra_unindexed_or_wrong_devices_refused(self):
        model = self.model()
        for kind in ('missing', 'extra', 'unindexed', 'wrong', 'meta'):
            devices = self.declared(model)
            if kind == 'missing': del devices['packed_alias']
            elif kind == 'extra': devices['unknown'] = 'cpu'
            else: devices['scale'] = {'unindexed': 'cuda', 'wrong': 'cuda:0', 'meta': 'meta'}[kind]
            with self.assertRaises(ValueError):
                collect_inventory(model, device_map=devices)
            with self.assertRaises(ValueError):
                encode_device_state(model, device='cpu', device_map=devices)
        self.assertEqual(state_devices('cpu', {'a': 'cpu', 'b': 'cuda:2'}, ['a', 'b']),
                         {'a': torch.device('cpu'), 'b': torch.device('cuda:2')})

    def test_hidden_tensor_not_silently_omitted_by_registered_map(self):
        model = self.model()
        devices = self.declared(model)
        model.hidden = [torch.ones(1)]
        with self.assertRaisesRegex(ValueError, 'exactly'):
            collect_inventory(model, device_map=devices)

    def test_mapped_independent_install_preserves_real_bits_and_parameters(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            source, result, buffers, digest = test_independent_install.IndependentInstallTests().fixture(directory)
            target = self.model()
            original = target.packed
            target.packed.data.zero_()
            receipt = native_install.install_native_state(directory, target, result.tensors, digest,
                rank=0, allocation=dict(device='cpu', alias_of=result.plan['alias_of']),
                independent_records=result, target_device_map=self.declared(target), chunk_bytes=3)
            self.assertIs(original, target.packed)
            self.assertTrue(torch.equal(target.packed, source.packed))
            self.assertTrue(torch.equal(target.scale.view(torch.int16), source.scale.view(torch.int16)))
            self.assertTrue(receipt['payload_verified'])
            self.assertFalse(receipt['native_loader_verified'])

    def test_bad_target_map_rejected_before_payload_copy_or_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            source, result, buffers, digest = test_independent_install.IndependentInstallTests().fixture(directory)
            target = self.model()
            original_pointer = target.packed.data_ptr()
            devices = self.declared(target)
            devices['scale'] = 'cuda:1'
            with patch.object(native_install, 'verify_independent_payload') as verify:
                with self.assertRaises(ValueError):
                    native_install.install_native_state(directory, target, result.tensors, digest,
                        rank=0, allocation=dict(device='cpu', alias_of=result.plan['alias_of']),
                        independent_records=result, target_device_map=devices)
                verify.assert_not_called()
            self.assertEqual(target.packed.data_ptr(), original_pointer)
            self.assertFalse(hasattr(target, '_kimi_independent_records_bound'))
            with self.assertRaisesRegex(ValueError, 'independent'):
                native_install.install_native_state(directory, target, result.tensors, digest,
                    rank=0, allocation=dict(device='cpu', alias_of=result.plan['alias_of']),
                    target_device_map=self.declared(target))
