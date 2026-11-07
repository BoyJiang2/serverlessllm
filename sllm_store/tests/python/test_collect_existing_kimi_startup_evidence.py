"""Only temporary tiny logs; no runtime assets, subprocesses or GPU."""
import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import tempfile
import unittest
from unittest import mock

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "collect_existing_kimi_startup_evidence.py"
spec = importlib.util.spec_from_file_location("collector", SOURCE)
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class ExistingStartupCollectorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="kimi-log-unit-")
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)

    def ref(self, path, data):
        path.write_text(json.dumps(data))
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def manifest(self):
        runs = []
        for i in range(38):
            folder = self.root / str(i); folder.mkdir()
            validity = self.ref(folder / "validity.json", {"primary_sample_qualified": True})
            runs.append({"run_id": str(i), "cohort": "original", "original_runtime_root": str(folder), "refs": {"validity": validity}})
        runs.append({"run_id": "supplement", "cohort": "supplement", "refs": {}})
        return self.ref(self.root / "manifest.json", {"runs": runs})

    def test_real_consumer_sanitizes_markers_and_preserves_missing_null(self):
        ref = self.manifest(); output = self.root / "out.json"
        imports = {"origins": {"ray": "/usr/local/lib/ray/__init__.py", "credentials": "secret"}, "versions": {"torch": "2.10.0+cu129", "secret": "value"}, "env": {"TOKEN": "secret"}}
        (self.root / "0" / "worker.stdout").write_text("TOKEN=never_export\nModel loading took 72.125 GiB memory and 5.123456 seconds\nNATIVE_IMPORTS_PASS " + json.dumps(imports) + "\ncapture_seconds=999\nbackend_init=888")
        (self.root / "2" / "empty.stderr").write_bytes(b"")
        (self.root / "0" / "nested").mkdir()
        (self.root / "0" / "nested" / "ignore.stdout").write_text("Model loading took 1 GiB memory and 999 seconds")
        with mock.patch.object(c.os, "geteuid", return_value=0): result = c.collect(ref, output)
        data = json.loads(output.read_bytes())
        self.assertEqual(result["runs"], 38)
        self.assertEqual(data["runs"][0]["model_loading_seconds"], [5.123456])
        self.assertIsNone(data["runs"][1]["model_loading_seconds"])
        self.assertEqual(data["runs"][0]["observation_status"], "OBSERVED")
        self.assertEqual(data["runs"][1]["observation_status"], "NO_LOG_FILES")
        self.assertEqual(data["runs"][2]["observation_status"], "NO_MATCH")
        file = data["runs"][0]["files"][0]
        self.assertEqual(file["source_path"], str(self.root / "0" / "worker.stdout"))
        self.assertIsNone(file["model_loading_markers"][0]["rank"])
        self.assertIsNone(file["model_loading_markers"][0]["pid"])
        self.assertNotIn("never_export", output.read_text()); self.assertNotIn("secret", output.read_text())
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(data["runs"][0]["files"][0]["import_markers"][0]["versions"], {"torch": "2.10.0+cu129"})

    def test_nonroot_refused_before_input_or_output(self):
        with mock.patch.object(c.os, "geteuid", return_value=2147), mock.patch.object(c, "read_ref") as read:
            with self.assertRaisesRegex(PermissionError, "root_required"): c.collect({}, self.root / "out")
            read.assert_not_called()
        self.assertFalse((self.root / "out").exists())

    def test_output_exclusive_never_overwrites(self):
        ref = self.manifest(); output = self.root / "out"; output.write_bytes(b"keep")
        with mock.patch.object(c.os, "geteuid", return_value=0), self.assertRaises(FileExistsError): c.collect(ref, output)
        self.assertEqual(output.read_bytes(), b"keep")

    def test_sha_and_original_geometry_reject(self):
        ref = self.manifest(); bad = dict(ref, sha256="0" * 64)
        with mock.patch.object(c.os, "geteuid", return_value=0), self.assertRaisesRegex(ValueError, "sha_mismatch"): c.collect(bad, self.root / "out")
        data = json.loads(pathlib.Path(ref["path"]).read_bytes()); data["runs"][0]["cohort"] = "historical"
        ref = self.ref(pathlib.Path(ref["path"]), data)
        with mock.patch.object(c.os, "geteuid", return_value=0), self.assertRaisesRegex(ValueError, "38_distinct"): c.collect(ref, self.root / "out")

    def test_symlink_nonregular_per_file_and_total_limits(self):
        p = self.root / "data"; p.write_bytes(b"123")
        link = self.root / "link"; link.symlink_to(p)
        with self.assertRaises(OSError): c.read_stable(link, 10)
        with self.assertRaises(ValueError): c.read_stable(self.root, 10)
        with self.assertRaises(ValueError): c.read_stable(p, 2)
        ref = self.manifest(); (self.root / "0" / "a.stdout").write_bytes(b"12"); (self.root / "1" / "a.stderr").write_bytes(b"34")
        with mock.patch.object(c.os, "geteuid", return_value=0), mock.patch.object(c, "TOTAL", 3), self.assertRaises(ValueError): c.collect(ref, self.root / "out")

    def test_identity_change_after_read_rejected(self):
        p = self.root / "log"; p.write_bytes(b"abc")
        real = c.os.stat
        def changed(path, **kwargs):
            value = real(path, **kwargs)
            return type("Stat", (), dict(st_dev=value.st_dev, st_ino=value.st_ino + 1, st_size=value.st_size, st_mtime_ns=value.st_mtime_ns))()
        with mock.patch.object(c.os, "stat", side_effect=changed), self.assertRaisesRegex(ValueError, "identity_changed"): c.read_stable(p, 10)

    def test_empty_log_and_malformed_import_are_explicit(self):
        p = self.root / "empty"; p.write_bytes(b"")
        raw, identity = c.read_stable(p, 0); self.assertEqual(raw, b""); self.assertEqual(identity["bytes"], 0)
        markers, imports = c.extract(b"NATIVE_IMPORTS_PASS notJSON\nModel loading took 1 GiB memory and 2 seconds")
        self.assertEqual(markers[0]["model_loading_seconds"], 2)
        self.assertEqual(imports, [{"line": 1, "status": "MALFORMED_IMPORT_MARKER"}])


if __name__ == "__main__":
    unittest.main()
