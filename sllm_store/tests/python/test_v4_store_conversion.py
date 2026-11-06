"""Guarded conversion entry contracts; conversion itself is an explicit fixture."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

import v4_store_conversion as entry


class StoreConversionEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output, self.store = self.root / "output", self.root / "store"
        self.guard = self.root / "armed.json"
        self.armed = dict(purpose="store-conversion", identity=dict(container_id="a" * 64),
                          armed_ns=time.monotonic_ns() - 1000000,
                          deadline_ns=time.monotonic_ns() + 10000000000)
        self.save_guard()
        for patch in (mock.patch.object(entry.socket, "gethostname", return_value="a" * 12),
                      mock.patch.dict(os.environ, VLLM_PLUGINS="sllm_v4_store"),
                      mock.patch.object(entry.importlib.metadata, "version", return_value="0.20.1"),
                      mock.patch.object(entry, "export_engine_config", return_value={})):
            patch.start()
            self.addCleanup(patch.stop)

    def save_guard(self):
        self.guard.write_text(json.dumps(self.armed))

    def run_entry(self):
        entry.run_conversion("/model", str(self.store), {"conversion_id": "fixture"},
                             self.guard, self.output)

    def test_wrong_purpose_container_or_deadline_rejected_before_conversion(self):
        for change in (dict(purpose="native-reference"), dict(deadline_ns=0),
                       dict(armed_ns=time.monotonic_ns() + 100000000000),
                       dict(identity=dict(container_id="b" * 64))):
            original = dict(self.armed)
            self.armed.update(change)
            self.save_guard()
            with mock.patch.object(entry, "convert_grouped_store") as convert:
                with self.assertRaisesRegex(ValueError, "armed Store"):
                    self.run_entry()
                convert.assert_not_called()
            self.assertFalse(self.output.exists())
            self.armed = original

    def test_wrong_plugin_or_version_rejected_before_conversion(self):
        with mock.patch.object(entry, "convert_grouped_store") as convert:
            with mock.patch.dict(os.environ, VLLM_PLUGINS=""):
                with self.assertRaisesRegex(ValueError, "plugin"):
                    self.run_entry()
            with mock.patch.object(entry.importlib.metadata, "version", return_value="0.11.2"):
                with self.assertRaisesRegex(ValueError, "version"):
                    self.run_entry()
            convert.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_complete_is_only_a_candidate_pending_external_release(self):
        def convert(*args):
            self.store.mkdir()
            (self.store / "COMPLETE.json").write_text("{}")
        with mock.patch.object(entry, "convert_grouped_store", side_effect=convert):
            self.run_entry()
        result = json.loads((self.output / "candidate.json").read_text())
        self.assertEqual(result["complete_sha256"], entry.file_digest(self.store / "COMPLETE.json"))
        self.assertFalse(result["runtime_qualified"])
        self.assertFalse(result["physical_cleanup_verified"])

    def test_failure_retains_original_exception_and_no_candidate(self):
        with mock.patch.object(entry, "convert_grouped_store", side_effect=TimeoutError("rank5 fixture")):
            with self.assertRaisesRegex(TimeoutError, "rank5 fixture"):
                self.run_entry()
        result = json.loads((self.output / "failure.json").read_text())
        self.assertIn("rank5 fixture", result["traceback"])
        self.assertFalse((self.output / "candidate.json").exists())

    def test_default_cli_only_prints_plan(self):
        identity = self.root / "identity.json"
        identity.write_text("{}")
        argv = ["v4_store_conversion", "--model", "/model", "--store", str(self.store),
                "--identity", str(identity)]
        with mock.patch("sys.argv", argv), mock.patch("builtins.print") as printed, \
                mock.patch.object(entry, "run_conversion") as execute:
            entry.main()
        execute.assert_not_called()
        printed.assert_called_once()
        self.assertFalse(self.output.exists())
        self.assertFalse(self.store.exists())

    def test_execute_without_guard_is_rejected_before_conversion(self):
        identity = self.root / "identity.json"
        identity.write_text("{}")
        argv = ["v4_store_conversion", "--model", "/model", "--store", str(self.store),
                "--identity", str(identity), "--execute"]
        with mock.patch("sys.argv", argv), mock.patch("sys.stderr"), \
                mock.patch.object(entry, "run_conversion") as execute:
            with self.assertRaises(SystemExit) as error:
                entry.main()
        self.assertEqual(error.exception.code, 2)
        execute.assert_not_called()

    def test_existing_output_cannot_be_reused(self):
        self.output.mkdir()
        with mock.patch.object(entry, "convert_grouped_store") as convert:
            with self.assertRaises(FileExistsError):
                self.run_entry()
            convert.assert_not_called()


if __name__ == "__main__":
    unittest.main()
