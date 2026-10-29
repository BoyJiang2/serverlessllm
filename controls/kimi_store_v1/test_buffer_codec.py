import hashlib
import copy
import json
from pathlib import Path
import tempfile
import unittest

import torch

from buffer_codec import CODEC, VISION_CACHE, decode_cpu_buffers, encode_cpu_state
import contract
from cpu_restore import restore_cpu_state
from cpu_tensor_io import write_cpu_state
import native_index
from state_audit import compare_restored_state, fingerprint_state
import test_cpu_tensor_io
import test_vision_state

CODECS = {VISION_CACHE: CODEC, 'vision_tower.encoder.rope_2d.cache_alias': CODEC}


class BufferCodecTests(unittest.TestCase):
    def model(self):
        model = torch.nn.Module()
        model.vision_tower = torch.nn.Module()
        model.vision_tower.encoder = torch.nn.Module()
        model.vision_tower.encoder.rope_2d = torch.nn.Module()
        # NaN payload, signed zero and arbitrary real/imag words; never numeric casts.
        bits = torch.tensor([0, -2147483648, 0x7fc00001, -1, 0x7f800000, 1, -7, 42], dtype=torch.int32)
        buffer = bits.view(torch.complex64).reshape(2, 1, 2)
        model.vision_tower.encoder.rope_2d.register_buffer('freqs_cis', buffer, persistent=False)
        model.vision_tower.encoder.rope_2d.register_buffer('cache_alias', buffer, persistent=False)
        model.packed = torch.nn.Parameter(torch.tensor([12, -2], dtype=torch.int32), requires_grad=False)
        return model

    def export(self, directory, model, codecs):
        records, layouts, info = write_cpu_state(directory, model, chunk_bytes=7, buffer_codecs=codecs)
        inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                  for n, r in layouts.items()}
        plan = contract.plan_rank(test_cpu_tensor_io.CONFIG, rank=0, tensor_parallel_size=8,
            revision=contract.REVISION, tensors=inputs, vllm_version='0.19.1', torch_version='2.10.0')
        native_index.publish_index(directory, plan, records, layouts, test_cpu_tensor_io.CONFIG,
            aliases=info['alias_of'], buffer_encodings=info['buffer_encodings'], state_bindings=info['state_bindings'])
        return hashlib.sha256((directory / 'kimi_raw_state.json').read_bytes()).hexdigest()

    def test_raw_bit_roundtrip_alias_and_zero_copy_decode(self):
        source, destination = self.model(), self.model()
        destination.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32).zero_()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.export(directory, source, CODECS)
            restore_cpu_state(directory, destination, digest, rank=0, chunk_bytes=7, buffer_codecs=CODECS)
            a = fingerprint_state(source, rank=0, phase='before_postprocess', buffer_codecs=CODECS)
            b = fingerprint_state(destination, rank=0, phase='before_postprocess', buffer_codecs=CODECS)
            self.assertTrue(compare_restored_state(a, b)['same_phase_cpu_bytes_equal'])
            named, _, encodings = encode_cpu_state(source, CODECS)
            decoded = decode_cpu_buffers(dict(named), encodings)
            buffer = source.vision_tower.encoder.rope_2d.freqs_cis
            self.assertEqual(decoded[VISION_CACHE].dtype, torch.complex64)
            self.assertEqual(decoded[VISION_CACHE].data_ptr(), buffer.data_ptr())
            self.assertTrue(torch.equal(decoded[VISION_CACHE].view(torch.int32), buffer.view(torch.int32)))
            self.assertEqual(decoded[VISION_CACHE].data_ptr(), decoded['vision_tower.encoder.rope_2d.cache_alias'].data_ptr())

    def test_missing_codec_or_parameter_codec_rejected_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            for codecs in (None, {'packed': CODEC}, {VISION_CACHE: 'unknown'}):
                with self.assertRaises(ValueError):
                    write_cpu_state(directory, self.model(), buffer_codecs=codecs)
                self.assertFalse(directory.exists())

    def test_missing_manifest_rejected_even_with_new_sidecar_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            self.export(directory, self.model(), CODECS)
            path = directory / 'kimi_raw_state.json'
            data = json.loads(path.read_bytes())
            del data['buffer_encodings']
            raw = json.dumps(data).encode('utf-8')
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'requires its encoding'):
                native_index.read_index(directory, hashlib.sha256(raw).hexdigest())

    def test_changed_original_shape_and_corrupt_payload_rejected(self):
        for kind in ('shape', 'payload'):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                digest = self.export(directory, self.model(), CODECS)
                if kind == 'shape':
                    path = directory / 'kimi_raw_state.json'
                    data = json.loads(path.read_bytes())
                    data['buffer_encodings'][VISION_CACHE]['original']['shape'] = [4]
                    raw = json.dumps(data).encode('utf-8')
                    path.write_bytes(raw)
                    digest = hashlib.sha256(raw).hexdigest()
                else:
                    with (directory / 'tensor.data_0').open('r+b') as stream:
                        stream.seek(8)
                        stream.write(b'bad!')
                with self.assertRaises(ValueError):
                    restore_cpu_state(directory, self.model(), digest, rank=0, buffer_codecs=CODECS)

    def test_conjugate_partial_and_cross_dtype_alias_are_rejected(self):
        for kind in ('conjugate', 'partial', 'cross_dtype'):
            model = self.model()
            rope = model.vision_tower.encoder.rope_2d
            if kind == 'conjugate':
                rope.freqs_cis = rope.freqs_cis.conj()
            elif kind == 'partial':
                rope.freqs_cis = rope.freqs_cis[:1]
            else:
                model.cross = torch.nn.Parameter(rope.freqs_cis.reshape(-1).view(torch.int32), requires_grad=False)
            with self.assertRaises(ValueError):
                encode_cpu_state(model, CODECS)

    def test_actual_small_source_rope_cache_roundtrip(self):
        source, destination = test_vision_state.VisionStateTests().rope(), test_vision_state.VisionStateTests().rope()
        grid = torch.tensor([[1, 2, 3]])
        source.get_freqs_cis(grid, torch.device('cpu'))
        destination.get_freqs_cis(grid, torch.device('cpu'))
        destination.freqs_cis.view(torch.int32).zero_()
        codecs = {'freqs_cis': CODEC}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.export(directory, source, codecs)
            restore_cpu_state(directory, destination, digest, rank=0, buffer_codecs=codecs)
            self.assertTrue(torch.equal(source.freqs_cis.view(torch.int32), destination.freqs_cis.view(torch.int32)))
            self.assertNotIn('freqs_cis', destination.state_dict())

    def test_alias_recipe_mismatch_rejected_by_publisher_reader_and_decoder(self):
        alias = 'vision_tower.encoder.rope_2d.cache_alias'
        for kind in ('shape', 'single_codec'):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                model = self.model()
                records, layouts, info = write_cpu_state(directory, model, buffer_codecs=CODECS)
                inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                          for n, r in layouts.items()}
                plan = contract.plan_rank(test_cpu_tensor_io.CONFIG, rank=0, tensor_parallel_size=8,
                    revision=contract.REVISION, tensors=inputs, vllm_version='0.19.1', torch_version='2.10.0')
                changed = copy.deepcopy(info['buffer_encodings'])
                if kind == 'shape':
                    changed[alias]['original'].update(shape=[4], stride=[1])
                else:
                    del changed[alias]
                with self.assertRaisesRegex(ValueError, 'alias buffer encoding'):
                    native_index.publish_index(directory, plan, records, layouts, test_cpu_tensor_io.CONFIG,
                        aliases=info['alias_of'], buffer_encodings=changed, state_bindings=info['state_bindings'])
                self.assertFalse((directory / 'tensor_index.json').exists())
                native_index.publish_index(directory, plan, records, layouts, test_cpu_tensor_io.CONFIG,
                    aliases=info['alias_of'], buffer_encodings=info['buffer_encodings'], state_bindings=info['state_bindings'])
                path = directory / 'kimi_raw_state.json'
                sidecar = json.loads(path.read_bytes())
                sidecar['buffer_encodings'] = changed
                raw = json.dumps(sidecar).encode('utf-8')
                path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'alias buffer encoding'):
                    native_index.read_index(directory, hashlib.sha256(raw).hexdigest())
                named, _, _ = encode_cpu_state(model, CODECS)
                with self.assertRaisesRegex(ValueError, 'alias buffer encoding'):
                    decode_cpu_buffers(dict(named), changed)


if __name__ == '__main__':
    unittest.main()
