import hashlib
from pathlib import Path
import tempfile
import unittest
import weakref
from unittest.mock import patch

import torch

import device_export
from cpu_restore import restore_cpu_state
from marlin_aux import PROFILE
from native_index import read_index
from state_audit import compare_restored_state, fingerprint_state
import test_buffer_codec
import test_cpu_tensor_io


class DeviceExportTests(unittest.TestCase):
    def options(self):
        return dict(device='cpu', config_raw=test_cpu_tensor_io.CONFIG, checkpoint_contract=b'fixture',
            rank=0, profile=PROFILE, vllm_version='0.19.1', torch_version='2.10.0',
            phase='checkpoint_loaded_before_postprocess', chunk_bytes=7)

    def test_bounded_writer_releases_previous_host_block_and_deduplicates_aliases(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        real = device_export.iter_host_byte_blocks
        previous = None
        sizes = []
        def blocks(tensor, limit):
            nonlocal previous
            for block in real(tensor, limit):
                if previous is not None:
                    self.assertIsNone(previous())
                previous = weakref.ref(block.obj._tensor_owner)
                self.assertEqual(len(block) % tensor.element_size(), 0)
                sizes.append(len(block))
                yield block
                del block
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(device_export, 'iter_host_byte_blocks', side_effect=blocks):
                records, layouts, info = device_export.write_device_state(
                    Path(temp) / 'rank', source, device='cpu', rank=0, chunk_bytes=7)
        self.assertIsNone(previous())
        self.assertTrue(all(0 < size <= 7 for size in sizes))
        self.assertEqual(sum(sizes), 44)
        self.assertEqual(info['written_payload_bytes'], 44)
        self.assertEqual(info['alias_of'], {'packed_alias': 'packed'})
        self.assertEqual(records['packed'], records['packed_alias'])

    def test_partial_file_write_failure_releases_real_staging_copy_without_clearing_traceback(self):
        source=test_cpu_tensor_io.TensorIOTests().model()
        original_bits=bytes(source.packed.detach().view(torch.uint8).reshape(-1).tolist())
        producers=[];owners=[]
        real_blocks=device_export.iter_host_byte_blocks
        def blocks(tensor,limit):
            producer=real_blocks(tensor,limit);producers.append(producer);return producer
        real_open=Path.open
        class FailingFile:
            def __init__(self,stream):self.stream=stream;self.calls=0
            def __getattr__(self,name):return getattr(self.stream,name)
            def write(self,data):
                self.calls+=1
                if self.calls==1:return self.stream.write(data[:2])
                owners.append(weakref.ref(data.obj._tensor_owner))
                del data  # The synthetic Python callback must not anchor the buffer itself.
                raise OSError('controlled partial device export write')
        def opened(path,*args,**kwargs):
            stream=real_open(path,*args,**kwargs)
            return FailingFile(stream) if path.name=='tensor.data_0' and args[0]=='xb' else stream
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'rank';failure=None
            with patch.object(device_export,'iter_host_byte_blocks',side_effect=blocks),patch.object(Path,'open',opened):
                try:device_export.write_device_state(target,source,device='cpu',rank=0,chunk_bytes=8)
                except OSError as error:failure=error
            # Mock call_args records only the source tensor, not the produced staging block.
            self.assertIsNotNone(failure);self.assertIsNotNone(failure.__traceback__)
            self.assertEqual(len(owners),1);self.assertIsNone(owners[0]())
            self.assertIsNone(producers[0].gi_frame)
            self.assertEqual((target/'tensor.data_0').read_bytes(),original_bits[:2])
            self.assertFalse((target/'tensor_index.json').exists())
            self.assertEqual(bytes(source.packed.detach().view(torch.uint8).reshape(-1).tolist()),original_bits)

    def test_rank_export_publishes_and_restores_mixed_raw_bits(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        target = test_cpu_tensor_io.TensorIOTests().model()
        target.packed.data.zero_()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            # Fixture mocks rank/name policy only; writer/publisher/readback/restore execute.
            with patch.object(device_export, 'normalize_rank_cpu_auxiliary', return_value=[]), \
                    patch.object(device_export, 'load_targets', return_value={'packed', 'scale'}):
                receipt = device_export.export_rank_device(directory, source, **self.options())
            digest = hashlib.sha256((directory / 'kimi_raw_state.json').read_bytes()).hexdigest()
            self.assertEqual(set(read_index(directory, digest)), set(receipt['archive']['records']))
            restore_cpu_state(directory, target, digest, rank=0, chunk_bytes=7)
            a = fingerprint_state(source, rank=0, phase='before_postprocess')
            b = fingerprint_state(target, rank=0, phase='before_postprocess')
            self.assertTrue(compare_restored_state(a, b)['same_phase_cpu_bytes_equal'])
            self.assertFalse(receipt['archive']['store_created'])
            self.assertEqual(receipt['transfer']['source_device'], 'cpu')

    def test_complex_encoding_and_aliases_preserved_by_device_writer(self):
        source = test_buffer_codec.BufferCodecTests().model()
        with tempfile.TemporaryDirectory() as temp:
            records, layouts, info = device_export.write_device_state(Path(temp) / 'rank', source,
                device='cpu', rank=0, chunk_bytes=7, buffer_codecs=test_buffer_codec.CODECS)
        name = 'vision_tower.encoder.rope_2d.freqs_cis'
        self.assertEqual(layouts[name]['dtype'], 'torch.int32')
        self.assertEqual(info['buffer_encodings'][name]['original']['dtype'], 'torch.complex64')
        self.assertEqual(info['written_payload_bytes'], 40)
        self.assertEqual(records[name]['sha256'], hashlib.sha256(
            bytes(source.vision_tower.encoder.rope_2d.freqs_cis.reshape(-1).view(torch.uint8).tolist())).hexdigest())

    def test_invalid_phase_budget_or_existing_output_refused_before_normalization(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        for kind in ('phase', 'bound', 'exists'):
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                options = self.options()
                if kind == 'phase': options['phase'] = 'target_initialized_before_restore'
                if kind == 'bound': options['chunk_bytes'] = True
                if kind == 'exists': directory.mkdir()
                with patch.object(device_export, 'normalize_rank_cpu_auxiliary', side_effect=AssertionError('unexpected mutation')):
                    with self.assertRaises((ValueError, FileExistsError)):
                        device_export.export_rank_device(directory, source, **options)
                self.assertFalse((directory / 'tensor_index.json').exists())

    def test_transfer_failure_keeps_partial_output_without_index(self):
        source = test_cpu_tensor_io.TensorIOTests().model()
        real = device_export.iter_host_byte_blocks
        def failed(tensor, bound):
            for block in real(tensor, bound):
                yield block
                raise RuntimeError('fixture transfer failure')
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            with patch.object(device_export, 'iter_host_byte_blocks', side_effect=failed):
                with self.assertRaisesRegex(RuntimeError, 'transfer failure'):
                    device_export.write_device_state(directory, source, device='cpu', rank=0, chunk_bytes=7)
            self.assertTrue(directory.exists())
            self.assertFalse((directory / 'tensor_index.json').exists())


if __name__ == '__main__':
    unittest.main()
