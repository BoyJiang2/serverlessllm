"""Acceptance rejects incomplete ranks, wrong physical binding and bad data."""
import copy
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location('tp8_controller', pathlib.Path(__file__).resolve().parents[1]/'gpu512_once_controller.py')
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)

class AcceptanceTest(unittest.TestCase):
    def messages(self):
        rows = [{'event':'PYTHON_ENTERED'} for _ in range(8)]
        for rank, uuid in enumerate(controller.UUIDS):
            rows.append({'event':'TP8_CUDA_NCCL_MICRO_PASS','rank':rank,'local_rank':rank,'world_size':8,'device_count':8,'device_uuid':uuid,'payload_bytes':1048576,'dtype':'torch.float32','reduced_value':28,'raw_sha256':'2e042dc2f9f98002bb4fa72e283fcbfe046c03321dfc68f094834ca03891b814','allocator_peak_bytes':1048576})
        return rows

    def test_complete_result(self):
        self.assertEqual(len(controller.validate_messages(self.messages())),8)

    def test_missing_duplicate_or_unentered_rank(self):
        original = self.messages()
        for rows in (original[:-1], original+[original[-1]], original[1:]):
            with self.subTest(rows=len(rows)), self.assertRaises(AssertionError):
                controller.validate_messages(rows)
        rows = copy.deepcopy(original); rows[-1]['rank'] = 0
        with self.assertRaises(AssertionError): controller.validate_messages(rows)

    def test_wrong_binding_collective_or_payload(self):
        for key, value in [('device_uuid',controller.UUIDS[1]),('local_rank',1),('world_size',7),('device_count',7),('payload_bytes',1),('dtype','torch.float16'),('reduced_value',27),('raw_sha256','0'*64),('allocator_peak_bytes',0)]:
            rows = self.messages(); rows[8][key] = value
            with self.subTest(key=key), self.assertRaises(AssertionError):
                controller.validate_messages(rows)

if __name__ == '__main__': unittest.main()
