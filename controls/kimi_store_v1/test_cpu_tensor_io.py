import gc
import hashlib
from pathlib import Path
import tempfile
import unittest
import weakref

import torch

import contract
import cpu_tensor_io
import native_index

CONFIG = (Path(__file__).resolve().parents[2] / 'evidence/followup-kimi-k26/config.json').read_bytes()


class TensorIOTests(unittest.TestCase):
    def model(self):
        model = torch.nn.Module()
        model.packed = torch.nn.Parameter(torch.tensor([-1, -2147483648, 0, 305419896],
                                                     dtype=torch.int32), requires_grad=False)
        model.packed_alias = model.packed
        bits = torch.tensor([0, 0x8000, 0x7f80, 0x7fc1], dtype=torch.uint16)
        model.scale = torch.nn.Parameter(bits.view(torch.bfloat16), requires_grad=False)
        model.register_buffer('weight_shape', torch.tensor([7168, 2048], dtype=torch.int32))
        model.register_buffer('router_bias', torch.tensor([float('nan'), float('inf'), -0.0]))
        model.register_buffer('empty_scratch', torch.empty(0, dtype=torch.int32), persistent=False)
        return model

    def test_real_cpu_tensors_roundtrip_native_index_and_exact_bits(self):
        model = self.model()
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'rank'
            records, layouts, info = cpu_tensor_io.write_cpu_state(target, model, chunk_bytes=7)
            inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                      for n, r in layouts.items()}
            plan = contract.plan_rank(CONFIG, rank=0, tensor_parallel_size=8, revision=contract.REVISION,
                    tensors=inputs, vllm_version='0.19.1', torch_version='2.10.0')
            native_index.publish_index(target, plan, records, layouts, CONFIG)
            digest = hashlib.sha256((target / 'kimi_raw_state.json').read_bytes()).hexdigest()
            self.assertEqual(set(native_index.read_index(target, digest)), set(inputs))
            named = dict(model.named_parameters(remove_duplicate=False))
            named.update(model.named_buffers(remove_duplicate=False))
            for name, tensor in named.items():
                row = records[name]
                with (target / ('tensor.data_' + str(row['partition']))).open('rb') as stream:
                    stream.seek(row['local_offset'])
                    raw = stream.read(row['bytes'])
                self.assertEqual(raw, bytes(tensor.detach().reshape(-1).view(torch.uint8).tolist()))
            self.assertEqual(info['alias_of'], {'packed_alias': 'packed'})
            self.assertIn('empty_scratch', info['buffer_names'])
            self.assertIsNone(torch.version.cuda)

    def test_storage_views_and_unknown_dtype_are_rejected(self):
        for tensor in [torch.arange(8, dtype=torch.int32)[1:],
                       torch.arange(8, dtype=torch.int32)[:4],
                       torch.arange(8, dtype=torch.int32).reshape(2, 4).t(),
                       torch.ones(2, dtype=torch.int64),
                       torch.empty(2, device='meta')]:
            with self.assertRaises(ValueError):
                cpu_tensor_io.tensor_layout(tensor)

    def test_real_source_lifetime_until_generator_close(self):
        tensor = torch.arange(4, dtype=torch.int32)
        reference = weakref.ref(tensor)
        chunks = cpu_tensor_io.iter_cpu_blocks(tensor, chunk_bytes=4)
        block = next(chunks)
        self.assertEqual(bytes(block), b'\x00' * 4)
        del tensor, block
        gc.collect()
        self.assertIsNotNone(reference())
        chunks.close()
        gc.collect()
        self.assertIsNone(reference())

    def test_invalid_bound_cannot_create_successful_state(self):
        for cap in [0, True, cpu_tensor_io.CHUNK_BYTES + 1]:
            with self.assertRaises(ValueError):
                list(cpu_tensor_io.iter_cpu_blocks(torch.zeros(2, dtype=torch.int32), cap))

    def test_invalid_bound_and_shared_reinterpretation_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'rank'
            for cap in [0, True, cpu_tensor_io.CHUNK_BYTES + 1]:
                with self.assertRaises(ValueError):
                    cpu_tensor_io.write_cpu_state(target, self.model(), cap)
                self.assertFalse(target.exists())
            model = self.model()
            model.register_buffer('different_view', model.packed.detach().view(torch.bfloat16))
            with self.assertRaises(ValueError):
                cpu_tensor_io.write_cpu_state(target, model)
            self.assertFalse(target.exists())

    def test_lazy_negative_views_rejected_before_any_write(self):
        for include_original in (False, True):
            model = torch.nn.Module()
            source = torch.tensor([1.0, -2.0], dtype=torch.float32)
            if include_original:
                model.register_buffer('original', source)
            model.register_buffer('negative', torch._neg_view(source))
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'rank'
                with self.assertRaises(ValueError):
                    cpu_tensor_io.write_cpu_state(target, model)
                self.assertFalse(target.exists())

    def test_retained_byte_view_owns_source_after_generator_close(self):
        tensor = torch.tensor([305419896], dtype=torch.int32)
        reference = weakref.ref(tensor)
        chunks = cpu_tensor_io.iter_cpu_blocks(tensor, 4)
        block = next(chunks)
        del tensor
        chunks.close()
        gc.collect()
        self.assertIsNotNone(reference())
        self.assertEqual(bytes(block), b'\x78\x56\x34\x12')
        del block
        gc.collect()
        self.assertIsNone(reference())


if __name__ == '__main__':
    unittest.main()
