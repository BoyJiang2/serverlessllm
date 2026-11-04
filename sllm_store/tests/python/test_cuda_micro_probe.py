"""Pure stdlib validation of TP8 probe admission, without importing Torch."""
import importlib.util
from pathlib import Path
import sys
import unittest

ENTRY = Path(__file__).resolve().parents[1] / "cuda_micro_probe.py"
spec = importlib.util.spec_from_file_location("cuda_probe_validation", ENTRY)
probe = importlib.util.module_from_spec(spec)
before_import = set(sys.modules)
spec.loader.exec_module(probe)
imported_modules = set(sys.modules) - before_import


class Tp8EnvironmentTest(unittest.TestCase):
    def environment(self):
        visible = ",".join("GPU-00000000-0000-0000-0000-%012d" % rank for rank in range(8))
        return {"WORLD_SIZE": "8", "LOCAL_WORLD_SIZE": "8", "RANK": "3", "LOCAL_RANK": "3",
                "TORCHELASTIC_MAX_RESTARTS": "0", "TORCHELASTIC_RESTART_COUNT": "0",
                "MASTER_ADDR": "127.0.0.1", "MASTER_PORT": "29500",
                "CUDA_VISIBLE_DEVICES": visible, "NVIDIA_VISIBLE_DEVICES": visible}

    def test_valid_single_host_rank_and_distinct_physical_bindings(self):
        rank, visible, nvidia = probe.tp8_environment(self.environment())
        self.assertEqual(rank, 3)
        self.assertEqual(len(set(visible)), 8)
        self.assertEqual(nvidia, ",".join(visible))

    def test_non_loopback_rendezvous_is_rejected(self):
        for key, value in [("MASTER_ADDR", "localhost"), ("MASTER_ADDR", "::1"),
                           ("MASTER_ADDR", "172.31.0.4"), ("MASTER_PORT", "29501"),
                           ("MASTER_PORT", "0"), ("MASTER_PORT", "")]:
            env = self.environment(); env[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe.tp8_environment(env)
        for key in ("MASTER_ADDR", "MASTER_PORT"):
            env = self.environment(); del env[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                probe.tp8_environment(env)

    def test_invalid_world_rank_and_restart_are_rejected(self):
        for key, value in [("WORLD_SIZE", "7"), ("LOCAL_WORLD_SIZE", "4"),
                           ("RANK", "8"), ("RANK", "-1"), ("LOCAL_RANK", "2"),
                           ("RANK", "bad"), ("TORCHELASTIC_MAX_RESTARTS", "1"),
                           ("TORCHELASTIC_RESTART_COUNT", "1")]:
            env = self.environment(); env[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                probe.tp8_environment(env)

    def test_missing_mismatched_duplicate_or_non_uuid_visibility_rejected(self):
        for value in ["", "all", "0,1,2,3,4,5,6,7", ",".join(["GPU-00000000-0000-0000-0000-000000000000"] * 8)]:
            env = self.environment()
            env["CUDA_VISIBLE_DEVICES"] = env["NVIDIA_VISIBLE_DEVICES"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.tp8_environment(env)
        for value in (None, "", "all", "0,1,2,3,4,5,6,7", self.environment()["CUDA_VISIBLE_DEVICES"].split(",")[0]):
            env = self.environment()
            if value is None: del env["NVIDIA_VISIBLE_DEVICES"]
            else: env["NVIDIA_VISIBLE_DEVICES"] = value
            with self.subTest(nvidia=value), self.assertRaises(ValueError):
                probe.tp8_environment(env)
        for value in ("", ",".join(["GPU-00000000-0000-0000-0000-000000000000"] * 8)):
            env = self.environment(); env["NVIDIA_VISIBLE_DEVICES"] = "void"; env["CUDA_VISIBLE_DEVICES"] = value
            with self.subTest(cdi_cuda=value), self.assertRaises(ValueError):
                probe.tp8_environment(env)

    def test_syscall_captured_eight_rank_cdi_environment(self):
        # Sanitized seven-field worker environments captured by execve in CID
        # 5becc32afb7c0f1d9fbafddc9c2a99599566405de00386b3b7ad2163bb797ef0.
        # LOCAL_WORLD_SIZE/restart settings are the unchanged launch contract.
        devices = ["GPU-736d7f37-f647-b82d-ab11-693753ac6822", "GPU-c5510474-1f6d-03f1-c57f-476db275c5cb",
                   "GPU-d8081222-9816-41e4-68f8-ecbb13de6855", "GPU-15b80bc6-fcac-96f8-411d-3fd7ad0ecb8f",
                   "GPU-1fcf8aaa-8825-c6d3-fb8d-245377038242", "GPU-0df5ba07-c402-f8fd-562d-7fccb4f15d1a",
                   "GPU-cca00d09-79a7-49de-2290-9ba1d56f8c6b", "GPU-21336138-6e64-ec08-305b-26c99d661ab9"]
        for rank in range(8):
            env = {"RANK":str(rank), "LOCAL_RANK":str(rank), "WORLD_SIZE":"8",
                   "MASTER_ADDR":"127.0.0.1", "MASTER_PORT":"29500",
                   "CUDA_VISIBLE_DEVICES":",".join(devices), "NVIDIA_VISIBLE_DEVICES":"void",
                   "LOCAL_WORLD_SIZE":"8", "TORCHELASTIC_MAX_RESTARTS":"0", "TORCHELASTIC_RESTART_COUNT":"0"}
            with self.subTest(rank=rank):
                self.assertEqual(probe.tp8_environment(env), (rank, devices, "void"))

    def test_import_does_not_import_torch(self):
        self.assertFalse(any(name == "torch" or name.startswith("torch.") for name in imported_modules))


if __name__ == "__main__":
    unittest.main()
