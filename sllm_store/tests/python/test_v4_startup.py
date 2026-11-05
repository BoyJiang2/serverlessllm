"""Cold startup contracts on actual router methods, fake RPCs, CPU only."""
import asyncio
from dataclasses import dataclass
import importlib.util
from pathlib import Path
import types
import unittest
from unittest import mock

BASE = Path(__file__).with_name("test_v4_router.py")
spec = importlib.util.spec_from_file_location("startup_router_fixture", BASE)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


@dataclass
class Instance:
    instance_id: str
    max_queue_length: int
    num_gpu: int
    ready: bool = False
    node_id: str = None
    backend_instance: object = None
    def __post_init__(self):
        self.lock = asyncio.Lock()


class StartupContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        cls = fixture.router_class()
        self.router = cls("v4", {"num_cpus": 1, "num_gpus": 8}, "vllm", {}, {})
        self.router._new_instance_id = lambda: "cold"
        self.router.running = True
        self.router.auto_scaling_config = {"min_instances": 1, "max_instances": 1}
        self.router.starting_inference_instances["cold"] = self.instance = Instance("cold", 1, 8)
        self.init = mock.AsyncMock()
        self.actor = types.SimpleNamespace(init_backend=types.SimpleNamespace(remote=self.init))
        self.start = mock.Mock()
        self.start.options.return_value.remote = mock.AsyncMock(return_value=self.actor)
        self.allocate = mock.AsyncMock(return_value="h20-node")
        self.router.model_loading_scheduler = types.SimpleNamespace(
            allocate_resource=types.SimpleNamespace(remote=self.allocate),
            deallocate_resource=types.SimpleNamespace(remote=mock.AsyncMock()))
        ns = cls._start_instance.__globals__
        ns.update(start_instance=self.start, InstanceHandle=Instance,
                  ray=types.SimpleNamespace(get_actor=mock.Mock(return_value=self.actor)))
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def test_ready_is_published_only_after_init_completes(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def init():
            entered.set()
            await release.wait()
        self.init.side_effect = init
        task = asyncio.create_task(self.router._start_instance("cold"))
        self.tasks.append(task)
        await asyncio.wait_for(entered.wait(), 0.5)
        self.assertFalse(self.instance.ready)
        self.assertIs(self.instance.backend_instance, self.actor)
        self.assertEqual(self.instance.node_id, "h20-node")
        self.assertNotIn("cold", self.router.ready_inference_instances)
        release.set()
        self.assertEqual(await task, "cold")
        self.assertTrue(self.instance.ready)
        self.assertIs(self.router.ready_inference_instances["cold"], self.instance)
        self.assertNotIn("cold", self.router.starting_inference_instances)

    async def test_init_failure_retains_node_actor_and_nonready_owner(self):
        self.init.side_effect = RuntimeError("init failed")
        with self.assertRaisesRegex(RuntimeError, "init failed"):
            await self.router._start_instance("cold")
        self.assertIs(self.router.starting_inference_instances["cold"], self.instance)
        self.assertFalse(self.instance.ready)
        self.assertEqual(self.instance.node_id, "h20-node")
        self.assertIs(self.instance.backend_instance, self.actor)
        self.router.model_loading_scheduler.deallocate_resource.remote.assert_not_called()

    async def test_actor_rpc_failure_retains_confirmed_node(self):
        self.start.options.return_value.remote.side_effect = RuntimeError("creation outcome unknown")
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await self.router._start_instance("cold")
        self.assertFalse(self.instance.ready)
        self.assertEqual(self.instance.node_id, "h20-node")
        self.assertIsNone(self.instance.backend_instance)
        self.router.model_loading_scheduler.deallocate_resource.remote.assert_not_called()

    async def test_allocation_failure_retains_id_without_creating_actor(self):
        self.allocate.side_effect = RuntimeError("allocation outcome unknown")
        with self.assertRaisesRegex(RuntimeError, "outcome unknown"):
            await self.router._start_instance("cold")
        self.assertIs(self.router.starting_inference_instances["cold"], self.instance)
        self.assertIsNone(self.instance.node_id)
        self.start.options.assert_not_called()

    async def create_failed(self, cancelled=False):
        self.router.starting_inference_instances.clear()
        if cancelled:
            entered = asyncio.Event()
            async def allocate(*args):
                entered.set()
                await asyncio.Future()
            self.allocate.side_effect = allocate
        else:
            self.init.side_effect = RuntimeError("native cold failure")
        self.assertEqual(await self.router._create_instance(), "cold")
        tasks = getattr(self.router, "_startup_tasks", {})
        self.assertIn("cold", tasks)
        task = tasks["cold"]
        self.tasks.append(task)
        if cancelled:
            await entered.wait()
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        return task

    async def test_start_task_failure_is_observed_and_future_requests_fail(self):
        await self.create_failed()
        self.assertIsInstance(self.router._startup_error, RuntimeError)
        self.assertFalse(self.router._startup_tasks)
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await self.router.inference({}, "generate")
        self.assertEqual(self.router.request_count, 0)
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await self.router._create_instance()
        self.assertIs(self.router.starting_inference_instances["cold"].backend_instance, self.actor)

    async def test_start_task_cancellation_is_explicit_failure_and_retains_owner(self):
        await self.create_failed(cancelled=True)
        self.assertIsInstance(self.router._startup_error, RuntimeError)
        self.assertIn("cold", self.router.starting_inference_instances)
        self.assertFalse(self.router.ready_inference_instances)

    async def test_waiting_request_gets_failure_and_router_count_is_released(self):
        request = asyncio.create_task(self.router.inference({}, "generate"))
        worker = asyncio.create_task(self.router._load_balancer_loop())
        self.tasks.extend([request, worker])
        await asyncio.sleep(0)
        await self.create_failed()
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await asyncio.wait_for(request, 2)
        self.assertEqual(self.router.request_count, 0)
        self.assertFalse(worker.done())

    async def test_failed_start_cannot_report_successful_shutdown_or_discard_owner(self):
        await self.create_failed()
        owner = self.router.starting_inference_instances["cold"]
        with self.assertRaisesRegex(RuntimeError, "quiescent"):
            await self.router.shutdown()
        self.assertIs(self.router.starting_inference_instances["cold"], owner)
        self.assertIs(owner.backend_instance, self.actor)
        self.router.model_loading_scheduler.deallocate_resource.remote.assert_not_called()

    async def test_successful_start_task_is_retired_without_latching_error(self):
        self.router.starting_inference_instances.clear()
        self.assertEqual(await self.router._create_instance(), "cold")
        task = self.router._startup_tasks["cold"]
        await task
        await asyncio.sleep(0)
        self.assertFalse(self.router._startup_tasks)
        self.assertIsNone(self.router._startup_error)
        self.assertTrue(self.router.ready_inference_instances["cold"].ready)

    async def test_auto_scaler_does_not_retry_failed_start(self):
        await self.create_failed()
        self.router._create_instance = mock.AsyncMock(side_effect=AssertionError("unexpected retry"))
        self.router.loop_interval = 0
        worker = asyncio.create_task(self.router._auto_scaler_loop())
        self.tasks.append(worker)
        for _ in range(5):
            await asyncio.sleep(0)
        self.assertFalse(worker.done())
        self.router._create_instance.assert_not_called()


if __name__ == "__main__":
    unittest.main()
