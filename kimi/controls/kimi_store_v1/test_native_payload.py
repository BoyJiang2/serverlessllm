import hashlib
import unittest
import weakref
from unittest.mock import patch

import torch

import native_payload
import test_native_spans
import test_record_allocations
from record_allocations import restore_independent_records


class NativePayloadTests(unittest.TestCase):
    def audit(self, mode):
        if mode == 'single':
            index, state, records, args = self.fixture()
            return lambda: native_payload.verify_native_payload(index,state,records,chunk_bytes=3,**args), state
        index, plan, buffers, pointers, restore, empty, calls = test_record_allocations.RecordAllocationTests().fixture()
        result = restore_independent_records(index,plan,pointers,device_id=0,expected_device='cpu',
            transfer_confirmed=True,restore_tensors=restore,make_empty=empty)
        records = {n: dict(bytes=index[n][1],sha256=hashlib.sha256(
            b''.join(native_payload.iter_host_byte_blocks(t,3))).hexdigest()) for n,t in result.tensors.items()}
        return lambda: native_payload.verify_independent_payload(index,result,records,chunk_bytes=3), result.tensors

    def test_both_audit_failures_release_host_staging_with_traceback_and_generator_retained(self):
        original = native_payload.iter_host_byte_blocks
        real_sha = hashlib.sha256
        class Digest:
            def update(self, data):
                del data  # Simulate a C-call fault without a Python argument owner.
                raise RuntimeError('audit hash fault')
        for mode in ('single', 'independent'):
            with self.subTest(mode=mode):
                run, state = self.audit(mode)
                owners, iterators, failures = [], [], []
                def blocks(tensor, chunk_bytes):
                    inner = original(tensor,chunk_bytes)
                    try:
                        for block in inner:
                            owners.append(weakref.ref(block.obj._tensor_owner))
                            self.assertNotEqual(block.obj._tensor_owner.data_ptr(),tensor.data_ptr())
                            yield block
                    finally:
                        inner.close()
                def tracked(*args):
                    iterator = blocks(*args)
                    iterators.append(iterator)
                    return iterator
                with patch.object(native_payload,'iter_host_byte_blocks',side_effect=tracked), \
                        patch.object(native_payload.hashlib,'sha256',side_effect=lambda data=b'': real_sha(data) if data else Digest()):
                    try: run()
                    except RuntimeError as error: failures.append(error)
                try:
                    self.assertEqual(str(failures[0]),'audit hash fault')
                    self.assertIsNotNone(failures[0].__traceback__)
                    self.assertEqual(len(owners),1)
                    self.assertIsNone(owners[0](),'failed audit retained host staging')
                    self.assertIsNone(iterators[0].gi_frame)
                    self.assertEqual(int(state['packed'][0]),-2147483648 if mode=='single' else -1)
                finally:
                    for iterator in iterators: iterator.close()

    def test_staging_close_failure_does_not_mask_primary_audit_error(self):
        original = native_payload.iter_host_byte_blocks
        real_sha = hashlib.sha256
        class Digest:
            def update(self,data):
                del data
                raise RuntimeError('primary audit fault')
        for mode in ('single','independent'):
            with self.subTest(mode=mode):
                run, _ = self.audit(mode)
                closed = []
                def blocks(*args):
                    inner = original(*args)
                    try:
                        yield from inner
                    finally:
                        inner.close()
                        closed.append(True)
                        raise OSError('secondary staging close fault')
                with patch.object(native_payload,'iter_host_byte_blocks',side_effect=blocks), \
                        patch.object(native_payload.hashlib,'sha256',side_effect=lambda data=b'':real_sha(data) if data else Digest()):
                    with self.assertRaisesRegex(RuntimeError,'primary audit fault') as caught:
                        run()
                self.assertEqual(closed,[True])
                self.assertIn('secondary staging close fault',' '.join(caught.exception.__notes__))

    def fixture(self):
        index, state, args = test_native_spans.NativeSpanTests().fixture()
        state['packed'].copy_(torch.tensor([-2147483648, -1], dtype=torch.int32))
        state['scale'].view(torch.int16).copy_(torch.tensor([-32768, 32705], dtype=torch.int16))
        records = {n: dict(bytes=index[n][1], sha256=hashlib.sha256(
            b''.join(native_payload.iter_host_byte_blocks(t, 3))).hexdigest()) for n, t in state.items()}
        return index, state, records, args

    def test_small_blocks_preserve_mixed_bits_and_alias_without_duplicate_copy(self):
        index, state, records, args = self.fixture()
        result = native_payload.verify_native_payload(index, state, records, chunk_bytes=3, **args)
        self.assertEqual(result['host_payload_copy_bytes'], 12)
        self.assertEqual(result['verified_digests'], {n: r['sha256'] for n, r in records.items()})
        self.assertTrue(result['payload_verified'])
        self.assertEqual(result['payload_device'], 'cpu')
        self.assertFalse(result['native_loader_verified'])

    def test_late_bad_record_alias_or_span_rejected_before_first_copy(self):
        for kind in ('digest', 'bytes', 'alias', 'span', 'bound'):
            index, state, records, args = self.fixture()
            bound = 3
            if kind == 'digest':
                records['scale']['sha256'] = 'invalid'
            elif kind == 'bytes':
                records['scale']['bytes'] = True
            elif kind == 'alias':
                records['alias']['sha256'] = '0' * 64
            elif kind == 'span':
                args['allocation_bytes'] = 4
            else:
                bound = True
            with patch.object(native_payload, 'iter_host_byte_blocks', side_effect=AssertionError('unexpected copy')):
                with self.assertRaises(ValueError):
                    native_payload.verify_native_payload(index, state, records, chunk_bytes=bound, **args)

    def test_changed_payload_never_returns_success(self):
        index, state, records, args = self.fixture()
        state['scale'].view(torch.int16)[0] ^= 1
        with self.assertRaisesRegex(ValueError, 'digest differs: scale'):
            native_payload.verify_native_payload(index, state, records, chunk_bytes=3, **args)

    def test_host_blocks_are_raw_copies_and_release_between_iterations(self):
        tensor = torch.tensor([-1, -2147483648], dtype=torch.int32)
        blocks = native_payload.iter_host_byte_blocks(tensor, 3)
        first = next(blocks)
        owner = weakref.ref(first.obj._tensor_owner)
        self.assertNotEqual(first.obj._tensor_owner.data_ptr(), tensor.data_ptr())
        first[0] = 0
        self.assertEqual(int(tensor[0]), -1)
        del first
        second = next(blocks)
        self.assertIsNone(owner())
        del second
        blocks.close()

    def test_empty_scalar_and_unsupported_lazy_view(self):
        self.assertEqual(list(native_payload.iter_host_byte_blocks(torch.empty(0, dtype=torch.int32), 1)), [])
        scalar = torch.tensor(-1, dtype=torch.int32)
        self.assertEqual(b''.join(native_payload.iter_host_byte_blocks(scalar, 1)), b'\xff' * 4)
        with self.assertRaises(ValueError):
            list(native_payload.iter_host_byte_blocks(torch._neg_view(scalar)))


if __name__ == '__main__':
    unittest.main()
