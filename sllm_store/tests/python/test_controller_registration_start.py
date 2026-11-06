"""CPU regression of the real controller/router methods; no Ray or ML imports."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Dict
import unittest


ROOT = Path(__file__).resolve().parents[3]


def real_method(relative, name, namespace):
    tree = ast.parse((ROOT / relative).read_text())
    node = next(node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), relative, "exec"), namespace)
    return namespace[name]


class ControllerRegistrationStartTest(unittest.IsolatedAsyncioTestCase):
    async def make_fixture(self, failure=None):
        started, release = asyncio.Event(), asyncio.Event()
        scheduler = object()
        namespace = {
            "asyncio": asyncio, "Dict": Dict,
            "logger": SimpleNamespace(info=lambda *args: None, error=lambda *args: None),
            "ray": SimpleNamespace(get_actor=lambda name: scheduler),
        }
        register = real_method("sllm/controller.py", "register", namespace)
        start = real_method("sllm/routers/roundrobin_router.py", "start", namespace)
        inference = real_method("sllm/routers/roundrobin_router.py", "inference", namespace)
        async def noop(*args, **kwargs):
            return None
        async def generate(**kwargs):
            return {"ok": True}
        class Queue:
            async def put(self, future):
                future.set_result("instance")
        router = SimpleNamespace(
            running=False, running_lock=asyncio.Lock(), auto_scaling_lock=asyncio.Lock(),
            _auto_scaler_loop=noop, _load_balancer_loop=noop, model_name="test",
            request_count=0, request_count_lock=asyncio.Lock(), idle_time=0,
            idle_time_lock=asyncio.Lock(), loop=asyncio.get_running_loop(),
            request_queue=Queue(), instance_management_lock=asyncio.Lock(),
            ready_inference_instances={"instance": SimpleNamespace(
                backend_instance=SimpleNamespace(generate=SimpleNamespace(remote=generate)),
                add_requests=noop)},
            enable_lora=False,
        )
        pending = []
        def start_remote(config):
            async def delayed():
                started.set()
                await release.wait()
                if failure is not None:
                    raise failure
                await start(router, config)
            task = asyncio.create_task(delayed())
            pending.append(task)
            return task
        router.start = SimpleNamespace(remote=start_remote)
        factory = SimpleNamespace(options=lambda **kwargs: SimpleNamespace(remote=lambda *args: router))
        controller = SimpleNamespace(
            running=True, metadata_lock=asyncio.Lock(), registered_models={},
            request_routers={}, store_manager=SimpleNamespace(register=SimpleNamespace(remote=noop)),
            router_cls=factory,
        )
        config = {"model": "test", "backend": "vllm", "num_gpus": 8,
                  "auto_scaling_config": {"min_instances": 0, "max_instances": 1, "keep_alive": 3600},
                  "placement_config": {"memory_pool": []}}
        return controller, router, register, inference, config, started, release, pending

    async def test_delayed_start_blocks_registration_publication_and_immediate_inference(self):
        controller, router, register, inference, config, started, release, pending = await self.make_fixture()
        task = asyncio.create_task(register(controller, config))
        try:
            await asyncio.wait_for(started.wait(), 1)
            self.assertFalse(task.done(), "registration returned before router start completed")
            self.assertEqual(controller.registered_models, {})
            self.assertEqual(controller.request_routers, {})
            self.assertEqual(await inference(router, {}, "generate"), {"error": "Instance stopped"})
            release.set()
            await asyncio.wait_for(task, 1)
            self.assertIs(controller.request_routers["test"], router)
            self.assertEqual(router.auto_scaling_config["min_instances"], 0)
            self.assertEqual(await inference(router, {}, "generate"), {"ok": True})
        finally:
            release.set()
            await asyncio.gather(task, *pending, return_exceptions=True)

    async def test_start_failure_propagates_and_does_not_publish_registration(self):
        failure = RuntimeError("router start failed")
        controller, router, register, _, config, started, release, pending = await self.make_fixture(failure)
        task = asyncio.create_task(register(controller, config))
        try:
            await asyncio.wait_for(started.wait(), 1)
            release.set()
            with self.assertRaises(RuntimeError) as caught:
                await asyncio.wait_for(task, 1)
            self.assertIs(caught.exception, failure)
            self.assertEqual(controller.registered_models, {})
            self.assertEqual(controller.request_routers, {})
            self.assertFalse(router.running)
        finally:
            release.set()
            await asyncio.gather(task, *pending, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
