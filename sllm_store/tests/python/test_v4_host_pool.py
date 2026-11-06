"""Actual grouped service host lifecycle; small cohorts and controlled native RPCs."""
import asyncio
import types
import unittest
from unittest import mock

import test_v4_service as fixture


class GroupedHostPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = fixture.manager_source()
        self.local = self.ns["SllmLocalStore"].__new__(self.ns["SllmLocalStore"])
        self.local.node_id = "node-2"
        self.local.lock = asyncio.Lock()
        self.local.hardware_info = dict(disk_bandwidth=10)
        self.local.chunk_size = 4
        self.local.pinned_memory_pool_chunks = 3
        self.local.pinned_memory_pool_usage = 0
        self.local.pinned_memory_pool = {}
        self.local.queued_models = {}
        self.local.io_queue = []
        # Tiny cohort for lifecycle faults; full TP8/496 coverage lives in test_v4_service.
        self.local.disk_models = dict(v4=(["g/0", "g/1", "g/2"], 3))
        self.local.grouped_models = dict(v4={})
        self.local.grouped_chunks = dict(v4=3)
        self.local.grouped_host_attempts = {}
        self.local.grouped_host_waiters = {}
        self.resident, self.tokens, self.next_token = set(), {}, 0
        def load(path):
            self.next_token += 1
            token = f"read-{self.next_token}"
            self.resident.add(path)
            self.tokens[path] = token
            return token
        def confirm(path, token):
            if path not in self.resident or self.tokens[path] != token:
                raise RuntimeError("not resident")
        def unload(path):
            self.resident.remove(path)
            return types.SimpleNamespace(model_path=path)
        self.client = types.SimpleNamespace(load_cpu_confirmed=mock.Mock(side_effect=load),
            confirm_cpu_model_loaded=mock.Mock(side_effect=confirm),
            unload_from_cpu=mock.Mock(side_effect=unload))
        self.local.client = self.client

    async def prefetch(self):
        caller = asyncio.create_task(self.local.load_to_host("v4"))
        await asyncio.sleep(0)
        loader = asyncio.create_task(self.local.loading_loop())
        try:
            return await asyncio.wait_for(caller, 2)
        finally:
            loader.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await loader

    async def test_cohort_completion_pool_accounting_eviction_and_relaunch(self):
        self.assertTrue(await self.prefetch())
        self.assertEqual(self.resident, {"g/0", "g/1", "g/2"})
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)
        self.assertIn("v4", self.local.pinned_memory_pool)
        old_tokens = dict(self.tokens)
        self.assertTrue(await self.local._lru_eviction(1))
        self.assertFalse(self.resident)
        self.assertFalse(self.local.pinned_memory_pool)
        self.assertEqual(self.local.pinned_memory_pool_usage, 0)
        self.assertFalse(self.local.grouped_host_waiters)
        self.assertTrue(await self.prefetch())
        self.assertTrue(all(self.tokens[p] != t for p, t in old_tokens.items()))
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)

    async def test_complete_cohort_capacity_includes_per_group_rounding(self):
        self.local.pinned_memory_pool_chunks = 2  # sum bytes fits one chunk; cohort needs three.
        with self.assertRaises(MemoryError):
            await self.local.load_to_host("v4")
        self.client.load_cpu_confirmed.assert_not_called()
        self.assertFalse(self.local.io_queue)
        self.assertEqual(self.local.pinned_memory_pool_usage, 0)

    async def test_partial_copy_failure_retains_full_reservation_and_inflight(self):
        self.client.load_cpu_confirmed.side_effect = ["read-0", RuntimeError("disk failed")]
        with self.assertRaisesRegex(RuntimeError, "disk failed"):
            await self.prefetch()
        attempt = self.local.grouped_host_attempts["v4"]
        self.assertEqual(attempt["receipts"], {"g/0": "read-0"})
        self.assertEqual(attempt["inflight"], "g/1")
        self.assertTrue(attempt["failed"])
        self.assertNotIn("v4", self.local.pinned_memory_pool)
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)
        self.assertFalse(self.local.io_queue)
        with self.assertRaisesRegex(RuntimeError, "isolate"):
            await self.local.load_to_host("v4")

    async def test_evicted_group_cannot_reuse_a_successful_historical_receipt(self):
        await self.prefetch()
        self.resident.remove("g/1")  # External native eviction after the first complete observation.
        with self.assertRaisesRegex(RuntimeError, "not resident"):
            await self.local.load_to_host("v4")
        self.assertNotIn("v4", self.local.pinned_memory_pool)
        self.assertTrue(self.local.grouped_host_attempts["v4"]["failed"])
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)

    async def test_partial_unload_never_releases_the_cohort_reservation(self):
        await self.prefetch()
        self.client.unload_from_cpu.side_effect = [types.SimpleNamespace(model_path="g/0"), False]
        self.assertFalse(await self.local._lru_eviction(1))
        attempt = self.local.grouped_host_attempts["v4"]
        self.assertEqual(attempt["unloaded"], ["g/0"])
        self.assertEqual(attempt["inflight"], "g/1")
        self.assertTrue(attempt["failed"])
        self.assertNotIn("v4", self.local.pinned_memory_pool)
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)

    async def test_cancelling_a_waiter_does_not_cancel_the_native_load_owner(self):
        caller = asyncio.create_task(self.local.load_to_host("v4"))
        await asyncio.sleep(0)
        completion = self.local.grouped_host_waiters["v4"]
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertFalse(completion.cancelled())
        loader = asyncio.create_task(self.local.loading_loop())
        try:
            self.assertTrue(await asyncio.wait_for(asyncio.shield(completion), 2))
        finally:
            loader.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await loader
        self.assertIn("v4", self.local.pinned_memory_pool)

    async def test_cancelling_the_loader_keeps_failed_attempt_and_reserved_chunks(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args):
            entered.set()
            await release.wait()
        caller = asyncio.create_task(self.local.load_to_host("v4"))
        await asyncio.sleep(0)
        with mock.patch.object(asyncio, "to_thread", side_effect=blocked):
            loader = asyncio.create_task(self.local.loading_loop())
            await entered.wait()
            loader.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await loader
        with self.assertRaises(asyncio.CancelledError):
            await caller
        self.assertTrue(self.local.grouped_host_attempts["v4"]["failed"])
        self.assertEqual(self.local.grouped_host_attempts["v4"]["inflight"], "g/0")
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)

    async def test_all_groups_are_reconfirmed_before_the_pool_marker_is_published(self):
        self.client.confirm_cpu_model_loaded.side_effect = RuntimeError("cohort changed")
        with self.assertRaisesRegex(RuntimeError, "cohort changed"):
            await self.prefetch()
        self.assertEqual(self.client.load_cpu_confirmed.call_count, 3)
        self.assertNotIn("v4", self.local.pinned_memory_pool)
        self.assertEqual(self.local.pinned_memory_pool_usage, 3)

    async def test_store_manager_exposes_fresh_residency_confirmation(self):
        await self.prefetch()
        manager = self.ns["StoreManager"]()
        manager.local_servers["node-2"] = self.local
        self.assertTrue(await manager.confirm_grouped_host("node-2", "v4"))

    async def test_explicit_host_unload_is_confirmed_and_refuses_pending_loads(self):
        self.local.queued_models["v4"] = True
        self.assertFalse(await self.local.unload_grouped_host("v4"))
        self.client.unload_from_cpu.assert_not_called()
        self.local.queued_models.clear()
        await self.prefetch()
        manager = self.ns["StoreManager"]()
        manager.local_servers["node-2"] = self.local
        self.assertTrue(await manager.unload_grouped_host("node-2", "v4"))
        self.assertFalse(self.resident)
        self.assertEqual(self.local.pinned_memory_pool_usage, 0)
        self.assertFalse(await manager.unload_grouped_host("node-2", "v4"))
