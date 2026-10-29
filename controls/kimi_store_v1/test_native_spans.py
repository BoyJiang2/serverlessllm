import unittest
import weakref

import torch

from native_spans import retain_native_allocation, validate_native_spans


class NativeSpanTests(unittest.TestCase):
    def fixture(self):
        memory = bytearray(16)
        # Separate frombuffer storages over one allocation mimic from_blob addresses.
        packed = torch.frombuffer(memory, dtype=torch.int32, count=2)
        alias = torch.frombuffer(memory, dtype=torch.int32, count=2)
        scale = torch.frombuffer(memory, dtype=torch.bfloat16, count=2, offset=8)
        state = dict(packed=packed, alias=alias, scale=scale)
        index = dict(packed=[0, 8, [2], [1], 'torch.int32'],
                     alias=[0, 8, [2], [1], 'torch.int32'],
                     scale=[8, 4, [2], [1], 'torch.bfloat16'])
        args = dict(device='cpu', base_address=packed.data_ptr(), allocation_bytes=16,
                    device_offsets=dict(packed=0, alias=0, scale=8), alias_of=dict(alias='packed'))
        return index, state, args

    def test_separate_storages_and_explicit_aliases(self):
        index, state, args = self.fixture()
        receipt = validate_native_spans(index, state, **args)
        self.assertEqual(receipt['unique_payload_bytes'], 12)
        self.assertFalse(receipt['allocation_ownership_verified'])
        self.assertFalse(receipt['payload_verified'])

    def test_bad_address_bounds_device_coverage_or_boolean_rejected(self):
        for kind in ('address', 'bounds', 'device', 'coverage', 'bool', 'offset'):
            index, state, args = self.fixture()
            if kind == 'address':
                args['base_address'] += 4
            elif kind == 'bounds':
                args['allocation_bytes'] = 10
            elif kind == 'device':
                args['device'] = 'cuda:0'
            elif kind == 'coverage':
                del state['scale']
            elif kind == 'bool':
                args['device_offsets']['scale'] = True
            else:
                args['device_offsets']['scale'] = 9
            with self.assertRaises(ValueError):
                validate_native_spans(index, state, **args)

    def test_undeclared_overlap_or_different_alias_layout_refused(self):
        for kind in ('undeclared', 'layout', 'chain'):
            index, state, args = self.fixture()
            if kind == 'undeclared':
                args['alias_of'] = {}
            elif kind == 'layout':
                index['alias'][0] = 16
            else:
                args['alias_of']['packed'] = 'alias'
            with self.assertRaises(ValueError):
                validate_native_spans(index, state, **args)

    def test_valid_sized_but_noncanonical_shape_refused(self):
        index, state, args = self.fixture()
        index['packed'][2:] = [[1, 2], [99, 1], 'torch.int32']
        with self.assertRaisesRegex(ValueError, 'noncanonical'):
            validate_native_spans(index, state, **args)

    def test_model_retains_owner_when_dictionary_and_parameter_are_replaced(self):
        index, state, args = self.fixture()
        validate_native_spans(index, state, **args)
        model = torch.nn.Module()
        model.weight = torch.nn.Parameter(state['packed'], requires_grad=False)
        owner = weakref.ref(state['packed'])
        retain_native_allocation(model, state)
        state.clear()
        model.weight.data = torch.zeros(2, dtype=torch.int32)
        self.assertIsNotNone(owner())
        self.assertNotIn('_kimi_native_allocation_tensors', model.state_dict())
        del model
        self.assertIsNone(owner())

    def test_ownership_anchor_cannot_be_overwritten(self):
        _, state, _ = self.fixture()
        model = torch.nn.Module()
        retain_native_allocation(model, state)
        before = model._kimi_native_allocation_tensors
        with self.assertRaises(ValueError):
            retain_native_allocation(model, state)
        self.assertIs(model._kimi_native_allocation_tensors, before)

    def test_unaligned_actual_base_address_refused(self):
        memory = bytearray(9)
        tensor = torch.frombuffer(memory, dtype=torch.int32, count=2, offset=1)
        with self.assertRaisesRegex(ValueError, 'misaligned'):
            validate_native_spans({'packed': [0, 8, [2], [1], 'torch.int32']}, {'packed': tensor},
                device='cpu', base_address=tensor.data_ptr(), allocation_bytes=8,
                device_offsets={'packed': 0}, alias_of={})


if __name__ == '__main__':
    unittest.main()
