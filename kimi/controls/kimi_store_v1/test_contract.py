import copy
import hashlib
import json
from pathlib import Path
import struct
import unittest

import contract

CONFIG = (Path(__file__).resolve().parents[2] / 'evidence' / 'followup-kimi-k26'
          / 'config.json').read_bytes()


class ContractTests(unittest.TestCase):
    def plan(self, **changes):
        args = dict(rank=0, tensor_parallel_size=8, revision=contract.REVISION,
                    vllm_version='0.19.1', torch_version='2.10.0', chunk_bytes=16,
                    tensors={'packed': dict(dtype='torch.int32', shape=[3, 8], bytes=96,
                                            contiguous=True),
                             'scale': dict(dtype='torch.bfloat16', shape=[3, 2], bytes=12,
                                           contiguous=True)})
        args.update(changes)
        return contract.plan_rank(CONFIG, **args)

    def test_original_config_only(self):
        self.assertEqual(contract.validate_config(CONFIG)['model_type'], 'kimi_k25')
        with self.assertRaises(ValueError):
            contract.validate_config(CONFIG + b' ')

    def test_chunks_cover_without_overlap_or_partial_elements(self):
        for element in (2, 4):
            for size in (0, element, 100 * element):
                spans = list(contract.chunk_ranges(size, element, 19))
                self.assertEqual(sum(n for _, n in spans), size)
                cursor = 0
                for offset, count in spans:
                    self.assertEqual(offset, cursor)
                    self.assertEqual(count % element, 0)
                    self.assertLessEqual(count, 19)
                    cursor += count

    def test_bad_chunk_bounds(self):
        for args in [(-1, 4, 16), (7, 4, 16), (8, 4, 0), (8, 4, 2**31 + 1),
                     (8, 1, 16), (True, 4, 16), (8, 4, True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                list(contract.chunk_ranges(*args))

    def test_all_rank_ids_and_honest_status(self):
        for rank in range(8):
            plan = self.plan(rank=rank)
            self.assertEqual(plan['planned_transfer_bytes'], 108)
            self.assertEqual(plan['tensors']['packed']['chunk_count'], 6)
            self.assertFalse(plan['runtime_admitted'])
            self.assertFalse(plan['store_created'])

    def test_wrong_identity_or_backend_rejected(self):
        for args in [dict(rank=8), dict(rank=True), dict(tensor_parallel_size=4),
                     dict(revision='glm'), dict(vllm_version='0.19.1.dev1'),
                     dict(torch_version=''), dict(torch_version='2.9.0'),
                     dict(tensors={}), dict(chunk_bytes=0)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.plan(**args)

    def test_shape_dtype_and_view_fail_closed(self):
        template = dict(dtype='torch.int32', shape=[3, 8], bytes=96, contiguous=True)
        for delta in [dict(dtype='torch.float8_e4m3fn'), dict(bytes=95),
                      dict(shape=[-1, 8]), dict(shape=[True, 8]), dict(contiguous=False)]:
            row = copy.deepcopy(template)
            row.update(delta)
            with self.subTest(delta=delta), self.assertRaises(ValueError):
                self.plan(tensors={'packed': row})

    def test_bit_patterns_survive_unaligned_chunk_requests(self):
        # Include signed packed words and floating-point special-value encodings.
        # These are CPU byte fixtures, never model weights or inference inputs.
        for element, raw in [
                (4, struct.pack('<4I', 0, 0xffffffff, 0x87654321, 0x7fc00001)),
                (2, struct.pack('<6H', 0, 0x8000, 0x7f80, 0xff80, 0x7fc1, 0xffff))]:
            for cap in (element, 7, 19, 64):
                digest = hashlib.sha256()
                restored = bytearray()
                for offset, count in contract.chunk_ranges(len(raw), element, cap):
                    block = memoryview(raw)[offset:offset + count]
                    digest.update(block)
                    restored.extend(block)
                self.assertEqual(bytes(restored), raw)
                self.assertEqual(digest.digest(), hashlib.sha256(raw).digest())

    def test_multi_gib_boundary_without_allocating_payload(self):
        limit = contract.MAX_CHUNK_BYTES
        self.assertEqual(list(contract.chunk_ranges(3 * limit + 4, 4, limit)),
                         [(0, limit), (limit, limit), (2 * limit, limit), (3 * limit, 4)])

    def test_scalar_empty_tensor_and_alias_budget_is_conservative(self):
        scalar = dict(dtype='torch.float32', shape=[], bytes=4, contiguous=True)
        empty = dict(dtype='torch.int32', shape=[0, 8], bytes=0, contiguous=True)
        plan = self.plan(tensors={'scalar': scalar, 'same_view': scalar, 'empty': empty})
        self.assertEqual(plan['planned_transfer_bytes'], 8)
        self.assertEqual(plan['tensors']['empty']['chunk_count'], 0)
        self.assertEqual(plan['tensors']['scalar']['chunk_count'], 1)
        self.assertFalse(plan['aliases_deduplicated'])

    def test_all_official_header_examples_have_consistent_byte_plans(self):
        evidence = Path(__file__).resolve().parents[2] / 'evidence' / 'followup-kimi-k26'
        header = json.loads((evidence / 'all-header-summary.json').read_text())
        dtype = {'I32': 'torch.int32', 'BF16': 'torch.bfloat16', 'F32': 'torch.float32'}
        self.assertEqual(len(header['rows']), 64)
        for row in header['rows']:
            # Header examples are original checkpoint metadata, not TP8 rank inventories.
            tensors = {name: dict(dtype=dtype[t['dtype']], shape=t['shape'],
                                  bytes=t['data_offsets'][1] - t['data_offsets'][0],
                                  contiguous=True) for name, t in row['examples'].items()}
            plan = self.plan(tensors=tensors, chunk_bytes=64 * 1024**2)
            self.assertEqual(plan['planned_transfer_bytes'], sum(t['bytes'] for t in tensors.values()))
            self.assertFalse(plan['tp_mapping_verified'])


if __name__ == '__main__':
    unittest.main()
