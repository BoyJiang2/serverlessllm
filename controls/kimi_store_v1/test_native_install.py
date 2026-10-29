import json
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

import torch

from buffer_codec import CODEC, VISION_CACHE
import native_install
import test_buffer_codec
import test_cpu_tensor_io
import test_install_state


class NativeInstallTests(unittest.TestCase):
    def native_fixture(self, directory, source, codecs=None):
        original, digest = test_install_state.InstallTests().export(directory, source, codecs)
        index = json.loads((directory / 'tensor_index.json').read_text())
        total = max(row[0] + row[1] for row in index.values()) + 8
        memory = bytearray(total)
        base = torch.frombuffer(memory, dtype=torch.uint8).data_ptr()
        state = {}
        for name, (offset, size, shape, stride, dtype) in index.items():
            target_dtype = getattr(torch, dtype.split('.')[1])
            if size:
                tensor = torch.frombuffer(memory, dtype=target_dtype,
                    count=original[name].numel(), offset=offset).reshape(shape)
                tensor.copy_(original[name])
            else:
                tensor = torch.empty(shape, dtype=target_dtype)
            state[name] = tensor
        sidecar = json.loads((directory / 'kimi_raw_state.json').read_text())
        allocation = dict(device='cpu', base_address=base, allocation_bytes=total,
            device_offsets={n: row[0] for n, row in index.items()}, alias_of=sidecar['alias_of'])
        return state, digest, allocation

    def test_install_preserves_parameter_object_attributes_and_all_bits(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        target = test_cpu_tensor_io.TensorIOTests().model()
        target.packed.data.zero_()
        marker = object()
        target.packed.loader_marker = marker
        parameter = target.packed
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest, allocation = self.native_fixture(directory, source)
            receipt = native_install.install_native_state(directory, target, state, digest,
                rank=0, allocation=allocation, chunk_bytes=3)
            self.assertIs(target.packed, parameter)
            self.assertIs(target.packed.loader_marker, marker)
            self.assertEqual(target.packed.data_ptr(), target.packed_alias.data_ptr())
            for name, tensor in list(target.named_parameters(remove_duplicate=False)) + list(target.named_buffers(remove_duplicate=False)):
                from native_payload import iter_host_byte_blocks
                self.assertEqual(b''.join(iter_host_byte_blocks(tensor, 3)),
                                 b''.join(iter_host_byte_blocks(state[name], 3)))
            self.assertTrue(receipt['payload_verified'])
            self.assertFalse(receipt['native_loader_verified'])
            self.assertFalse(receipt['postprocessing_performed'])
            self.assertNotIn('empty_scratch', target.state_dict())
            owner = weakref.ref(state['packed'])
            state.clear()
            target.packed.data = torch.zeros_like(target.packed)
            self.assertIsNotNone(owner())

    def test_missing_complex_cache_decoded_and_registered_nonpersistently(self):
        helper = test_buffer_codec.BufferCodecTests()
        source, target = helper.model(), helper.model()
        del source.vision_tower.encoder.rope_2d.cache_alias
        del target.vision_tower.encoder.rope_2d.cache_alias
        del target.vision_tower.encoder.rope_2d.freqs_cis
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest, allocation = self.native_fixture(directory, source, {VISION_CACHE: CODEC})
            receipt = native_install.install_native_state(directory, target, state, digest,
                rank=0, allocation=allocation, chunk_bytes=3)
            actual = target.vision_tower.encoder.rope_2d.freqs_cis
            self.assertTrue(torch.equal(actual.view(torch.int32), source.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32)))
            self.assertEqual(actual.data_ptr(), state[VISION_CACHE].data_ptr())
            self.assertNotIn(VISION_CACHE, target.state_dict())
            self.assertEqual(receipt['created_lazy_buffers'], [VISION_CACHE])

    def test_bad_rank_roles_payload_or_alias_never_mutates_or_anchors(self):
        for kind in ('rank', 'roles', 'payload', 'alias', 'hidden'):
            source = test_cpu_tensor_io.TensorIOTests().model()
            target = test_cpu_tensor_io.TensorIOTests().model()
            target.packed.data.zero_()
            if kind == 'roles':
                target._non_persistent_buffers_set.remove('empty_scratch')
            if kind == 'hidden':
                target.extra = [torch.ones(1)]
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                state, digest, allocation = self.native_fixture(directory, source)
                if kind == 'payload':
                    state['packed'][0] ^= 1
                if kind == 'alias':
                    allocation['alias_of'] = {}
                with self.assertRaises(ValueError):
                    native_install.install_native_state(directory, target, state, digest,
                        rank=1 if kind == 'rank' else 0, allocation=allocation)
                self.assertFalse(hasattr(target, '_kimi_native_allocation_tensors'))
                self.assertTrue(torch.equal(target.packed, torch.zeros_like(target.packed)))

    def test_default_inventory_remains_cpu_and_cuda_requires_index(self):
        from runtime_inventory import collect_inventory
        target = test_cpu_tensor_io.TensorIOTests().model()
        self.assertFalse(collect_inventory(target)['payload_read'])
        for device in ('cuda', 'cuda:0', 'meta'):
            with self.assertRaises(ValueError):
                collect_inventory(target, device=device)

    def test_existing_anchor_refused_before_payload_copy(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        target = test_cpu_tensor_io.TensorIOTests().model()
        target._kimi_native_allocation_tensors = ()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest, allocation = self.native_fixture(directory, source)
            with patch.object(native_install, 'verify_native_payload', side_effect=AssertionError('unexpected copy')):
                with self.assertRaisesRegex(ValueError, 'fresh target'):
                    native_install.install_native_state(directory, target, state, digest,
                        rank=0, allocation=allocation)


if __name__ == '__main__':
    unittest.main()
