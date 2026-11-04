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
                "CUDA_VISIBLE_DEVICES": visible, "NVIDIA_VISIBLE_DEVICES": visible}

    def test_valid_single_host_rank_and_distinct_physical_bindings(self):
        rank, visible = probe.tp8_environment(self.environment())
        self.assertEqual(rank, 3)
        self.assertEqual(len(set(visible)), 8)

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
        env = self.environment(); env["NVIDIA_VISIBLE_DEVICES"] = "void"
        with self.assertRaises(ValueError):probe.tp8_environment(env)

    def test_import_does_not_import_torch(self):
        self.assertFalse(any(name == "torch" or name.startswith("torch.") for name in imported_modules))


if __name__ == "__main__":
    unittest.main()
