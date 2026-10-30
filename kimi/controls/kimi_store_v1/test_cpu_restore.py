import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import torch

import contract
import cpu_restore
from cpu_tensor_io import write_cpu_state
import native_index
from state_audit import compare_restored_state, fingerprint_state
import test_cpu_tensor_io

CONFIG = test_cpu_tensor_io.CONFIG


class RestoreTests(unittest.TestCase):
    def model(self):
        return test_cpu_tensor_io.TensorIOTests().model()

    def prepare(self, directory, source, rank=0):
        records, layouts, info = write_cpu_state(directory, source, chunk_bytes=7)
        inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                  for n, r in layouts.items()}
        plan = contract.plan_rank(CONFIG, rank=rank, tensor_parallel_size=8, revision=contract.REVISION,
                                  tensors=inputs, vllm_version='0.19.1', torch_version='2.10.0')
        native_index.publish_index(directory, plan, records, layouts, CONFIG, aliases=info['alias_of'])
        return hashlib.sha256((directory / 'kimi_raw_state.json').read_bytes()).hexdigest()

    def snapshot(self, model, rank=0):
        return fingerprint_state(model, rank=rank, phase='before_postprocess', chunk_bytes=4)

    def test_bounded_restore_into_existing_model_all_eight_rank_labels(self):
        for rank in range(8):
            source, destination = self.model(), self.model()
            for tensor in destination.parameters():
                tensor.data.zero_()
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                digest = self.prepare(directory, source, rank)
                result = cpu_restore.restore_cpu_state(directory, destination, digest, rank=rank, chunk_bytes=7)
                comparison = compare_restored_state(self.snapshot(source, rank), self.snapshot(destination, rank))
                self.assertTrue(comparison['same_phase_cpu_bytes_equal'])
                self.assertIs(destination.packed, destination.packed_alias)
                self.assertEqual(result['restored_payload_bytes'], 44)
                self.assertFalse(result['native_loader_verified'])

    def test_bad_rank_alias_shape_or_chunk_rejected_before_copy(self):
        for kind in ('rank', 'alias', 'shape', 'chunk'):
            source, destination = self.model(), self.model()
            if kind == 'alias':
                destination.packed_alias = torch.nn.Parameter(destination.packed.clone(), requires_grad=False)
            if kind == 'shape':
                destination.scale = torch.nn.Parameter(torch.zeros(5, dtype=torch.bfloat16), requires_grad=False)
            before = self.snapshot(destination)
            with tempfile.TemporaryDirectory() as temp:
                directory = Path(temp) / 'rank'
                digest = self.prepare(directory, source)
                with self.assertRaises(ValueError):
                    cpu_restore.restore_cpu_state(directory, destination, digest,
                        rank=1 if kind == 'rank' else 0, chunk_bytes=True if kind == 'chunk' else 7)
                self.assertTrue(compare_restored_state(before, self.snapshot(destination))['same_phase_cpu_bytes_equal'])

    def test_corrupt_later_record_detected_before_any_copy(self):
        source, destination = self.model(), self.model()
        destination.packed.data.zero_()
        before = self.snapshot(destination)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.prepare(directory, source)
            with (directory / 'tensor.data_0').open('r+b') as stream:
                stream.seek(24)
                stream.write(b'bad!')
            with self.assertRaises(ValueError):
                cpu_restore.restore_cpu_state(directory, destination, digest, rank=0)
            self.assertTrue(compare_restored_state(before, self.snapshot(destination))['same_phase_cpu_bytes_equal'])

    def test_change_between_verification_and_copy_fails_without_success_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.prepare(directory, self.model())
            original = cpu_restore._restore_tensor
            def changed(stream, tensor, chunk_bytes):
                with (directory / 'tensor.data_0').open('r+b') as writer:
                    writer.write(b'bad!')
                return original(stream, tensor, chunk_bytes)
            with patch.object(cpu_restore, '_restore_tensor', side_effect=changed):
                with self.assertRaisesRegex(ValueError, 'destination invalid'):
                    cpu_restore.restore_cpu_state(directory, self.model(), digest, rank=0)

    def test_short_reads_and_truncation(self):
        class ShortReads(io.BytesIO):
            def readinto(self, buffer):
                return super().readinto(buffer[:3])
        tensor = torch.zeros(3, dtype=torch.int32)
        raw = bytes(range(12))
        self.assertEqual(cpu_restore._restore_tensor(ShortReads(raw), tensor, 7), hashlib.sha256(raw).hexdigest())
        self.assertEqual(bytes(tensor.view(torch.uint8).tolist()), raw)
        with self.assertRaisesRegex(ValueError, 'destination invalid'):
            cpu_restore._restore_tensor(ShortReads(raw[:7]), tensor, 7)

    def test_reinterpreted_destination_storage_rejected_before_any_copy(self):
        source, destination = torch.nn.Module(), torch.nn.Module()
        source.packed = torch.nn.Parameter(torch.tensor([1, 2], dtype=torch.int32), requires_grad=False)
        source.scale = torch.nn.Parameter(torch.ones(4, dtype=torch.bfloat16), requires_grad=False)
        destination.packed = torch.nn.Parameter(torch.tensor([7, 8], dtype=torch.int32), requires_grad=False)
        destination.scale = torch.nn.Parameter(destination.packed.view(torch.bfloat16), requires_grad=False)
        before = self.snapshot(destination)
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.prepare(directory, source)
            with patch.object(cpu_restore, '_restore_tensor', side_effect=AssertionError('unexpected copy')):
                with self.assertRaisesRegex(ValueError, 'different layouts'):
                    cpu_restore.restore_cpu_state(directory, destination, digest, rank=0)
            self.assertTrue(compare_restored_state(before, self.snapshot(destination))['same_phase_cpu_bytes_equal'])


if __name__ == '__main__':
    unittest.main()
