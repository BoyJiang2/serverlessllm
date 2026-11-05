"""Actual router AST and asyncio; no Ray, Torch, engine or GPU imports."""
import ast
import asyncio
import logging
from pathlib import Path
import types
from typing import Dict, List, Optional
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]


def router_class():
    tree = ast.parse((ROOT / "sllm/routers/roundrobin_router.py").read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "RoundRobinRouter"]
    ns = dict(asyncio=asyncio, Dict=Dict, List=List, Optional=Optional,
              SllmRouter=object, logger=logging.getLogger("router-cpu"))
    exec(compile(tree, "actual-roundrobin-router", "exec"), ns)
    return ns["RoundRobinRouter"]


class RouterContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.router = router_class()("v4", {"num_gpus": 8}, "vllm", {}, {})
        self.router.running = True
        self.router.loop_interval = 0
        self.instance = types.SimpleNamespace(concurrency=0)
        async def add(count):
            self.instance.concurrency += count
            return True
        self.instance.add_requests = add
        self.instance.check_request_queue = mock.AsyncMock(return_value=True)
        self.instance.backend_instance = types.SimpleNamespace(generate=types.SimpleNamespace(
            remote=mock.AsyncMock(return_value={"ok": True})))
        self.router.ready_inference_instances["one"] = self.instance
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def request(self):
        task = asyncio.create_task(self.router.inference({}, "generate"))
        self.tasks.append(task)
        future = await asyncio.wait_for(self.router.request_queue.get(), 0.5)
        return task, future

    async def allocate(self, future):
        await self.instance.add_requests(1)
        future.set_result("one")

    async def test_backend_failure_releases_slot_and_request_count(self):
        self.instance.backend_instance.generate.remote.side_effect = RuntimeError("engine failed")
        task, future = await self.request()
        await self.allocate(future)
        with self.assertRaisesRegex(RuntimeError, "engine failed"):
            await task
        self.assertEqual(self.instance.concurrency, 0)
        self.assertEqual(self.router.request_count, 0)

    async def test_cancel_running_request_releases_counts(self):
        started = asyncio.Event()
        async def generate(**kwargs):
            started.set()
            await asyncio.Future()
        self.instance.backend_instance.generate.remote = generate
        task, future = await self.request()
        await self.allocate(future)
        await asyncio.wait_for(started.wait(), 0.5)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.instance.concurrency, 0)
        self.assertEqual(self.router.request_count, 0)

    async def test_stopped_router_does_not_enqueue_or_count(self):
        self.router.running = False
        self.assertIn("error", await self.router.inference({}, "generate"))
        self.assertEqual(self.router.request_count, 0)
        self.assertTrue(self.router.request_queue.empty())

    async def test_cancel_queued_request_releases_router_count(self):
        task, future = await self.request()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertTrue(future.cancelled())
        self.assertEqual(self.router.request_count, 0)

    async def test_cancel_after_allocation_before_resumption_releases_slot(self):
        task, future = await self.request()
        await self.allocate(future)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.instance.concurrency, 0)
        self.assertEqual(self.router.request_count, 0)

    async def test_load_balancer_skips_cancelled_queue_item_and_serves_next(self):
        cancelled = self.router.loop.create_future()
        cancelled.cancel()
        next_request = self.router.loop.create_future()
        await self.router.request_queue.put(cancelled)
        await self.router.request_queue.put(next_request)
        original_sleep = asyncio.sleep
        async def fast_sleep(_):
            await original_sleep(0)
        with mock.patch.object(asyncio, "sleep", fast_sleep):
            worker = asyncio.create_task(self.router._load_balancer_loop())
            self.tasks.append(worker)
            result = await asyncio.wait_for(next_request, 0.5)
            self.assertEqual(result, "one")
            self.assertFalse(worker.done())
            self.assertEqual(self.instance.concurrency, 1)

    async def test_cancel_while_slot_lock_waits_does_not_kill_balancer(self):
        waiting, release = asyncio.Event(), asyncio.Event()
        original_add = self.instance.add_requests
        async def delayed_add(count):
            if count > 0:
                waiting.set()
                await release.wait()
            return await original_add(count)
        self.instance.add_requests = delayed_add
        first = self.router.loop.create_future()
        second = self.router.loop.create_future()
        await self.router.request_queue.put(first)
        await self.router.request_queue.put(second)
        original_sleep = asyncio.sleep
        async def fast_sleep(_):
            await original_sleep(0)
        with mock.patch.object(asyncio, "sleep", fast_sleep):
            worker = asyncio.create_task(self.router._load_balancer_loop())
            self.tasks.append(worker)
            await asyncio.wait_for(waiting.wait(), 0.5)
            first.cancel()
            release.set()
            self.assertEqual(await asyncio.wait_for(second, 0.5), "one")
            self.assertFalse(worker.done())
            self.assertEqual(self.instance.concurrency, 1)

    async def test_success_preserves_response_and_releases_counts(self):
        task, future = await self.request()
        await self.allocate(future)
        self.assertEqual(await task, {"ok": True})
        self.assertEqual(self.instance.concurrency, 0)
        self.assertEqual(self.router.request_count, 0)

    async def test_invalid_lora_releases_reserved_slot(self):
        self.router.enable_lora = True
        self.router.loaded_lora_adapters = {}
        task = asyncio.create_task(self.router.inference({"lora_adapter_name": "absent"}, "generate"))
        self.tasks.append(task)
        future = await self.router.request_queue.get()
        await self.allocate(future)
        self.assertIn("error", await task)
        self.assertEqual(self.instance.concurrency, 0)
        self.assertEqual(self.router.request_count, 0)


if __name__ == "__main__":
    unittest.main()
