import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import weakref

import torch

from buffer_codec import CODEC, VISION_CACHE, encode_cpu_state
import contract
from cpu_tensor_io import write_cpu_state
from install_state import install_cpu_loaded_state
import native_index
from state_audit import compare_restored_state, fingerprint_state
import test_buffer_codec
import test_cpu_tensor_io


class InstallTests(unittest.TestCase):
    def export(self, directory, model, codecs=None):
        records, layouts, info = write_cpu_state(directory, model, buffer_codecs=codecs)
        inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                  for n, r in layouts.items()}
        plan = contract.plan_rank(test_cpu_tensor_io.CONFIG, rank=0, tensor_parallel_size=8,
            revision=contract.REVISION, tensors=inputs, vllm_version='0.19.1', torch_version='2.10.0')
        native_index.publish_index(directory, plan, records, layouts, test_cpu_tensor_io.CONFIG,
            aliases=info['alias_of'], buffer_encodings=info['buffer_encodings'], state_bindings=info['state_bindings'])
        digest = hashlib.sha256((directory / 'kimi_raw_state.json').read_bytes()).hexdigest()
        named, _, _ = encode_cpu_state(model, codecs)
        return dict(named), digest

    def test_preserves_parameter_subclass_attributes_and_mixed_bits(self):
        source, target = test_cpu_tensor_io.TensorIOTests().model(), test_cpu_tensor_io.TensorIOTests().model()
        class LoaderParameter(torch.nn.Parameter):
            pass
        target.packed = LoaderParameter(torch.zeros_like(target.packed), requires_grad=False)
        marker = object()
        target.packed.loader_marker = marker
        original = target.packed
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest = self.export(directory, source)
            receipt = install_cpu_loaded_state(directory, target, state, digest, rank=0)
            self.assertIs(target.packed, original)
            self.assertIs(target.packed.loader_marker, marker)
            self.assertIsInstance(target.packed, LoaderParameter)
            self.assertEqual(target.packed.data_ptr(), target.packed_alias.data_ptr())
            a = fingerprint_state(source, rank=0, phase='before_postprocess')
            b = fingerprint_state(target, rank=0, phase='before_postprocess')
            self.assertEqual(a['tensors'], b['tensors'])
            comparison = compare_restored_state(a, b)
            self.assertFalse(comparison['inventory_equal'])  # Target-only opaque loader marker is now visible.
            self.assertFalse(comparison['same_phase_cpu_bytes_equal'])
            self.assertIn(dict(path='packed.tensor_attrs.loader_marker', type='builtins.object'),
                          b['inventory']['uninspected_objects'])
            self.assertFalse(b['inventory']['opaque_container_audit_complete'])
            self.assertNotIn('empty_scratch', target.state_dict())
            self.assertFalse(receipt['postprocessing_performed'])
            self.assertFalse(receipt['runtime_admitted'])

    def test_existing_complex_buffers_decode_and_release_old_buffer_objects(self):
        source, target = test_buffer_codec.BufferCodecTests().model(), test_buffer_codec.BufferCodecTests().model()
        previous = weakref.ref(target.vision_tower.encoder.rope_2d.freqs_cis)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest = self.export(directory, source, test_buffer_codec.CODECS)
            install_cpu_loaded_state(directory, target, state, digest, rank=0)
            self.assertIsNone(previous())
            self.assertEqual(target.vision_tower.encoder.rope_2d.freqs_cis.dtype, torch.complex64)
            a = fingerprint_state(source, rank=0, phase='before_postprocess', buffer_codecs=test_buffer_codec.CODECS)
            b = fingerprint_state(target, rank=0, phase='before_postprocess', buffer_codecs=test_buffer_codec.CODECS)
            self.assertTrue(compare_restored_state(a, b)['same_phase_cpu_bytes_equal'])

    def test_missing_lazy_visual_cache_registered_nonpersistently(self):
        source, target = test_buffer_codec.BufferCodecTests().model(), test_buffer_codec.BufferCodecTests().model()
        del source.vision_tower.encoder.rope_2d.cache_alias
        del target.vision_tower.encoder.rope_2d.cache_alias
        del target.vision_tower.encoder.rope_2d.freqs_cis
        codecs = {VISION_CACHE: CODEC}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest = self.export(directory, source, codecs)
            receipt = install_cpu_loaded_state(directory, target, state, digest, rank=0)
            self.assertEqual(receipt['created_lazy_buffers'], [VISION_CACHE])
            self.assertNotIn(VISION_CACHE, target.state_dict())
            self.assertEqual(target.vision_tower.encoder.rope_2d.freqs_cis.dtype, torch.complex64)

    def test_wrong_roles_persistence_corrupt_state_or_custom_buffer_refused_before_install(self):
        for kind in ('role', 'persistence', 'payload', 'custom'):
            source, target = test_cpu_tensor_io.TensorIOTests().model(), test_cpu_tensor_io.TensorIOTests().model()
            if kind == 'role':
                tensor = target.router_bias
                del target.router_bias
                target.router_bias = torch.nn.Parameter(tensor, requires_grad=False)
            elif kind == 'persistence':
                target._non_persistent_buffers_set.remove('empty_scratch')
            elif kind == 'custom':
                target.router_bias.hidden = object()
            original = target.packed
            before = target.packed.detach().clone()
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                state, digest = self.export(directory, source)
                if kind == 'payload':
                    source.packed.detach()[0] ^= 1
                with self.assertRaises(ValueError):
                    install_cpu_loaded_state(directory, target, state, digest, rank=0)
                self.assertIs(target.packed, original)
                self.assertTrue(torch.equal(target.packed, before))

    def test_missing_binding_manifest_and_unsafe_tied_target_refused(self):
        source, target = torch.nn.Module(), torch.nn.Module()
        source.a = torch.nn.Parameter(torch.ones(1), requires_grad=False)
        source.b = torch.nn.Parameter(torch.zeros(1), requires_grad=False)
        target.a = torch.nn.Parameter(torch.ones(1), requires_grad=False)
        target.b = target.a
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest = self.export(directory, source)
            with self.assertRaisesRegex(ValueError, 'tied target'):
                install_cpu_loaded_state(directory, target, state, digest, rank=0)
            path = directory / 'kimi_raw_state.json'
            data = json.loads(path.read_bytes())
            data['state_bindings'] = None
            raw = json.dumps(data).encode('utf-8')
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'binding manifest required'):
                install_cpu_loaded_state(directory, target, state, hashlib.sha256(raw).hexdigest(), rank=0)

    def test_buffer_grad_flags_preserved_for_shared_storage_and_plain_loaded_tensors(self):
        source, target = torch.nn.Module(), torch.nn.Module()
        for model in (source, target):
            buffer = torch.ones(2, requires_grad=True)
            model.register_buffer('trainable', buffer, persistent=False)
            model.register_buffer('alias', buffer.detach(), persistent=False)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            state, digest = self.export(directory, source)
            state = {n: t.detach() for n, t in state.items()}
            self.assertTrue(all(not t.requires_grad for t in state.values()))
            install_cpu_loaded_state(directory, target, state, digest, rank=0)
            self.assertTrue(target.trainable.requires_grad)
            self.assertFalse(target.alias.requires_grad)
            self.assertIsNot(target.trainable, target.alias)
            self.assertEqual(target.trainable.data_ptr(), target.alias.data_ptr())

    def test_custom_source_buffer_attributes_rejected_before_output_creation(self):
        model = torch.nn.Module()
        model.register_buffer('buffer', torch.ones(2))
        model.buffer.hidden = object()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            with self.assertRaisesRegex(ValueError, 'custom buffer'):
                write_cpu_state(directory, model)
            self.assertFalse(directory.exists())


if __name__ == '__main__':
    unittest.main()
