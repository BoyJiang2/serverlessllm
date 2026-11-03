"""Exercise patch status with real patch(1), without importing vLLM."""
import os
import pathlib
import subprocess
import tempfile
import unittest


class PatchStatusTest(unittest.TestCase):
    def test_unapplied_applied_and_conflict(self):
        script = pathlib.Path(__file__).resolve().parents[2] / "vllm_patch/patch.sh"
        with tempfile.TemporaryDirectory(prefix="sllm-patch-test-") as directory:
            work = pathlib.Path(directory)
            target = work / "package"
            target.mkdir()
            (work / "patch.sh").write_bytes(script.read_bytes())
            (work / "sllm_load.patch").write_text(
                "--- a/vllm/item.txt\n+++ b/vllm/item.txt\n"
                "@@ -1 +1 @@\n-old\n+new\n")
            python = work / "python"
            python.write_text("#!/bin/sh\nprintf '%s\\n' '" + str(target) + "'\n")
            python.chmod(0o700)
            environment = dict(os.environ, PATH=str(work) + ":" + os.environ["PATH"])

            def run():
                return subprocess.run(["bash", str(work / "patch.sh")],
                                      env=environment, capture_output=True,
                                      text=True, timeout=10)

            item = target / "item.txt"
            item.write_text("old\n")
            first = run()
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(item.read_text(), "new\n")
            second = run()
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("already been applied", second.stdout)
            self.assertEqual(item.read_text(), "new\n")
            item.write_text("foreign\n")
            conflict = run()
            self.assertNotEqual(conflict.returncode, 0)
            self.assertIn("conflicts", conflict.stderr)
            self.assertNotIn("already been applied", conflict.stdout)
            self.assertEqual(item.read_text(), "foreign\n")


if __name__ == "__main__":
    unittest.main()
