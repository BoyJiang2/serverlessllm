"""Offline tests of the candidate's actual archive validation (no SDK/CUDA imports).

Synthetic payload digests are injected only into this isolated AST namespace;
gold constants must separately be reviewed against the retained real receipt.
"""
import ast
import copy
import hashlib
import json
import pathlib
import tempfile
import types
import unittest

SOURCE = pathlib.Path(__file__).with_name("test_kimi_tensor_component.py")
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
NODES = [node for node in TREE.body if
         isinstance(node, ast.FunctionDef) and node.name in ("require", "read_existing_archive")
         or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
             t.id in ("LIMIT", "GOLD_INDEX", "GOLD_RAW_SHA", "GOLD_FILE_SHA") for t in node.targets)]
CODE = compile(ast.Module(body=NODES, type_ignores=[]), str(SOURCE), "exec")


class Tensor:
    def __init__(self, data, dtype):
        self.data, self.dtype = data, dtype

    def reshape(self, shape):
        self.shape = shape
        return self

    def stride(self):
        return [self.shape[1], 1] if len(self.shape) == 2 else [1]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name).resolve()
        self.archive = self.root / "component"
        self.archive.mkdir()
        self.ns = {"pathlib": pathlib, "json": json, "hashlib": hashlib,
                   "torch": types.SimpleNamespace(int32="int32", bfloat16="bfloat16",
                                                   frombuffer=Tensor)}
        exec(CODE, self.ns)
        self.index = copy.deepcopy(self.ns["GOLD_INDEX"])
        self.data = bytes(8261632)
        self.index_bytes = json.dumps(self.index).encode()
        self.ns["GOLD_FILE_SHA"] = {"tensor_index.json": self.sha(self.index_bytes),
                                    "tensor.data_0": self.sha(self.data)}
        self.ns["GOLD_RAW_SHA"] = {name: self.sha(self.data[offset:offset + size])
                                  for name, (offset, size, *_) in self.index.items()}
        (self.archive / "tensor_index.json").write_bytes(self.index_bytes)
        (self.archive / "tensor.data_0").write_bytes(self.data)
        self.receipt = {"tensor_index": self.index, "raw_sha256": self.ns["GOLD_RAW_SHA"],
            "tensor_index_sha256": self.ns["GOLD_FILE_SHA"]["tensor_index.json"],
            "tensor_data_sha256": self.ns["GOLD_FILE_SHA"]["tensor.data_0"],
            "archive_bytes": 8261632, "payload_bytes": 8257544, "total_archive_bytes": 8261992}
        self.receipt_path = self.root / "receipt.json"
        self.write_receipt()
        self.args = types.SimpleNamespace(storage=self.root, archive="component",
                                          receipt=self.receipt_path)

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def write_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt))

    def run_archive(self):
        return self.ns["read_existing_archive"](self.args)

    def test_valid_archive(self):
        state, evidence = self.run_archive()
        self.assertEqual(set(state), set(self.index))
        self.assertEqual(evidence["stage"], "EXISTING_ARCHIVE_VALIDATED")
        self.assertEqual([t.dtype for t in state.values()], ["int32", "bfloat16", "int32"])

    def test_rejections(self):
        cases = ("extra", "data_digest", "index_digest", "receipt", "metadata", "extent",
                 "bound", "traversal", "absolute", "relative_root", "slice_digest")
        for case in cases:
            with self.subTest(case=case):
                # Restore only the small synthetic archive and isolated expected constants.
                (self.archive / "extra").unlink(missing_ok=True)
                (self.archive / "tensor.data_0").write_bytes(self.data)
                (self.archive / "tensor_index.json").write_bytes(self.index_bytes)
                self.args.storage, self.args.archive = self.root, "component"
                self.write_receipt()
                if case == "extra": (self.archive / "extra").touch()
                elif case == "data_digest": (self.archive / "tensor.data_0").write_bytes(b"x")
                elif case == "index_digest": (self.archive / "tensor_index.json").write_text("{}")
                elif case == "receipt": self.receipt_path.write_text("{}")
                elif case == "metadata":
                    receipt = copy.deepcopy(self.receipt)
                    receipt["tensor_index"][next(iter(self.index))][3] = [1, 1]
                    self.receipt_path.write_text(json.dumps(receipt))
                elif case == "extent": (self.archive / "tensor.data_0").write_bytes(self.data[:-1])
                elif case == "bound":
                    with (self.archive / "tensor.data_0").open("wb") as stream:
                        stream.truncate((16 << 20) + 1)
                elif case == "traversal": self.args.archive = "../component"
                elif case == "absolute": self.args.archive = str(self.archive)
                elif case == "relative_root": self.args.storage = pathlib.Path(".")
                elif case == "slice_digest":
                    self.ns["GOLD_RAW_SHA"] = dict(self.ns["GOLD_RAW_SHA"])
                    self.ns["GOLD_RAW_SHA"][next(iter(self.index))] = "0" * 64
                    self.receipt["raw_sha256"] = self.ns["GOLD_RAW_SHA"]
                    self.write_receipt()
                with self.assertRaises((AssertionError, KeyError)):
                    self.run_archive()

    def test_symlink_rejections(self):
        link = self.root / "linked"
        try:
            link.symlink_to(self.archive, target_is_directory=True)
        except OSError as error:
            self.skipTest("symlink creation unavailable: " + str(error))
        self.args.archive = "linked"
        with self.assertRaisesRegex(AssertionError, "symlink"):
            self.run_archive()
        self.args.archive = "component"
        data_path = self.archive / "tensor.data_0"
        outside = self.root / "outside"
        data_path.rename(outside)
        data_path.symlink_to(outside)
        with self.assertRaisesRegex(AssertionError, "symlinks"):
            self.run_archive()


if __name__ == "__main__":
    unittest.main(verbosity=2)
