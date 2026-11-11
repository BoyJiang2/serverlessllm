"""CPU-only checks for the export memory controls added for the v3 rebuild."""
import os
import tempfile
import unittest
from pathlib import Path

import sllm_store.vllm_glm20_loader as loader


class ExportMemoryTest(unittest.TestCase):
    def test_drop_page_cache_reports_dropped_files(self):
        with tempfile.TemporaryDirectory() as root:
            good = Path(root) / 'checkpoint.safetensors'
            good.write_bytes(b'\0' * (1 << 20))
            handle = os.open(good, os.O_RDONLY)
            os.read(handle, 1 << 20)
            os.close(handle)
            missing = Path(root) / 'missing.safetensors'
            self.assertEqual(loader._drop_page_cache([good, missing]), 1)

    def test_barrier_returns_once_every_rank_arrives(self):
        original = loader.get_tensor_model_parallel_rank
        try:
            with tempfile.TemporaryDirectory() as root:
                seen = []
                for rank in (5, 2):
                    loader.get_tensor_model_parallel_rank = lambda rank=rank: rank
                    seen.append(loader._await_export_barrier(root, ranks=2, timeout=1))
                self.assertEqual(seen, [1, 2])
        finally:
            loader.get_tensor_model_parallel_rank = original

    def test_barrier_timeout_is_bounded(self):
        original = loader.get_tensor_model_parallel_rank
        try:
            with tempfile.TemporaryDirectory() as root:
                loader.get_tensor_model_parallel_rank = lambda: 0
                self.assertEqual(loader._await_export_barrier(root, ranks=8, timeout=0.1), 1)
        finally:
            loader.get_tensor_model_parallel_rank = original


if __name__ == '__main__':
    unittest.main()
