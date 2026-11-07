"""Grouped manifest coverage and alias rejection, metadata-only TP8 fixtures."""
import json
from pathlib import Path
import tempfile
import threading
from unittest import mock
import unittest
from sllm_store import v4_grouped_state as grouped
from sllm_store import v4_state as raw
from test_v4_state import identity, tensor_row, rank_fixture


def state_fixture():
    rows = {"hc.scale": tensor_row("torch.float32", [3]),
            "attention.buffer": tensor_row("torch.bfloat16", [1], "buffer")}
    methods = {}
    for n in range(61):
        prefix = f"model.layers.{n}.experts"
        methods[prefix] = dict(**{"class": "quant.Mxfp4MoEMethod"},
            backends={"mxfp4_backend": "Mxfp4MoeBackend.MARLIN"})
        for suffix in grouped.EXPERT_SUFFIXES:
            rows[prefix + "." + suffix] = tensor_row()
    return rows, methods


class GroupedStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows, self.methods = state_fixture()

    def populate(self, ranks=8):
        self.results = []
        plan = grouped.partition_groups(self.rows, self.methods)
        for rank in range(ranks):
            entries = []
            for group in plan:
                directory = self.root / "groups" / group["id"]
                directory.mkdir(parents=True, exist_ok=True)
                rows = {name: self.rows[name] for name in group["tensors"]}
                rank_fixture(directory, rank, rows, grouped.group_methods(self.methods, rows))
                manifest = directory / f"rank_{rank}/raw_state.json"
                entries.append(dict(group, manifest_sha256=raw.file_digest(manifest)))
            directory = self.root / f"rank_{rank}"
            directory.mkdir()
            raw.write_once(directory / "grouped_state.json", dict(format=grouped.FORMAT,
                identity=identity(), rank=rank, groups=entries, quant_methods=self.methods))
            self.results.append(grouped.complete_grouped_export_rank(self.root, rank, identity()))

    def test_partition_preserves_native_module_order_and_complete_coverage(self):
        plan = grouped.partition_groups(self.rows, self.methods)
        self.assertEqual(len(plan), 62)
        self.assertEqual(plan[3]["module"], "model.layers.2.experts")
        names = [name for group in plan for name in group["tensors"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(self.rows))

    def test_missing_expert_scale_or_layer_is_rejected(self):
        self.rows.pop("model.layers.2.experts.w2_weight_scale")
        with self.assertRaisesRegex(ValueError, "weights/scales"):
            grouped.partition_groups(self.rows, self.methods)
        self.methods.pop("model.layers.2.experts")
        with self.assertRaisesRegex(ValueError, "61"):
            grouped.partition_groups(self.rows, self.methods)

    def test_cross_group_alias_rejected_and_same_group_alias_preserved(self):
        name = "model.layers.0.experts.w13_weight"
        self.rows[name + "_alias"] = dict(self.rows[name], alias=name)
        plan = grouped.partition_groups(self.rows, self.methods)
        self.assertIn(name + "_alias", plan[1]["tensors"])
        self.rows["outside_alias"] = dict(self.rows[name], alias=name)
        with self.assertRaisesRegex(ValueError, "Cross-group"):
            grouped.partition_groups(self.rows, self.methods)

    def test_publisher_verifies_all_eight_payloads_concurrently(self):
        self.populate()
        barrier = threading.Barrier(8, timeout=5)
        original = grouped.validate_grouped_rank
        def verify(root, rank, expected_identity, verify_payload=False):
            self.assertTrue(verify_payload)
            barrier.wait()
            return original(root, rank, expected_identity, verify_payload)
        with mock.patch.object(grouped, "validate_grouped_rank", side_effect=verify) as validation:
            grouped.publish_grouped_complete(self.root, identity(), self.results)
        self.assertEqual(validation.call_count, 8)
        self.assertTrue((self.root / "COMPLETE.json").exists())

    def test_all_rank_complete_and_no_overwrite(self):
        self.populate()
        complete = grouped.publish_grouped_complete(self.root, identity(), self.results)
        self.assertEqual(len(complete["ranks"]), 8)
        self.assertEqual(grouped.validate_grouped_complete(self.root, identity(), True), complete)
        with self.assertRaises(FileExistsError):
            grouped.publish_grouped_complete(self.root, identity(), self.results)
        with self.assertRaises((ValueError, FileNotFoundError)):
            raw.validate_complete(self.root, identity())

    def test_failed_conversion_cannot_publish_or_restore_even_with_all_receipts(self):
        self.populate()
        grouped.publish_grouped_complete(self.root, identity(), self.results)
        raw.write_once(self.root / "CONVERSION_FAILED.json", dict(error="interrupted"))
        with self.assertRaisesRegex(ValueError, "failure marker"):
            grouped.validate_grouped_complete(self.root, identity())
        with self.assertRaisesRegex(ValueError, "failure marker"):
            grouped.publish_grouped_complete(self.root, identity(), self.results)

    def test_partial_rank_never_publishes_complete(self):
        self.populate(7)
        with self.assertRaises(ValueError):
            grouped.publish_grouped_complete(self.root, identity(), self.results)
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_payload_corruption_and_missing_group_rejected(self):
        self.populate()
        path = self.root / "groups/expert_0002/rank_0/tensor.data_0"
        path.write_bytes(b"x" * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, "checksum"):
            grouped.publish_grouped_complete(self.root, identity(), self.results)
        self.assertFalse((self.root / "COMPLETE.json").exists())
        path.unlink()
        with self.assertRaises(ValueError):
            grouped.validate_grouped_rank(self.root, 0, identity())

    def test_group_order_and_manifest_hash_cannot_be_swapped(self):
        self.populate(1)
        path = self.root / "rank_0/grouped_state.json"
        document = json.loads(path.read_text())
        document["groups"][1], document["groups"][2] = document["groups"][2], document["groups"][1]
        path.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "order"):
            grouped.validate_grouped_rank(self.root, 0, identity())

    def test_expert_buffers_stay_in_remainder_allocation(self):
        self.rows["model.layers.0.experts.route_buffer"] = tensor_row("torch.int64", [1], "buffer")
        plan = grouped.partition_groups(self.rows, self.methods)
        self.assertIn("model.layers.0.experts.route_buffer", plan[0]["tensors"])
        self.assertNotIn("model.layers.0.experts.route_buffer", plan[1]["tensors"])

    def test_raw_files_without_successful_export_receipt_cannot_publish(self):
        self.populate()
        (self.root / "rank_7/export_done.json").unlink()
        with self.assertRaises(ValueError):
            grouped.publish_grouped_complete(self.root, identity(), self.results)
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_all_files_are_insufficient_when_worker_results_are_missing_or_duplicate(self):
        self.populate()
        for results in (self.results[:-1], self.results[:-1] + [self.results[0]]):
            with self.assertRaises(ValueError):
                grouped.publish_grouped_complete(self.root, identity(), results)
        self.assertFalse((self.root / "COMPLETE.json").exists())

    def test_wrong_backend_rejected(self):
        next(iter(self.methods.values()))["backends"]["mxfp4_backend"] = "other"
        with self.assertRaisesRegex(ValueError, "Marlin"):
            grouped.partition_groups(self.rows, self.methods)

if __name__ == "__main__":
    unittest.main()
