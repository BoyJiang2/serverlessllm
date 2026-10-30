import ctypes
import json
import math
from pathlib import Path
import tempfile
import types
import unittest
import weakref
from unittest.mock import patch

import torch

import native_payload
from native_install import install_native_state
from record_allocations import plan_independent_records, restore_independent_records
import test_cpu_tensor_io
import test_install_state
import test_vllm_cpu_hook


class IndependentInstallTests(unittest.TestCase):
    def test_pinned_cpu_postprocess_releases_record_owners_with_metadata_receipts_kept(self):
        for anchor_control in (False,True):
            with self.subTest(anchor_control=anchor_control),tempfile.TemporaryDirectory() as temp:
                source,result,buffers,digest=self.fixture(Path(temp)/'rank')
                target=test_cpu_tensor_io.TensorIOTests().model()
                target.packed_alias=torch.nn.Parameter(target.packed.clone(),requires_grad=False)
                owners={name:weakref.ref(buffers[name]) for name in ('packed','scale','router_bias')}
                receipt=install_native_state(Path(temp)/'rank',target,result.tensors,digest,rank=0,
                    allocation=dict(device='cpu',alias_of=result.plan['alias_of']),independent_records=result,
                    chunk_bytes=3)
                anchor=dict(result.tensors) if anchor_control else {}
                del result
                buffers.clear()
                events=[]
                _,namespace=test_vllm_cpu_hook.LifecycleTests().base(events)
                process=namespace['process_weights_after_loading']
                quant_base=process.__globals__['QuantizeMethodBase']
                attention_base=process.__globals__['Attention']
                check=self
                class RepackControl(quant_base):
                    def process_weights_after_loading(self,model):
                        check.assertTrue(torch.equal(model.packed,source.packed))
                        check.assertTrue(torch.equal(model.scale.view(torch.int16),source.scale.view(torch.int16)))
                        model.packed.data=torch.zeros_like(model.packed)
                        check.assertIsNotNone(owners['packed']())  # Distinct alias Parameter still owns storage.
                        model.packed_alias.data=torch.zeros_like(model.packed_alias)
                        if anchor_control:
                            check.assertIsNotNone(owners['packed']())
                            anchor.clear()  # Explicit retained-raw-table control, not production cleanup.
                        check.assertIsNone(owners['packed']())
                        check.assertIsNotNone(owners['scale']())
                        model.scale.data=torch.zeros_like(model.scale)
                        check.assertIsNone(owners['scale']())
                        check.assertIsNotNone(owners['router_bias']())
                        events.append('quant_cpu_control')
                class AttentionControl(attention_base):
                    def process_weights_after_loading(self,dtype):
                        check.assertIsNone(owners['packed']())
                        check.assertIsNone(owners['scale']())
                        events.append('attention_cpu_control')
                target.quant_method=RepackControl()
                target.attention=AttentionControl()
                # Actual pinned utility/device_loading_context CPU branch, no CUDA or Marlin kernel.
                process(target,types.SimpleNamespace(dtype=torch.bfloat16,quantization='compressed-tensors'),torch.device('cpu'))
                self.assertEqual(events,['quant_cpu_control','attention_cpu_control'])
                self.assertTrue(receipt['canonical_storage_owners_bound'])
                self.assertFalse(receipt['allocation_ownership_verified'])
                json.dumps(receipt,allow_nan=False)
                del target
                self.assertIsNone(owners['router_bias']())

    def fixture(self, directory):
        source = test_cpu_tensor_io.TensorIOTests().model()
        _, digest = test_install_state.InstallTests().export(directory, source)
        index = json.loads((directory / 'tensor_index.json').read_bytes())
        sidecar = json.loads((directory / 'kimi_raw_state.json').read_bytes())
        plan = plan_independent_records(index, sidecar['alias_of'])
        payload = (directory / 'tensor.data_0').read_bytes()
        class Buffer(bytearray):
            pass
        buffers, pointers = {}, {}
        create = ctypes.pythonapi.PyCapsule_New
        create.argtypes, create.restype = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p], ctypes.py_object
        for record in plan['records']:
            root, size = record['root'], record['bytes']
            if size:
                offset = index[root][0]
                buffers[root] = Buffer(payload[offset:offset+size])
                address = torch.frombuffer(buffers[root], dtype=torch.uint8).data_ptr()
                pointers[root] = create(address, None, None)
        def restore(meta, ptrs, offsets):
            root = next(iter(meta))
            shape, stride, dtype = meta[root]
            return {root: torch.frombuffer(buffers[root], dtype=getattr(torch, dtype.split('.')[1]),
                count=math.prod(shape)).reshape(shape)}
        result = restore_independent_records(index, plan, pointers, device_id=0, expected_device='cpu',
            transfer_confirmed=True, restore_tensors=restore,
            make_empty=lambda shape, dtype, device: torch.empty(shape,
                dtype=getattr(torch, dtype.split('.')[1]), device=device))
        return source, result, buffers, digest

    def test_install_preserves_bits_and_releases_only_replaced_record_storage(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            source, result, buffers, digest = self.fixture(directory)
            target = test_cpu_tensor_io.TensorIOTests().model()
            target.packed_alias = torch.nn.Parameter(target.packed.clone(), requires_grad=False)
            packed_owner, scale_owner = weakref.ref(buffers['packed']), weakref.ref(buffers['scale'])
            receipt = install_native_state(directory, target, result.tensors, digest, rank=0,
                allocation=dict(device='cpu', alias_of=result.plan['alias_of']), independent_records=result,
                chunk_bytes=3)
            self.assertTrue(torch.equal(source.packed, target.packed))
            self.assertTrue(torch.equal(source.scale.view(torch.int16), target.scale.view(torch.int16)))
            self.assertFalse(hasattr(target, '_kimi_native_allocation_tensors'))
            self.assertTrue(receipt['canonical_storage_owners_bound'])
            self.assertFalse(receipt['native_tensor_references_retained'])
            self.assertFalse(receipt['allocation_ownership_verified'])
            del result
            buffers.clear()
            target.packed.data = torch.zeros_like(target.packed)
            self.assertIsNotNone(packed_owner())
            target.packed_alias.data = torch.zeros_like(target.packed_alias)
            self.assertIsNone(packed_owner())
            self.assertIsNotNone(scale_owner())
            del target
            self.assertIsNone(scale_owner())

    def test_alias_or_payload_corruption_refused_without_target_mutation(self):
        for kind in ('alias', 'payload'):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                source, result, buffers, digest = self.fixture(directory)
                target = test_cpu_tensor_io.TensorIOTests().model()
                target.packed.data.zero_()
                if kind == 'alias':
                    result.tensors['packed_alias'] = result.tensors['packed'].clone()
                else:
                    result.tensors['scale'].view(torch.int16)[0] ^= 1
                with self.assertRaises(ValueError):
                    install_native_state(directory, target, result.tensors, digest, rank=0,
                        allocation=dict(device='cpu', alias_of=result.plan['alias_of']), independent_records=result)
                self.assertFalse(hasattr(target, '_kimi_independent_records_bound'))
                self.assertTrue(torch.equal(target.packed, torch.zeros_like(target.packed)))

    def test_late_bad_metadata_rejected_before_any_host_copy(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            source, result, buffers, digest = self.fixture(directory)
            result.tensors['weight_shape'] = result.tensors['weight_shape'].clone()
            with patch.object(native_payload, 'iter_host_byte_blocks', side_effect=AssertionError('unexpected copy')):
                with self.assertRaisesRegex(ValueError, 'address differs'):
                    install_native_state(directory, test_cpu_tensor_io.TensorIOTests().model(),
                        result.tensors, digest, rank=0,
                        allocation=dict(device='cpu', alias_of=result.plan['alias_of']), independent_records=result)


if __name__ == '__main__':
    unittest.main()
