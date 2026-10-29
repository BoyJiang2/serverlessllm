from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

from buffer_codec import CODEC, VISION_CACHE
import cpu_restore
import test_buffer_codec


class LazyRestoreTests(unittest.TestCase):
    def pair(self, alias=False):
        helper = test_buffer_codec.BufferCodecTests()
        source, target = helper.model(), helper.model()
        if not alias:
            del source.vision_tower.encoder.rope_2d.cache_alias
            del target.vision_tower.encoder.rope_2d.cache_alias
        del target.vision_tower.encoder.rope_2d.freqs_cis
        target.packed.data.zero_()
        codecs = test_buffer_codec.CODECS if alias else {VISION_CACHE: CODEC}
        return helper, source, target, codecs

    def test_create_cache_preserves_bits_roles_and_existing_parameter(self):
        for alias in (False, True):
            helper, source, target, codecs = self.pair(alias)
            original = target.packed
            if alias:
                target.vision_tower.encoder.rope_2d.cache_alias.view(torch.int32).zero_()
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                digest = helper.export(directory, source, codecs)
                receipt = cpu_restore.restore_cpu_state(directory, target, digest,
                    rank=0, chunk_bytes=7, create_lazy_visual_cache=True)
            cache = target.vision_tower.encoder.rope_2d.freqs_cis
            self.assertTrue(torch.equal(cache.view(torch.int32),
                source.vision_tower.encoder.rope_2d.freqs_cis.view(torch.int32)))
            self.assertTrue(torch.equal(target.packed, source.packed))
            self.assertIs(target.packed, original)
            self.assertFalse(cache.requires_grad)
            self.assertNotIn(VISION_CACHE, target.state_dict())
            self.assertEqual(receipt['created_lazy_buffers'], [VISION_CACHE])
            self.assertFalse(receipt['native_loader_verified'])
            if alias:
                self.assertEqual(cache.data_ptr(), target.vision_tower.encoder.rope_2d.cache_alias.data_ptr())

    def test_default_missing_or_occupied_name_refused_before_copy(self):
        for kind in ('default', 'occupied', 'owner'):
            helper, source, target, codecs = self.pair()
            if kind == 'occupied':
                target.vision_tower.encoder.rope_2d.freqs_cis = None
            elif kind == 'owner':
                del target.vision_tower.encoder.rope_2d
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                digest = helper.export(directory, source, codecs)
                with patch.object(cpu_restore, '_restore_tensor', side_effect=AssertionError('unexpected copy')):
                    with self.assertRaises((ValueError, AttributeError)):
                        cpu_restore.restore_cpu_state(directory, target, digest, rank=0,
                            create_lazy_visual_cache=kind != 'default')
            self.assertTrue(torch.equal(target.packed, torch.zeros_like(target.packed)))

    def test_persistent_source_cache_refused_before_allocation(self):
        helper, source, target, codecs = self.pair()
        source.vision_tower.encoder.rope_2d._non_persistent_buffers_set.remove('freqs_cis')
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = helper.export(directory, source, codecs)
            with patch.object(torch, 'empty', side_effect=AssertionError('unexpected allocation')):
                with self.assertRaisesRegex(ValueError, 'missing target'):
                    cpu_restore.restore_cpu_state(directory, target, digest,
                        rank=0, create_lazy_visual_cache=True)

    def test_corrupt_payload_refused_before_cache_allocation_or_copy(self):
        helper, source, target, codecs = self.pair()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = helper.export(directory, source, codecs)
            with (directory / 'tensor.data_0').open('r+b') as stream:
                stream.write(b'bad!')
            with patch.object(torch, 'empty', side_effect=AssertionError('unexpected allocation')), \
                    patch.object(cpu_restore, '_restore_tensor', side_effect=AssertionError('unexpected copy')):
                with self.assertRaises(ValueError):
                    cpu_restore.restore_cpu_state(directory, target, digest,
                        rank=0, create_lazy_visual_cache=True)
        self.assertFalse(hasattr(target.vision_tower.encoder.rope_2d, 'freqs_cis'))

    def test_copy_failure_never_registers_lazy_cache(self):
        helper, source, target, codecs = self.pair()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = helper.export(directory, source, codecs)
            with patch.object(cpu_restore, '_restore_tensor', side_effect=ValueError('destination invalid')):
                with self.assertRaisesRegex(ValueError, 'destination invalid'):
                    cpu_restore.restore_cpu_state(directory, target, digest,
                        rank=0, create_lazy_visual_cache=True)
        self.assertFalse(hasattr(target.vision_tower.encoder.rope_2d, 'freqs_cis'))


if __name__ == '__main__':
    unittest.main()
