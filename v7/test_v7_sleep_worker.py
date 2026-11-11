"""CPU receipt checks; native Sleep itself still requires full GPU validation."""
import sys
import types
import unittest
from unittest import mock

import torch
from sllm_store.v7_sleep_worker import V7SleepWorkerExtension


class SleepReceiptTest(unittest.TestCase):
    def worker(self):
        worker = V7SleepWorkerExtension()
        worker.model_config = types.SimpleNamespace(enable_sleep_mode=True)
        worker.model_runner = types.SimpleNamespace(model=torch.nn.Linear(2, 2))
        worker.rank = 3
        return worker

    def receipt(self, worker, backup):
        module = types.ModuleType("vllm.device_allocator.cumem")
        data = types.SimpleNamespace(handle=(0, 8, 4096), tag="weights", cpu_backup_tensor=backup)
        allocator = types.SimpleNamespace(pointer_to_data={4096: data})
        module.CuMemAllocator = types.SimpleNamespace(get_instance=lambda: allocator)
        with mock.patch.dict(sys.modules, {module.__name__: module}), \
                mock.patch('torch.cuda.current_device', return_value=0), \
                mock.patch('torch.cuda.get_device_properties', return_value=types.SimpleNamespace(uuid='00000000-0000-0000-0000-000000000000')):
            return worker.sllm_v7_sleep_receipt()

    def test_tag_totals_and_identity_are_reported_without_weight_reads(self):
        worker = self.worker()
        before = self.receipt(worker, None)
        sleeping = self.receipt(worker, torch.empty(8, dtype=torch.uint8))
        self.assertEqual(before["allocation_signature"], sleeping["allocation_signature"])
        self.assertEqual(sleeping["tags"]["weights"]["cpu_backup_bytes"], 8)
        self.assertEqual(before["tags"]["weights"]["cpu_backup_bytes"], 0)
        self.assertEqual(sleeping["rank"], 3)
        self.assertIn("pid", sleeping["identity"])

    def test_partial_backup_is_rejected(self):
        with self.assertRaises(ValueError):
            self.receipt(self.worker(), torch.empty(4, dtype=torch.uint8))

    def test_transposed_weight_view_is_covered_by_its_allocation(self):
        worker = self.worker()
        tensor = types.SimpleNamespace(is_cuda=True, shape=(2, 2), dtype=torch.bfloat16,
                                       numel=lambda: 4, element_size=lambda: 2,
                                       data_ptr=lambda: 4096, stride=lambda: (1, 2))
        worker.model_runner.model.named_parameters = lambda: [('weight', tensor)]
        result = self.receipt(worker, None)
        self.assertEqual(result['uncovered_cuda_parameters'], [])
        self.assertEqual(result['cuda_parameter_count'], 1)

    def test_disabled_sleep_is_rejected(self):
        worker = self.worker()
        worker.model_config.enable_sleep_mode = False
        with self.assertRaises(ValueError):
            self.receipt(worker, None)


if __name__ == "__main__":
    unittest.main()
