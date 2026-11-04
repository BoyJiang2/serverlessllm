"""Source-isolated mixed-empty checks; no Torch/SDK/container imports."""
import ast
import hashlib
import json
import pathlib
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
