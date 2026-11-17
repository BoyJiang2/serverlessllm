"""Exercise exact backup ownership and copy order with no CUDA hardware."""

import ctypes
import importlib.util
import logging
import mmap
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest
from unittest.mock import patch


OPTION = "SLLM_V11_EXACT_L1_PINNED_BACKUP"


class FakeCUDA:
    def __init__(self):
        self.calls = []
        self.host = {}
        self.gpu = {}
        self.error = None

        def register(ptr, size, flags):
            assert ptr % mmap.PAGESIZE == 0 and flags == 0
            self.calls.append(("register", size))
            if self.error == "register":
                return 1
            self.host[ptr] = size
            return 0

        def unregister(ptr):
            self.calls.append(("unregister", self.host[ptr]))
            if self.error == "unregister":
                return 1
            del self.host[ptr]
            return 0

        self.lib = SimpleNamespace(cudaHostRegister=register, cudaHostUnregister=unregister)

    def CUDART_CHECK(self, code):
        if code:
            raise RuntimeError(self.error)

    def cudaMemcpy(self, dst, src, size):
        direction = "D2H" if src in self.gpu else "H2D"
        self.calls.append((direction, size))
        if self.error == direction:
            raise RuntimeError(direction)
        if direction == "D2H":
            assert len(self.gpu[src]) == size
            assert self.host.get(dst, size) == size
            ctypes.memmove(dst, self.gpu[src], size)
        else:
            assert len(self.gpu[dst]) == size
            assert self.host.get(src, size) == size
            self.gpu[dst] = ctypes.string_at(src, size)

    def map(self, _, size, ptr, __):
        self.calls.append(("map", size))
        assert ptr not in self.gpu
        self.gpu[ptr] = b"\x00" * size

    def unmap(self, _, size, ptr, __):
        self.calls.append(("unmap", size))
        del self.gpu[ptr]


def load_candidate(driver, torch_empty):
    spec = importlib.util.spec_from_file_location("candidate_cumem", Path(__file__).with_name("cumem.py"))
    candidate = importlib.util.module_from_spec(spec)
    modules = {spec.name: candidate}
    stubs = {
        "torch": dict(Tensor=object, device=lambda kind: SimpleNamespace(type=kind), uint8=object(),
                      version=SimpleNamespace(cuda="test"), empty=torch_empty,
                      distributed=SimpleNamespace(is_initialized=lambda: False),
                      cuda=SimpleNamespace(empty_cache=lambda: None,
                                           memory=SimpleNamespace(CUDAPluggableAllocator=object, MemPool=object))),
        "vllm.logger": dict(init_logger=logging.getLogger),
        "vllm.utils.platform_utils": dict(is_pin_memory_available=lambda: True),
        "vllm.utils.system_utils": dict(find_loaded_library=lambda _: "fake"),
        "vllm.cumem_allocator": dict(init_module=lambda *_: None, python_create_and_map=driver.map,
                                    python_unmap_and_release=driver.unmap),
        "vllm.distributed.device_communicators.cuda_wrapper": dict(CudaRTLibrary=lambda: driver),
    }
    for name, attributes in stubs.items():
        modules[name] = ModuleType(name)
        vars(modules[name]).update(attributes)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(candidate)
    return candidate


class ExactBackupTest(unittest.TestCase):
    size = mmap.PAGESIZE + 123

    def setUp(self):
        self.driver = FakeCUDA()
        self.torch_allocs = []
        self.backend = load_candidate(self.driver, self.legacy_empty)
        self.allocator = self.backend.CuMemAllocator()
        self.weight = bytes(i % 253 for i in range(self.size))
        for ptr, tag, contents in [(1, "weights", self.weight), (2, "kv_cache", b"kv")]:
            handle = (0, len(contents), ptr, 0)
            self.allocator.pointer_to_data[ptr] = self.backend.AllocationData(handle, tag)
            self.driver.gpu[ptr] = contents
        env = patch.dict("os.environ", {OPTION: "1"})
        env.start()
        self.addCleanup(env.stop)

    def legacy_empty(self, size, **kwargs):
        self.torch_allocs.append((size, kwargs["pin_memory"]))
        buf = ctypes.create_string_buffer(size)
        return SimpleNamespace(numel=lambda: size, element_size=lambda: 1,
                               data_ptr=lambda: ctypes.addressof(buf))

    def test_two_round_trips_and_exact_release(self):
        for _ in range(2):
            self.driver.calls.clear()
            self.allocator.sleep(("weights",))
            backup = self.allocator.pointer_to_data[1].cpu_backup_tensor
            self.assertEqual(backup.device.type, "cpu")
            self.assertEqual(backup.numel() * backup.element_size(), self.size)
            self.assertEqual(ctypes.string_at(backup.data_ptr(), self.size), self.weight)
            self.assertEqual(self.driver.gpu, {})
            self.allocator.wake_up()
            self.assertEqual(self.driver.gpu, {1: self.weight, 2: b"\x00\x00"})
            self.assertTrue(backup._mapping.closed)
            self.assertIsNone(self.allocator.pointer_to_data[1].cpu_backup_tensor)
            self.assertEqual(self.driver.host, {})
            self.assertEqual(self.driver.calls, [("register", self.size), ("D2H", self.size),
                             ("unmap", self.size), ("unmap", 2), ("map", self.size),
                             ("H2D", self.size), ("unregister", self.size), ("map", 2)])
        self.assertFalse(self.torch_allocs)

    def test_l2_discards_and_default_l1_uses_legacy(self):
        self.allocator.sleep(())
        self.allocator.wake_up(tags=["weights"])
        self.allocator.wake_up(tags=["kv_cache"])
        self.assertEqual(self.driver.calls, [("unmap", self.size), ("unmap", 2),
                                             ("map", self.size), ("map", 2)])
        with patch.dict("os.environ", {OPTION: "0"}):
            self.allocator.sleep(("weights",))
            self.allocator.wake_up()
        self.assertEqual(self.torch_allocs, [(self.size, True)])
        self.assertFalse(self.driver.host)

    def test_failed_registration_closes_mapping(self):
        real_mmap = self.backend.mmap.mmap
        self.driver.error = "register"
        with patch.object(self.backend.mmap, "mmap", wraps=real_mmap) as allocate:
            mappings = []
            def keep_mapping(*args, **kwargs):
                mapping = real_mmap(*args, **kwargs)
                mappings.append(mapping)
                return mapping
            allocate.side_effect = keep_mapping
            with self.assertRaisesRegex(RuntimeError, "register"):
                self.allocator.sleep(("weights",))
        self.assertTrue(mappings[0].closed)
        self.assertEqual(self.driver.gpu[1], self.weight)
        self.assertFalse(self.driver.host)

    def test_failed_d2h_releases_mapping_before_aborting(self):
        self.driver.error = "D2H"
        with self.assertRaisesRegex(RuntimeError, "D2H"):
            self.allocator.sleep(("weights",))
        self.assertEqual(self.driver.calls, [("register", self.size), ("D2H", self.size),
                                             ("unregister", self.size)])
        self.assertFalse(self.driver.host)
        self.assertEqual(self.driver.gpu[1], self.weight)

    def test_failed_unmap_keeps_completed_backup(self):
        unmap = self.backend.unmap_and_release
        with patch.object(self.backend, "unmap_and_release", side_effect=RuntimeError("unmap")):
            with self.assertRaisesRegex(RuntimeError, "unmap"):
                self.allocator.sleep(("weights",))
        backup = self.allocator.pointer_to_data[1].cpu_backup_tensor
        self.assertEqual(ctypes.string_at(backup.data_ptr(), self.size), self.weight)
        unmap(self.allocator._python_free_callback(1))
        self.assertFalse(self.driver.host)

    def test_failed_h2d_preserves_backup_and_unmaps_failed_target(self):
        self.allocator.sleep(("weights",))
        backup = self.allocator.pointer_to_data[1].cpu_backup_tensor
        self.driver.error = "H2D"
        with self.assertRaisesRegex(RuntimeError, "H2D"):
            self.allocator.wake_up()
        self.assertIs(self.allocator.pointer_to_data[1].cpu_backup_tensor, backup)
        self.assertFalse(backup._mapping.closed)
        self.assertNotIn(1, self.driver.gpu)
        self.driver.error = None
        self.allocator.wake_up()
        self.assertEqual(self.driver.gpu[1], self.weight)
        self.assertTrue(backup._mapping.closed)

    def test_failed_unregistration_keeps_mapping_until_free_retry(self):
        self.allocator.sleep(("weights",))
        backup = self.allocator.pointer_to_data[1].cpu_backup_tensor
        self.driver.error = "unregister"
        with self.assertRaisesRegex(RuntimeError, "unregister"):
            self.allocator.wake_up()
        self.assertFalse(backup._mapping.closed)
        self.assertNotIn(1, self.driver.gpu)
        self.driver.error = None
        self.assertEqual(self.allocator._python_free_callback(1), (0, self.size, 1, 0))
        self.assertTrue(backup._mapping.closed)

    def test_free_callback_returns_handle_even_if_cuda_teardown_fails(self):
        self.allocator.sleep(("weights",))
        backup = self.allocator.pointer_to_data[1].cpu_backup_tensor
        self.driver.error = "unregister"
        with self.assertLogs(self.backend.logger, level="ERROR"):
            self.assertEqual(self.allocator._python_free_callback(1), (0, self.size, 1, 0))
        self.assertFalse(backup._mapping.closed)
        self.assertEqual(self.allocator._failed_host_releases, [backup])
        with self.assertRaisesRegex(RuntimeError, "failed to release"):
            self.allocator.sleep(("weights",))
        self.driver.error = None
        backup.close()


if __name__ == "__main__":
    unittest.main()
