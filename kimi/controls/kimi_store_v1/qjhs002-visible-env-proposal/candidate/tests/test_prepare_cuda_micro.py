"""Offline command-construction checks; never contact Docker or a GPU."""
import contextlib
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ENTRY = Path(__file__).resolve().parents[1] / "prepare_cuda_micro.py"


class PrepareCudaMicroTest(unittest.TestCase):
    def invoke(self, arguments, inspected_limit=None):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            p0 = base / "p0"
            p0.mkdir()
            names = ["cuda-micro-probe-fixture.py", "guard-fixture.py"]
            for name in names:
                (p0 / name).write_text("fixture")
            commands = []
            root = "/data/brick1/boyjiang/kimi-new/ServerlessLLM"
            def mapped_path(value):
                return p0 if value == "/data/brick1/boyjiang/kimi-k2.6/p0" else Path(value)
            def output(command, **kwargs):
                commands.append(command)
                if command == ["hostname"]:
                    return "qjhs2\n"
                if command == ["git", "-C", root, "rev-parse", "HEAD"]:
                    return "d" * 40 + "\n"
                if "create" in command:
                    return "c" * 64 + "\n"
                if "inspect" in command:
                    create = next(c for c in commands if "create" in c)
                    limit = int(next(v.split("=", 1)[1] for v in create if v.startswith("--pids-limit=")))
                    row = {"Id": "c" * 64, "Image": next(v for v in create if v.startswith("sha256:")), "State": {"Running": False},
                           "HostConfig": {"PidsLimit": limit if inspected_limit is None else inspected_limit}}
                    return json.dumps([row])
                raise AssertionError(command)
            modules = {"pathlib": types.SimpleNamespace(Path=mapped_path),
                       "subprocess": types.SimpleNamespace(check_output=output)}
            with patch.dict(sys.modules, modules), patch.object(sys, "argv", [str(ENTRY), *arguments, *names]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                runpy.run_path(str(ENTRY), run_name="__main__")
            receipt = json.loads((p0 / "cuda-micro-pids512-visible-20261104" / "prepared.json").read_text())
            self.assertFalse(any("start" in c for c in commands))
            return receipt

    def test_default_retains_128(self):
        self.assertEqual(self.invoke([])["pids_limit"], 128)

    def test_explicit_512_and_actual_source_are_recorded(self):
        receipt = self.invoke(["--pids-limit", "512"])
        self.assertEqual(receipt["pids_limit"], 512)
        self.assertEqual(receipt["source_commit"], "d" * 40)
        self.assertEqual(receipt["nonce"], "cuda-micro-pids512-visible-20261104")
        command = receipt["command"]
        environments = [command[index+1] for index, value in enumerate(command) if value == "--env"]
        uuid = "GPU-736d7f37-f647-b82d-ab11-693753ac6822"
        for variable in ("CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES"):
            self.assertEqual([value for value in environments if value.startswith(variable+"=")], [variable+"="+uuid])
        for flag in ("--pids-limit=512", "--runtime=nvidia", "--cpus=1", "--memory=2g", "--memory-swap=2g", "--network=none", "--restart=no"):
            self.assertIn(flag, receipt["command"])

    def test_nonpositive_limits_are_rejected(self):
        for value in ("0", "-1"):
            with self.subTest(value=value), self.assertRaises(SystemExit) as error:
                self.invoke(["--pids-limit", value])
            self.assertEqual(error.exception.code, 2)

    def test_inspected_limit_must_match_request(self):
        with self.assertRaises(AssertionError):
            self.invoke(["--pids-limit", "512"], inspected_limit=128)


if __name__ == "__main__":
    unittest.main()
