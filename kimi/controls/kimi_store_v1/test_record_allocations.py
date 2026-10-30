import ctypes
import math
import unittest
import weakref
from unittest.mock import Mock

import torch

from native_call import _capsule_address
from record_allocations import plan_independent_records, restore_independent_records


class RecordAllocationTests(unittest.TestCase):
    def fixture(self):
        index = dict(packed=[0, 8, [2], [1], 'torch.int32'],
            alias=[0, 8, [2], [1], 'torch.int32'],
            scale=[8, 4, [2], [1], 'torch.bfloat16'],
            empty=[16, 0, [384, 0], [1, 1], 'torch.int32'])
        plan = plan_independent_records(index, {'alias': 'packed'})
        class Buffer(bytearray):
            pass
        buffers = {'packed': Buffer(b'\xff' * 8), 'scale': Buffer(b'\x00\x80\xc1\x7f')}
        pointers = {}
        create = ctypes.pythonapi.PyCapsule_New
        create.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
        create.restype = ctypes.py_object
        for root, buffer in buffers.items():
            address = torch.frombuffer(buffer, dtype=torch.uint8).data_ptr()
            pointers[root] = create(address, None, None)
        calls = []
        def restore(meta, ptrs, offsets):
            self.assertEqual(len(meta), 1)
            root = next(iter(meta))
            shape, stride, dtype = meta[root]
            tensor = torch.frombuffer(buffers[root], dtype=getattr(torch, dtype.split('.')[1]),
                                      count=math.prod(shape)).reshape(shape)
            self.assertEqual(tensor.data_ptr(), _capsule_address(ptrs[0]))
            self.assertEqual(offsets, {0: {root: 0}})
            calls.append(root)
            return {root: tensor}
        def empty(shape, dtype, device):
            return torch.empty(shape, dtype=getattr(torch, dtype.split('.')[1]), device=device)
        return index, plan, buffers, pointers, restore, empty, calls

    def test_plan_has_distinct_nonempty_handles_and_explicit_alias_dedup(self):
        index, plan, *_ = self.fixture()
        self.assertEqual(plan['allocation_count'], 2)
        self.assertEqual(plan['copy_chunks'], [[0, 8, 0, 0], [8, 4, 0, 1]])
        self.assertEqual(plan['payload_bytes'], 12)
        self.assertEqual(plan['largest_allocation_bytes'], 8)
        self.assertFalse(plan['allocation_ownership_verified'])

    def test_shared_alias_owner_and_independent_storage_release_during_replacement(self):
        index, plan, buffers, pointers, restore, empty, calls = self.fixture()
        packed_owner, scale_owner = weakref.ref(buffers['packed']), weakref.ref(buffers['scale'])
        result = restore_independent_records(index, plan, pointers, device_id=0, expected_device='cpu',
            transfer_confirmed=True, restore_tensors=restore, make_empty=empty)
        state = result.tensors
        self.assertIs(state['packed'], state['alias'])
        self.assertEqual(calls, ['packed', 'scale'])
        model = torch.nn.Module()
        model.a = torch.nn.Parameter(state['packed'], requires_grad=False)
        model.alias = torch.nn.Parameter(state['alias'], requires_grad=False)
        model.b = torch.nn.Parameter(state['scale'], requires_grad=False)
        del state, result
        buffers.clear()
        self.assertIsNotNone(packed_owner())
        model.a.data = torch.zeros(2, dtype=torch.int32)
        self.assertIsNotNone(packed_owner())  # Alias still owns the same StorageImpl.
        model.alias.data = torch.zeros(2, dtype=torch.int32)
        self.assertIsNone(packed_owner())
        self.assertIsNotNone(scale_owner())
        del model
        self.assertIsNone(scale_owner())

    def test_unknown_or_overlapping_source_record_refused(self):
        index, plan, *_ = self.fixture()
        with self.assertRaisesRegex(ValueError, 'overlap'):
            plan_independent_records(index, {})
        index['scale'][1] = True
        with self.assertRaises(ValueError):
            plan_independent_records(index, {'alias': 'packed'})

    def test_unconfirmed_transfer_or_overlapping_pointers_refused_before_restore(self):
        for kind in ('unconfirmed', 'overlap', 'missing'):
            index, plan, buffers, pointers, restore, empty, calls = self.fixture()
            if kind == 'overlap': pointers['scale'] = pointers['packed']
            if kind == 'missing': del pointers['scale']
            with self.assertRaises(ValueError):
                restore_independent_records(index, plan, pointers, device_id=0, expected_device='cpu',
                    transfer_confirmed=kind != 'unconfirmed', restore_tensors=restore, make_empty=empty)
            self.assertFalse(calls)

    def test_changed_plan_or_noncanonical_result_refused(self):
        index, plan, buffers, pointers, restore, empty, calls = self.fixture()
        plan['copy_chunks'][0][2] = 4
        blocked = Mock(side_effect=AssertionError('unexpected restore'))
        with self.assertRaisesRegex(ValueError, 'changed'):
            restore_independent_records(index, plan, pointers, device_id=0, expected_device='cpu',
                transfer_confirmed=True, restore_tensors=blocked, make_empty=empty)
        blocked.assert_not_called()


if __name__ == '__main__':
    unittest.main()
