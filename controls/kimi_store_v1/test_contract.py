import copy
from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()
