"""Source-isolated mixed-empty checks; no Torch/SDK/container imports."""
import ast
import hashlib
import json
import pathlib
import re
import tempfile
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]


class MixedEmptySourceTest(unittest.TestCase):
    def fixture(self):
        temporary = tempfile.TemporaryDirectory(prefix="sllm-mixed-source-")
        self.addCleanup(temporary.cleanup)
        storage = pathlib.Path(temporary.name)
        reference = storage / "gold"
        reference.mkdir()
        receipt = storage / "receipt.json"
        receipt.write_text("immutable receipt")
        index = {name: [0, 4, [1], [1], "torch.int32"]
                 for name in ("packed", "scale", "shape")}
        (reference / "tensor_index.json").write_text(json.dumps(index))
        (reference / "tensor.data_0").write_bytes(b"gold")
        state = {name: object() for name in index}
        evidence = {"payload_bytes": 4}
        args = types.SimpleNamespace(storage=storage, archive="gold", receipt=receipt,
                                     mixed_empty_archive="mixed")
        empty = types.SimpleNamespace(untyped_storage=lambda: types.SimpleNamespace(nbytes=lambda: 0))
        names = ("empty0", "empty1", "empty2", "empty3")
        def save_dict(tensors, destination):
            path = pathlib.Path(destination)
            path.mkdir()
            saved = dict(index)
            saved.update({name: [4, 0, [384, 0], [1, 1], "torch.int32"] for name in names})
            (path / "tensor_index.json").write_text(json.dumps(saved))
            (path / "tensor.data_0").write_bytes(b"gold")
        tree = ast.parse((ROOT / "tests/python/test_kimi_tensor_component.py").read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ("require", "save_mixed_empty_archive")]
        namespace = dict(pathlib=pathlib, hashlib=hashlib, json=json, LIMIT=16 << 20,
            GOLD_INDEX=index, GOLD_FILE_SHA={"tensor_index.json": "unused", "tensor.data_0": hashlib.sha256(b"gold").hexdigest()},
            EMPTY_NAMES=names, EMPTY_METADATA_SOURCE_SHA="frozen provenance",
            torch=types.SimpleNamespace(int32="i32", empty_strided=lambda *a, **k: empty),
            save_dict=save_dict)
        exec(compile(ast.Module(body=functions, type_ignores=[]), "<mixed-component-source>", "exec"), namespace)
        return args, state, evidence, namespace

    def test_seven_key_mixed_archive_keeps_source_files_and_zero_metadata(self):
        args, state, evidence, ns = self.fixture()
        sources = ns["save_mixed_empty_archive"](args, state, evidence)
        self.assertEqual(args.archive, "mixed")
        self.assertEqual(set(evidence["tensor_index"]), set(ns["GOLD_INDEX"]) | set(ns["EMPTY_NAMES"]))
        self.assertEqual(evidence["payload_bytes"], 4)
        self.assertEqual(evidence["source_file_sha256"], {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
        self.assertTrue(all(evidence["tensor_index"][name][1:] == [0, [384, 0], [1, 1], "torch.int32"] for name in ns["EMPTY_NAMES"]))

    def test_existing_or_traversal_destination_is_rejected(self):
        args, state, evidence, ns = self.fixture()
        for name in ("gold", "../outside", "/absolute"):
            args.mixed_empty_archive = name
            with self.assertRaises(AssertionError):
                ns["save_mixed_empty_archive"](args, state, evidence)

    def test_changed_original_index_is_rejected(self):
        args, state, evidence, ns = self.fixture()
        original = ns["save_dict"]
        def changed(state, destination):
            original(state, destination)
            path = pathlib.Path(destination) / "tensor_index.json"
            index = json.loads(path.read_text())
            index["packed"][1] = 5
            path.write_text(json.dumps(index))
        ns["save_dict"] = changed
        with self.assertRaisesRegex(AssertionError, "original three"):
            ns["save_mixed_empty_archive"](args, state, evidence)

    def test_invalid_empty_stride_is_rejected(self):
        args, state, evidence, ns = self.fixture()
        original = ns["save_dict"]
        def changed(state, destination):
            original(state, destination)
            path = pathlib.Path(destination) / "tensor_index.json"
            index = json.loads(path.read_text())
            index["empty0"][3] = [0, 0]
            path.write_text(json.dumps(index))
        ns["save_dict"] = changed
        with self.assertRaisesRegex(AssertionError, "empty index"):
            ns["save_mixed_empty_archive"](args, state, evidence)

    def extension_check(self):
        tree = ast.parse((ROOT / "tests/python/test_kimi_tensor_component.py").read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                     and n.name in ("require", "checkpoint_extension_identity")]
        namespace = dict(pathlib=pathlib, re=re, hashlib=hashlib, LIMIT=16 << 20)
        exec(compile(ast.Module(body=functions, type_ignores=[]), "<imported-extension-source>", "exec"), namespace)
        return namespace["checkpoint_extension_identity"]

    def test_imported_extension_hash_records_actual_file(self):
        with tempfile.TemporaryDirectory(prefix="sllm-extension-source-") as folder:
            path = pathlib.Path(folder) / "_C.so"
            path.write_bytes(b"fake unit extension, never imported")
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            result = self.extension_check()(types.SimpleNamespace(__file__=str(path)), digest)
            self.assertEqual(result, {"file": str(path), "sha256": digest})

    def test_imported_extension_hash_mismatch_or_missing_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="sllm-extension-source-") as folder:
            path = pathlib.Path(folder) / "_C.so"
            path.write_bytes(b"fake unit extension")
            module = types.SimpleNamespace(__file__=str(path))
            with self.assertRaisesRegex(AssertionError, "hash mismatch"):
                self.extension_check()(module, "0" * 64)
            with self.assertRaisesRegex(AssertionError, "invalid extension pin"):
                self.extension_check()(module, "not-a-hash")
            path.unlink()
            with self.assertRaisesRegex(AssertionError, "invalid imported"):
                self.extension_check()(module, "0" * 64)

    def test_pointer_fix_profile_is_distinct_and_overlay_is_opt_in(self):
        tree = ast.parse((ROOT / "tests/s4a_root_controller.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "mixed_empty_controller")
        names = {"RUN", "BASE", "pinfile", "versioned_c"}
        assignments = [n for n in function.body if isinstance(n, ast.Assign)
                       and isinstance(n.targets[0], ast.Name) and n.targets[0].id in names]
        overlay = next(n for n in ast.walk(function) if isinstance(n, ast.If)
                       and ast.unparse(n.test) == "empty_pointer_fix"
                       and any(isinstance(v, ast.Expr) and isinstance(v.value, ast.Call)
                               and ast.unparse(v.value.func) == "mounts.append" for v in n.body))
        for enabled in (False, True):
            ns = dict(ROOT=pathlib.Path("/data/brick1/boyjiang/kimi-new/ServerlessLLM"), empty_pointer_fix=enabled)
            exec(compile(ast.Module(body=assignments, type_ignores=[]), "<fixed-profile-source>", "exec"), ns)
            expected = "kimi-s4a-mixed-empty-pointer-fix-20261105-once" if enabled else "kimi-s4a-mixed-empty-20261105-once"
            self.assertEqual(ns["RUN"], expected)
            self.assertEqual(ns["pinfile"].name, "s4a-mixed-empty-pointer-fix-input-pins-20261105.json" if enabled else "s4a-mixed-empty-input-pins-20261105.json")
            ns.update(mounts=[], command=[], pins={"files": {str(ns["versioned_c"]): "a" * 64}})
            exec(compile(ast.Module(body=[overlay], type_ignores=[]), "<fixed-overlay-source>", "exec"), ns)
            self.assertEqual(ns["mounts"], [(ns["versioned_c"], "/source/sllm_store/sllm_store/_C.cpython-312-x86_64-linux-gnu.so", True)] if enabled else [])
            self.assertEqual(ns["command"], ["--expected-c-sha256", "a" * 64] if enabled else [])

    def test_runner_forwards_extension_pin_only_when_explicit(self):
        tree = ast.parse((ROOT / "tests/python/s4a_component_runner.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main")
        branch = next(n for n in ast.walk(function) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "args.expected_c_sha256 is not None")
        for digest in (None, "b" * 64):
            ns = dict(args=types.SimpleNamespace(expected_c_sha256=digest), command=[])
            exec(compile(ast.Module(body=[branch], type_ignores=[]), "<runner-extension-source>", "exec"), ns)
            self.assertEqual(ns["command"], [] if digest is None else ["--expected-c-sha256", digest])

    def launch(self, now, actual_enter):
        tree = ast.parse((ROOT / "tests/s4a_root_controller.py").read_text())
        parent = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "mixed_empty_controller")
        function = next(n for n in parent.body if isinstance(n, ast.FunctionDef) and n.name == "launch")
        call = mock.Mock()
        namespace = dict(time=types.SimpleNamespace(monotonic=lambda: now), outer_end=218,
            call=call, BASE=pathlib.Path("/evidence"), props=lambda _: {
                "RuntimeMaxUSec": "3min", "TimeoutStopUSec": "3s",
                "ActiveEnterTimestampMonotonic": str(int(actual_enter * 1e6))})
        exec(compile(ast.Module(body=[function], type_ignores=[]), "<mixed-native-budget>", "exec"), namespace)
        return namespace["launch"], call

    def test_native_precheck_rejects_before_launch(self):
        launch, call = self.launch(40, 0)
        with self.assertRaises(AssertionError):
            launch("owned.service", ["python3"])
        call.assert_not_called()

    def test_native_postcheck_rejects_late_actual_activation(self):
        launch, call = self.launch(10, 50)
        with self.assertRaises(AssertionError):
            launch("owned.service", ["python3"])
        call.assert_called_once()
        launch, call = self.launch(10, 20)
        launch("owned.service", ["python3"])
        call.assert_called_once()


if __name__ == "__main__":
    unittest.main()
