"""CPU filesystem/protocol tests; no Torch, extension or GPU imports."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sllm_store import v4_state as state


def identity():
    return {
        "format": state.FORMAT, "state": "before_quantization_postprocessing",
        "source_revision": state.REVISION, "engine_commit": state.ENGINE_COMMIT,
        "vllm_version": "0.20.1", "torch_version": "2.11.0+cpu_test_standin",
        "kernel_packages": dict(state.KERNEL_PACKAGES),
        "conversion_id": "cpu-fixture-1", "tp": 8, "pp": 1, "dp": 1,
        "ep": False, "activation_dtype": "torch.bfloat16",
    }


def tensor_row(dtype="torch.uint8", shape=None, kind="parameter"):
    shape = [2] if shape is None else shape
    size = state.DTYPE_BYTES[dtype]
    stride, step = [], 1
    for dim in reversed(shape):
        stride.insert(0, step)
        step *= max(dim, 1)
    count = 1
    for dim in shape:
        count *= dim
    row = dict(shape=shape, stride=stride, dtype=dtype, bytes=count * size,
               kind=kind, persistent=True if kind == "buffer" else None,
               sha256=hashlib.sha256(bytes(count * size)).hexdigest())
    if not count:
        row["empty"] = True
    return row


def rank_fixture(root, rank, rows=None, quant_methods=None):
    rows = copy.deepcopy(rows or {
        "w13_weight": tensor_row(),
        "w13_weight_scale": tensor_row(shape=[1]),
        "nonexpert.weight": tensor_row("torch.float8_e4m3fn"),
        "nonexpert.scale": tensor_row("torch.float8_e8m0fnu", [1]),
        "hc_attn_scale": tensor_row("torch.float32", [1]),
        "attention.buffer": tensor_row("torch.bfloat16", [1], "buffer"),
    })
    directory = Path(root) / f"rank_{rank}"
    directory.mkdir()
    index, offset = {}, 0
    for name, row in rows.items():
        if row.get("empty") or "alias" in row:
            continue
        index[name] = [offset, row["bytes"], row["shape"], row["stride"], row["dtype"]]
        offset += row["bytes"]
    (directory / "tensor.data_0").write_bytes(bytes(offset))
    (directory / "tensor_index.json").write_text(json.dumps(index))
    document = dict(identity=identity(), rank=rank, tensors=rows,
                    coordinates=dict(global_rank=rank, tp=rank, pp=0, dp=0),
                    device_uuid=f"cpu-fixture-device-{rank}",
                    quant_methods=quant_methods or {}, files={})
    for path in directory.iterdir():
        document["files"][path.name] = dict(bytes=path.stat().st_size, sha256=state.file_digest(path))
    (directory / "raw_state.json").write_text(json.dumps(document))
    return document


class V4StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def populate(self, count=8):
        for rank in range(count):
            rank_fixture(self.root, rank)

    def rewrite_rank(self, rank, edit):
        path = self.root / f"rank_{rank}" / "raw_state.json"
        document = json.loads(path.read_text())
        edit(document)
        path.write_text(json.dumps(document))

    def test_overlapping_canonical_offsets_cannot_publish(self):
        self.populate()
        directory = self.root / "rank_0"
        path = directory / "tensor_index.json"
        index = json.loads(path.read_text())
        names = list(index)
        index[names[1]][0] = index[names[0]][0]
        path.write_text(json.dumps(index))
        document_path = directory / "raw_state.json"
        document = json.loads(document_path.read_text())
        document["files"]["tensor_index.json"] = dict(bytes=path.stat().st_size, sha256=state.file_digest(path))
        document_path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "overlap"):
            state.publish_complete(self.root, identity())
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_partial_rank_cannot_publish_and_complete_cannot_be_overwritten(self):
        self.populate(7)
        with self.assertRaises(ValueError):
            state.publish_complete(self.root, identity())
        self.assertFalse((self.root / "COMPLETE.json").exists())
        rank_fixture(self.root, 7)
        state.publish_complete(self.root, identity())
        before = (self.root / "COMPLETE.json").read_bytes()
        with self.assertRaises(FileExistsError):
            state.publish_complete(self.root, identity())
        self.assertEqual((self.root / "COMPLETE.json").read_bytes(), before)
        state.validate_complete(self.root, identity(), verify_payload=True)

    def test_absent_complete_and_altered_rank_manifest_rejected(self):
        self.populate()
        with self.assertRaises(ValueError):
            state.validate_complete(self.root, identity())
        state.publish_complete(self.root, identity())
        self.rewrite_rank(2, lambda doc: doc.update(quant_methods={
            "unexpected": {"class": "test.changed", "backends": {}}}))
        with self.assertRaisesRegex(ValueError, "changed after publication"):
            state.validate_complete(self.root, identity())

    def test_mixed_run_rank_or_engine_fails_before_complete(self):
        self.populate()
        for field, value in (("conversion_id", "other-run"), ("engine_commit", "other-engine")):
            with self.subTest(field=field):
                old = (self.root / "rank_7/raw_state.json").read_bytes()
                self.rewrite_rank(7, lambda doc: doc["identity"].update({field: value}))
                with self.assertRaises(ValueError):
                    state.publish_complete(self.root, identity())
                (self.root / "rank_7/raw_state.json").write_bytes(old)
        self.rewrite_rank(7, lambda doc: doc.update(rank=0))
        with self.assertRaises(ValueError):
            state.publish_complete(self.root, identity())

    def test_runtime_stage_and_topology_are_not_raw_contracts(self):
        for field, value in (("state", "after_postprocessing"), ("tp", 16),
                             ("pp", 2), ("ep", True), ("source_revision", "main")):
            altered = dict(identity(), **{field: value})
            with self.subTest(field=field), self.assertRaises(ValueError):
                state.validate_identity(altered)

    def test_missing_scale_or_wrong_index_extent_is_rejected(self):
        for failure in ("scale", "extent", "dtype"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                self.root = Path(folder)
                self.populate()
                path = self.root / "rank_3/tensor_index.json"
                index = json.loads(path.read_text())
                if failure == "scale":
                    del index["w13_weight_scale"]
                elif failure == "extent":
                    index["w13_weight"][0] = 100000
                else:
                    index["nonexpert.scale"][4] = "torch.uint8"
                path.write_text(json.dumps(index))
                self.rewrite_rank(3, lambda doc: doc["files"].update({path.name: {
                    "bytes": path.stat().st_size, "sha256": state.file_digest(path)}}))
                with self.assertRaises(ValueError):
                    state.publish_complete(self.root, identity())

    def test_payload_short_write_and_same_size_corruption(self):
        self.populate()
        state.publish_complete(self.root, identity())
        path = self.root / "rank_5/tensor.data_0"
        before = path.read_bytes()
        path.write_bytes(before[:-1])
        with self.assertRaisesRegex(ValueError, "Truncated"):
            state.validate_complete(self.root, identity())
        path.write_bytes(b"\xff" + before[1:])
        # Timed metadata admission relies on the separately verified immutable mount.
        state.validate_complete(self.root, identity())
        with self.assertRaisesRegex(ValueError, "checksum"):
            state.validate_complete(self.root, identity(), verify_payload=True)

    def test_alias_empty_dtype_buffer_and_expected_keys(self):
        rows = {"weight": tensor_row(), "scale": tensor_row("torch.float8_e8m0fnu", [1]),
                "buffer": tensor_row("torch.float32", [1], "buffer"),
                "empty": tensor_row("torch.int32", [2, 0])}
        rows["alias"] = dict(rows["weight"], alias="weight")
        state.validate_rows(rows)
        expected = {name: {key: row[key] for key in ("shape", "stride", "dtype", "kind", "persistent")}
                    for name, row in rows.items()}
        state.validate_expected(rows, expected)
        for change in ("missing_scale", "wrong_dtype", "bad_alias", "bad_buffer", "bad_layout"):
            damaged = copy.deepcopy(rows)
            if change == "missing_scale":
                del damaged["scale"]
            elif change == "wrong_dtype":
                damaged["scale"]["dtype"] = "torch.uint8"
            elif change == "bad_alias":
                damaged["weight"]["alias"] = "alias"
            elif change == "bad_buffer":
                del damaged["buffer"]["persistent"]
            else:
                damaged["weight"]["stride"] = [2]
            with self.subTest(change=change), self.assertRaises(ValueError):
                state.validate_expected(damaged, expected)

    def test_rank_or_payload_links_are_not_followed(self):
        self.populate()
        path = self.root / "rank_3/tensor.data_0"
        target = self.root / "original-payload"
        path.rename(target)
        path.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "linked"):
            state.publish_complete(self.root, identity())

    def test_duplicate_device_or_directory_fsync_failure_cannot_publish_complete(self):
        self.populate()
        self.rewrite_rank(7, lambda doc: doc.update(device_uuid="cpu-fixture-device-0"))
        with self.assertRaisesRegex(ValueError, "same device"):
            state.publish_complete(self.root, identity())
        self.rewrite_rank(7, lambda doc: doc.update(device_uuid="cpu-fixture-device-7"))
        with mock.patch.object(state.os, "fsync", side_effect=[None, OSError("directory fsync failed")]):
            with self.assertRaises(OSError):
                state.publish_complete(self.root, identity())
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_native_dtype_source_support_is_a_separate_static_requirement(self):
        source = (Path(__file__).resolve().parents[2] / "csrc/checkpoint/checkpoint.cpp").read_text()
        for dtype in ("torch.float8_e4m3fn", "torch.float8_e8m0fnu"):
            with self.subTest(dtype=dtype):
                self.assertTrue('"' + dtype + '"' in source, dtype)

    def test_full_v4_tp8_monolithic_experts_alone_exceed_h20_capacity(self):
        experts, hidden, intermediate, layers = 384, 7168, 3072 // 8, 61
        shapes = {
            "w13_weight": [experts, 2 * intermediate, hidden // 2],
            "w2_weight": [experts, hidden, intermediate // 2],
            "w13_weight_scale": [experts, 2 * intermediate, hidden // 32],
            "w2_weight_scale": [experts, hidden, intermediate // 32],
        }
        rows, methods = {}, {}
        for layer in range(layers):
            prefix = f"model.layers.{layer}.ffn.experts"
            methods[prefix] = {
                "class": "vllm.quantization.Mxfp4MoEMethod",
                "backends": {"mxfp4_backend": "Mxfp4MoeBackend.MARLIN"},
            }
            for suffix, shape in shapes.items():
                count = 1
                for dim in shape:
                    count *= dim
                rows[prefix + "." + suffix] = {"bytes": count}
        document = dict(tensors=rows, quant_methods=methods)
        result = state.check_monolithic_capacity(document, capacity_bytes=2 ** 60)
        self.assertEqual(result["minimum_peak_bytes"], 205513555968)
        self.assertGreater(result["minimum_peak_bytes"], 143771 * 1024 ** 2)
        with self.assertRaisesRegex(ValueError, "exceeds device HBM"):
            state.check_monolithic_capacity(document, 143771 * 1024 ** 2)


if __name__ == "__main__":
    unittest.main()
