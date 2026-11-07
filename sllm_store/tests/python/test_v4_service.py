"""Worker-local admission and actual StoreManager AST; no Ray/Store/GPU runtime."""
import ast
import asyncio
import copy
import json
import logging
import os
from pathlib import Path
from typing import List, Mapping, Optional
import types
import unittest
from unittest import mock

from sllm_store import v4_grouped_state as grouped
from sllm_store import v4_service as service
from sllm_store import v4_state as raw
import test_v4_grouped_state as fixtures
from test_v4_state import identity


def config(root):
    return dict(load_format="sllm_v4_grouped_store", tensor_parallel_size=8, tokenizer_mode="deepseek_v4",
                torch_dtype="bfloat16", revision=raw.REVISION, tokenizer_revision=raw.REVISION,
                kv_cache_dtype="fp8", seed=20261105, distributed_executor_backend="mp",
                attention_config=dict(use_fp4_indexer_cache=False),
                enforce_eager=True, enable_prefix_caching=False,
                max_model_len=2048, max_num_seqs=1, max_num_batched_tokens=2048,
                pretrained_model_name_or_path="/mnt/raid/models/DeepSeek-V4-Pro",
                model_loader_extra_config=dict(store_path=str(root), identity=identity()))


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GroupedStateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.populate()
        self.root = self.fixture.root
        grouped.publish_grouped_complete(self.root, identity(), self.fixture.results)
        self.config = config(self.root)

    def test_explicit_revision_and_native_kv_profile_is_accepted(self):
        backend = dict(self.config, revision=raw.REVISION, tokenizer_revision=raw.REVISION,
                       kv_cache_dtype="fp8", attention_config={"use_fp4_indexer_cache": False})
        config = dict(model="v4", backend="vllm", num_gpus=8,
                      auto_scaling_config=dict(min_instances=0, max_instances=1), backend_config=backend)
        self.assertEqual(service.freeze_registration_config(config), config)
        kept = copy.deepcopy(config); kept['auto_scaling_config']['keep_alive'] = 3600
        self.assertEqual(service.freeze_registration_config(kept), kept)
        for value in (1, 3600.0, True, None):
            invalid = copy.deepcopy(config); invalid['auto_scaling_config']['keep_alive'] = value
            with self.assertRaises(ValueError):service.freeze_registration_config(invalid)
        for key, value in (("revision", None), ("revision", "main"),
                           ("tokenizer_revision", "main"), ("seed", 0),
                           ("distributed_executor_backend", None), ("kv_cache_dtype", "auto"),
                           ("kv_cache_dtype", "bfloat16"),
                           ("attention_config", {"use_fp4_indexer_cache": True}),
                           ("attention_config", {"use_fp4_indexer_cache": 0})):
            with self.subTest(key=key, value=value):
                changed = copy.deepcopy(config)
                changed["backend_config"][key] = value
                with self.assertRaises(ValueError):
                    service.freeze_registration_config(changed)

    def test_all_496_identifiers_and_native_partition_sizes(self):
        plan = service.grouped_registration_plan(self.config, self.root.parent)
        self.assertEqual(len(plan), 496)
        self.assertEqual(len(dict(plan)), 496)
        self.assertEqual(plan[0][0], f"{self.root.name}/groups/remainder/rank_0")
        self.assertEqual(plan[-1][0], f"{self.root.name}/groups/expert_0060/rank_7")
        self.assertEqual(plan[1][1], 8)  # Four uint8[2] tensors; JSON excluded.

    def test_payload_hashes_are_checked_before_plan_returns(self):
        path = self.root / "groups/expert_0060/rank_7/tensor.data_0"
        path.write_bytes(b"x" * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, "checksum"):
            service.grouped_registration_plan(self.config, self.root.parent)

    def test_wrong_loader_topology_root_or_incomplete_store_is_rejected(self):
        for value in (1, True, "8"):
            self.config["tensor_parallel_size"] = value
            with self.assertRaisesRegex(ValueError, "TP8"):
                service.grouped_registration_plan(self.config, self.root.parent)
        self.config["tensor_parallel_size"] = 8
        with self.assertRaises(ValueError):
            service.grouped_registration_plan(self.config, self.root / "groups")
        self.config["model_loader_extra_config"]["store_path"] = "relative"
        with self.assertRaisesRegex(ValueError, "absolute"):
            service.grouped_registration_plan(self.config, self.root.parent)
        self.config = config(self.root)
        (self.root / "COMPLETE.json").unlink()
        with self.assertRaises(ValueError):
            service.grouped_registration_plan(self.config, self.root.parent)

    def test_partition_gap_rejected_even_with_consistent_hashes_and_receipts(self):
        directory = self.root / "groups/expert_0000/rank_0"
        (directory / "tensor.data_0").rename(directory / "tensor.data_1")
        path = directory / "raw_state.json"
        document = json.loads(path.read_text())
        document["files"]["tensor.data_1"] = document["files"].pop("tensor.data_0")
        path.write_text(json.dumps(document))
        path = self.root / "rank_0/grouped_state.json"
        document = json.loads(path.read_text())
        document["groups"][1]["manifest_sha256"] = raw.file_digest(directory / "raw_state.json")
        path.write_text(json.dumps(document))
        (self.root / "rank_0/export_done.json").unlink()
        self.fixture.results[0] = grouped.complete_grouped_export_rank(self.root, 0, identity())
        (self.root / "COMPLETE.json").unlink()
        grouped.publish_grouped_complete(self.root, identity(), self.fixture.results)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            service.grouped_registration_plan(self.config, self.root.parent)


def manager_source():
    source = Path(__file__).resolve().parents[3] / "sllm/store_manager.py"
    tree = ast.parse(source.read_text())
    selected = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))
                and node.name in ("SllmLocalStore", "StoreManager", "inspect_v4_grouped_store")]
    for node in selected:
        node.decorator_list = []
    namespace = dict(asyncio=asyncio, copy=copy, os=os, time=__import__("time"),
                     List=List, Mapping=Mapping, Optional=Optional,
                     SllmStoreClient=object,
                     logger=logging.getLogger("cpu-store-test"))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = manager_source()
        self.plan = [(f"v4/groups/{n}/rank_0", n + 1) for n in range(496)]
        self.inspect = types.SimpleNamespace(options=mock.Mock())
        self.inspect.options.return_value = types.SimpleNamespace(
            remote=mock.AsyncMock(return_value=self.plan))
        self.ns["inspect_v4_grouped_store"] = self.inspect
        self.client = types.SimpleNamespace(register_model=mock.Mock(side_effect=[s for _, s in self.plan]))
        self.local = self.ns["SllmLocalStore"].__new__(self.ns["SllmLocalStore"])
        self.local.node_id = "node-2"
        self.local.client = self.client
        self.local.disk_models = {}
        self.local.grouped_models = {}
        self.local.grouped_chunks = {}
        self.local.grouped_host_attempts = {}
        self.local.grouped_host_waiters = {}
        self.local.chunk_size = 4
        self.local.failed_grouped_registrations = {}
        self.local.lock = asyncio.Lock()
        self.config = config(Path("/data/complete-v4"))

    async def test_worker_pinned_inspection_and_all_registration_before_publication(self):
        size = await self.local.register_model("v4", "vllm", self.config)
        self.assertEqual(size, sum(s for _, s in self.plan))
        self.inspect.options.assert_called_once_with(resources={"worker_id_node-2": 0.01})
        self.assertEqual(self.client.register_model.call_args_list,
                         [mock.call(p) for p, _ in self.plan])
        self.assertEqual(self.local.disk_models["v4"], ([p for p, _ in self.plan], size))
        self.assertFalse(self.local.failed_grouped_registrations)
        chunks = sum((size + 3) // 4 for _, size in self.plan)
        self.assertEqual(self.local.grouped_chunks["v4"], chunks)
        self.assertGreater(chunks, (size + 3) // 4)

    async def test_negative_wrong_size_bool_and_rpc_exception_never_publish(self):
        for result in (-1, 999, True, RuntimeError("rpc failed")):
            with self.subTest(result=result):
                self.local.failed_grouped_registrations.clear()
                self.client.register_model.side_effect = [1, result]
                with self.assertRaises(RuntimeError):
                    await self.local.register_model("v4", "vllm", self.config)
                self.assertNotIn("v4", self.local.disk_models)
                attempt = self.local.failed_grouped_registrations["v4"]
                self.assertEqual(attempt["registered"], [self.plan[0][0]])
                self.assertEqual(attempt["inflight"], self.plan[1][0])
                self.assertTrue(attempt["failed"])
                count = self.client.register_model.call_count
                with self.assertRaisesRegex(RuntimeError, "isolate"):
                    await self.local.register_model("v4", "vllm", self.config)
                self.assertEqual(self.client.register_model.call_count, count)

    async def test_partial_registration_cannot_be_retried_under_another_name(self):
        self.client.register_model.side_effect = [1, -1]
        with self.assertRaises(RuntimeError):
            await self.local.register_model("v4", "vllm", self.config)
        count = self.client.register_model.call_count
        with self.assertRaisesRegex(RuntimeError, "dataset registration failed"):
            await self.local.register_model("renamed-v4", "vllm", self.config)
        self.assertEqual(self.client.register_model.call_count, count)

    async def test_repeated_registration_is_idempotent_only_for_same_frozen_identity(self):
        size = await self.local.register_model("v4", "vllm", self.config)
        self.assertEqual(await self.local.register_model("v4", "vllm", self.config), size)
        self.assertEqual(self.client.register_model.call_count, 496)
        self.config["model_loader_extra_config"]["identity"]["conversion_id"] = "different"
        with self.assertRaisesRegex(ValueError, "identity/path changed"):
            await self.local.register_model("v4", "vllm", self.config)

    async def test_cancelled_registration_retains_inflight_without_publication(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            entered.set()
            await release.wait()
        with mock.patch.object(asyncio, "to_thread", side_effect=blocked):
            task = asyncio.create_task(self.local.register_model("v4", "vllm", self.config))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        attempt = self.local.failed_grouped_registrations["v4"]
        self.assertTrue(attempt["failed"])
        self.assertEqual(attempt["inflight"], self.plan[0][0])
        self.assertFalse(self.local.disk_models)

    async def test_inspection_failure_or_incomplete_plan_makes_no_store_rpc(self):
        remote = self.inspect.options.return_value.remote
        remote.side_effect = ValueError("corrupt payload")
        with self.assertRaises(ValueError):
            await self.local.register_model("v4", "vllm", self.config)
        remote.side_effect = None
        for plan in (self.plan[:-1], [self.plan[0]] * 496):
            remote.return_value = plan
            with self.assertRaisesRegex(ValueError, "incomplete"):
                await self.local.register_model("v4", "vllm", self.config)
        self.client.register_model.assert_not_called()
        self.assertFalse(self.local.disk_models)

    async def test_legacy_rank_registration_is_unchanged(self):
        self.client.register_model.side_effect = [11, 12]
        size = await self.local.register_model("old", "vllm", dict(tensor_parallel_size=2))
        self.assertEqual(size, 23)
        self.assertEqual(self.local.disk_models["old"],
                         (["vllm/old/rank_0", "vllm/old/rank_1"], 23))
        self.inspect.options.assert_not_called()

    async def test_manager_skips_legacy_converter_and_only_publishes_successful_node(self):
        manager = self.ns["StoreManager"]()
        manager.local_servers = {"node-2": self.local}
        self.ns["get_worker_nodes"] = lambda: {"node-2": dict(address="unused")}
        manager.download_vllm_model = mock.AsyncMock(side_effect=AssertionError("legacy converter"))
        await manager.register(dict(model="v4", backend="vllm", num_gpus=8, auto_scaling_config=dict(min_instances=0, max_instances=1), backend_config=self.config))
        manager.download_vllm_model.assert_not_called()
        self.assertTrue(manager.model_storage_info["v4"]["node-2"])
        self.assertEqual(manager.model_info["v4"], sum(s for _, s in self.plan))

    async def test_manager_registration_failure_never_publishes_placement(self):
        manager = self.ns["StoreManager"]()
        manager.local_servers = {"node-2": self.local}
        self.ns["get_worker_nodes"] = lambda: {"node-2": dict(address="unused")}
        self.client.register_model.side_effect = [-1]
        with self.assertRaises(RuntimeError):
            await manager.register(dict(model="v4", backend="vllm", num_gpus=8, auto_scaling_config=dict(min_instances=0, max_instances=1), backend_config=self.config))
        self.assertNotIn("v4", manager.model_info)
        self.assertEqual(manager.model_storage_info["v4"], {})

    async def test_prepared_native_sf_switch_reuses_binding_without_legacy_conversion(self):
        manager = self.ns["StoreManager"]()
        manager.local_servers = {"node-2": self.local}
        self.ns["get_worker_nodes"] = lambda: {"node-2": dict(address="unused")}
        manager.download_vllm_model = mock.AsyncMock(side_effect=AssertionError("legacy converter"))
        registration = dict(model="v4", backend="vllm", num_gpus=8,
                            auto_scaling_config=dict(min_instances=0, max_instances=1), backend_config=self.config)
        registration['auto_scaling_config']['keep_alive'] = 3600
        await manager.register(registration)
        # Use the same keeper when preparing the binding and switching loader.
        native = copy.deepcopy(registration)
        native["backend_config"]["load_format"] = "safetensors"
        native["backend_config"].pop("model_loader_extra_config")
        await manager.register(native)
        manager.download_vllm_model.assert_not_called()
        self.assertEqual(self.client.register_model.call_count, 496)

    async def test_prefetch_placement_uses_the_registered_model_name(self):
        manager = self.ns["StoreManager"]()
        manager.local_servers = {"node-2": self.local}
        self.ns["get_worker_nodes"] = lambda: {"node-2": dict(address="unused")}
        manager.load_to_host = mock.AsyncMock(return_value=True)
        await manager.register(dict(model="v4", backend="vllm", num_gpus=8, auto_scaling_config=dict(min_instances=0, max_instances=1), backend_config=self.config,
                                    placement_config=dict(memory_pool=["node-2"])))
        manager.load_to_host.assert_awaited_once_with("node-2", "v4")


if __name__ == "__main__":
    unittest.main()
