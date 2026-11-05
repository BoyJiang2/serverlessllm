"""Actual native method AST, real asyncio, FakeRay/engine; no Torch/Ray imports."""
import ast
import asyncio
import builtins
from dataclasses import dataclass, fields
from enum import IntEnum
import gc
import logging
import os
from pathlib import Path
import sys
import time
import types
import typing
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[2]


def actual_class(relative, name, methods, namespace):
    tree = ast.parse((ROOT / relative).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    if methods is not None:
        cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name in methods]
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])),
                 str(ROOT / relative), "exec"), namespace)
    return namespace[name]


@dataclass
class EngineArgs:
    model: str = ""
    dtype: str = "auto"
    load_format: str = "auto"
    tensor_parallel_size: int = 1
    distributed_executor_backend: str = "mp"
    trust_remote_code: bool = False
    mm_encoder_tp_mode: str = "data"
    enforce_eager: bool = False
    enable_prefix_caching: bool = True


class Status(IntEnum):
    UNINITIALIZED = 0
    RUNNING = 1
    DELETING = 3
    STOPPING = 2


def backend_class():
    ns = {k: getattr(typing, k) for k in ("Any", "Dict", "List", "Optional", "Union", "Sequence")}
    ns.update(asyncio=asyncio, gc=gc, os=os, time=time, uuid=uuid, fields=fields,
              logger=logging.getLogger("native-fixture"), AsyncEngineArgs=EngineArgs,
              BackendStatus=Status, SllmBackend=object, RequestOutput=types.SimpleNamespace,
              TokensPrompt=dict, torch=types.SimpleNamespace(
                  cuda=types.SimpleNamespace(is_available=lambda: False)))
    actual_class("sllm/backends/vllm_backend.py", "LLMEngineStatusDict", None, ns)
    tree = ast.parse((ROOT / "sllm/backends/vllm_backend.py").read_text())
    output = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "process_output")
    exec(compile(ast.Module(body=[output], type_ignores=[]), "actual-native-output", "exec"), ns)
    cls = actual_class("sllm/backends/vllm_backend.py", "VllmBackend",
                       {"__init__", "generate", "shutdown", "get_current_tokens", "stop"}, ns)
    return cls, ns


class NativeReadyLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_actual_counter_path_and_legacy_task_negative(self):
        source = (ROOT / "sllm/backends/vllm_backend.py").read_text()
        imports = [n for n in ast.parse(source).body if isinstance(n, ast.ImportFrom)]
        self.assertTrue(any(n.module == "vllm.utils.counter" and n.names[0].name == "Counter" for n in imports))
        cls, ns = backend_class()
        config = dict(torch_dtype="bfloat16", tensor_parallel_size=8,
                      trust_remote_code=True, mm_encoder_tp_mode="data")
        instance = cls("kimi", config)
        self.assertEqual(instance.engine_args.dtype, "bfloat16")
        self.assertEqual(instance.engine_args.tensor_parallel_size, 8)
        self.assertFalse(hasattr(instance.engine_args, "task"))
        # Restore the exact old forced kwarg to reproduce the old constructor error.
        tree = ast.parse(source)
        method = next(n for c in tree.body if isinstance(c, ast.ClassDef) and c.name == "VllmBackend"
                      for n in c.body if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        position = next(i for i, n in enumerate(method.body)
                        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "self.engine_args")
        method.body.insert(position, ast.parse('filtered_engine_config["task"] = self.task').body[0])
        exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
                     "actual-legacy-constructor", "exec"), ns)
        with self.assertRaisesRegex(TypeError, "task"):
            ns["__init__"](cls.__new__(cls), "kimi", config)

    async def test_optional_peft_export_is_lazy_and_missing_stays_visible(self):
        source = (ROOT / "sllm/backends/__init__.py").read_text()
        package = types.ModuleType("native_fixture")
        package.__path__ = []
        package.__package__ = "native_fixture"
        dummy, vllm = object(), object()
        modules = {"native_fixture": package,
                   "native_fixture.dummy_backend": types.SimpleNamespace(DummyBackend=dummy),
                   "native_fixture.vllm_backend": types.SimpleNamespace(VllmBackend=vllm)}
        original = builtins.__import__
        attempted = []
        def without_optional(name, *args, **kwargs):
            if name.endswith("transformers_backend") or name == "peft":
                attempted.append(name)
                raise ModuleNotFoundError("PEFT unavailable", name="peft")
            return original(name, *args, **kwargs)
        with mock.patch.dict(sys.modules, modules), mock.patch("builtins.__import__", without_optional):
            # Old eager package import fails before callers can select vLLM.
            with self.assertRaises(ModuleNotFoundError):
                exec("from .transformers_backend import TransformersBackend", package.__dict__)
            attempted.clear()
            exec(compile(source, "actual-backends-package", "exec"), package.__dict__)
            self.assertIs(package.VllmBackend, vllm)
            self.assertIs(package.DummyBackend, dummy)
            self.assertFalse(attempted)
            with self.assertRaises(ModuleNotFoundError):
                getattr(package, "TransformersBackend")
            self.assertTrue(attempted)
            with self.assertRaises(AttributeError):
                getattr(package, "UnknownBackend")

    async def test_backend_shutdown_explicit_and_error_keeps_engine(self):
        cls, _ = backend_class()
        for failing in (False, True):
            with self.subTest(failing=failing):
                instance = cls("kimi", {})
                instance.status = Status.RUNNING
                events = []
                error = RuntimeError("engine shutdown failure")
                async def abort(request): events.append(("abort", request))
                def shutdown():
                    events.append("shutdown")
                    if failing: raise error
                engine = types.SimpleNamespace(abort=abort, shutdown=shutdown)
                instance.engine = engine
                instance.request_trace.return_all_request_ids = mock.AsyncMock(return_value=["request"])
                if failing:
                    with self.assertRaises(RuntimeError) as raised:
                        await instance.shutdown()
                    self.assertIs(raised.exception, error)
                    self.assertIs(instance.engine, engine)
                    with self.assertRaisesRegex(RuntimeError, "not completed"):
                        await instance.shutdown()
                else:
                    await instance.shutdown()
                    self.assertIsNone(instance.engine)
                    await instance.shutdown()
                self.assertEqual(events, [("abort", "request"), "shutdown"])

    def router(self, failure=None):
        events = []
        ns = dict(asyncio=asyncio, Dict=typing.Dict, List=typing.List, Optional=typing.Optional,
                  SllmRouter=object, logger=logging.getLogger("router-fixture"))
        def kill(handle):
            self.assertIs(router.ready_inference_instances["ready"], instance)
            events.append("kill")
            if failure == "kill": raise error
        ns["ray"] = types.SimpleNamespace(kill=kill)
        cls = actual_class("sllm/routers/roundrobin_router.py", "RoundRobinRouter",
                           {"shutdown", "_shutdown_instance"}, ns)
        router = cls.__new__(cls)
        router.running = True
        router.running_lock = asyncio.Lock()
        router.instance_management_lock = asyncio.Lock()
        router.starting_inference_instances = {}
        router.deleting_inference_instances = {}
        router.ready_ft_instances = {}
        router.request_count = 0
        router.request_queue = asyncio.Queue()
        router.model_name = "kimi"
        router.resource_requirements = {"num_gpus": 8}
        error = RuntimeError("retained " + str(failure))
        async def backend_shutdown():
            self.assertIs(router.ready_inference_instances["ready"], instance)
            events.append("backend")
            if failure == "backend": raise error
        async def deallocate(*args):
            self.assertIs(router.ready_inference_instances["ready"], instance)
            events.append("deallocate")
            if failure == "deallocate": raise error
        instance = types.SimpleNamespace(lock=asyncio.Lock(), status=True,
            backend_instance=types.SimpleNamespace(shutdown=types.SimpleNamespace(remote=backend_shutdown)))
        router.ready_inference_instances = {"ready": instance}
        router.model_loading_scheduler = types.SimpleNamespace(
            deallocate_resource=types.SimpleNamespace(remote=deallocate))
        async def auto_scaler():
            while True:
                if not router.ready_inference_instances: events.append("recreate")
                await asyncio.sleep(0)
        async def load_balancer():
            await asyncio.Future()
        router.auto_scaler = asyncio.create_task(auto_scaler())
        router.load_balancer = asyncio.create_task(load_balancer())
        return router, instance, events, error

    async def test_ready_delete_cancels_tasks_before_removal_no_recreation(self):
        router, instance, events, _ = self.router()
        await asyncio.sleep(0)
        result = await router.shutdown()
        for _ in range(3): await asyncio.sleep(0)
        self.assertEqual(result, ["ready"])
        self.assertEqual(events, ["backend", "kill", "deallocate"])
        self.assertFalse(router.ready_inference_instances)
        self.assertTrue(router.auto_scaler.cancelled() and router.load_balancer.cancelled())
        self.assertFalse(router.running)

    async def test_ready_delete_failure_retains_owner_at_all_three_boundaries(self):
        for failure in ("backend", "kill", "deallocate"):
            with self.subTest(failure=failure):
                router, instance, events, error = self.router(failure)
                with self.assertRaises(RuntimeError) as raised:
                    await router.shutdown()
                self.assertIs(raised.exception, error)
                self.assertIs(router.ready_inference_instances["ready"], instance)
                self.assertTrue(router.auto_scaler.done() and router.load_balancer.done())
                self.assertNotIn("recreate", events)

    async def test_nonquiescent_cases_fail_without_discarding_ready_owner(self):
        for busy in ("starting", "deleting", "request", "queue"):
            with self.subTest(busy=busy):
                router, instance, events, _ = self.router()
                if busy == "starting": router.starting_inference_instances["pending"] = object()
                if busy == "deleting": router.deleting_inference_instances["pending"] = object()
                if busy == "request": router.request_count = 1
                if busy == "queue": await router.request_queue.put(asyncio.get_running_loop().create_future())
                with self.assertRaisesRegex(RuntimeError, "quiescent"):
                    await router.shutdown()
                self.assertIs(router.ready_inference_instances["ready"], instance)
                self.assertFalse(events)
                self.assertTrue(router.auto_scaler.done() and router.load_balancer.done())

    def controller(self):
        events, names = [], {}
        def kill(handle):
            events.append("kill")
            names.pop(handle.name, None)
        ns = dict(asyncio=asyncio, List=typing.List, Optional=typing.Optional,
                  logger=logging.getLogger("controller-fixture"), ray=types.SimpleNamespace(kill=kill))
        cls = actual_class("sllm/controller.py", "SllmController",
                           {"register", "delete", "shutdown"}, ns)
        controller = cls.__new__(cls)
        controller.running = True
        controller.running_lock = asyncio.Lock()
        controller.metadata_lock = asyncio.Lock()
        controller.request_routers = {}
        controller.registered_models = {}
        controller.store_manager = types.SimpleNamespace(register=types.SimpleNamespace(remote=mock.AsyncMock()))
        class Factory:
            def options(self, **kwargs):
                self.options_data = kwargs
                return self
            def remote(factory, name, resources, *args):
                self.assertNotIn(name, names)
                self.assertEqual(resources["num_gpus"], 8)
                self.assertEqual(factory.options_data["name"], name)
                async def shutdown():
                    self.assertIs(controller.request_routers[name], handle)
                    self.assertIn(name, controller.registered_models)
                    events.append("shutdown")
                    return ["ready"]
                handle = types.SimpleNamespace(name=name, shutdown=types.SimpleNamespace(remote=shutdown),
                    start=types.SimpleNamespace(remote=lambda config: None))
                names[name] = handle
                return handle
        controller.router_cls = Factory()
        return controller, events, names

    async def test_controller_awaits_shutdown_before_pop_and_second_register(self):
        controller, events, names = self.controller()
        config = dict(model="kimi", backend="vllm", num_gpus=8, backend_config={},
                      auto_scaling_config=dict(min_instances=1, max_instances=1))
        await controller.register(config)
        first = controller.request_routers["kimi"]
        await controller.delete("kimi")
        self.assertEqual(events, ["shutdown", "kill"])
        self.assertNotIn("kimi", controller.request_routers)
        self.assertNotIn("kimi", names)
        await controller.register(config)
        self.assertIsNot(controller.request_routers["kimi"], first)

    async def test_controller_failure_retains_registry_and_shutdown_reuses_delete(self):
        controller, events, _ = self.controller()
        error = RuntimeError("router shutdown failed")
        handle = types.SimpleNamespace(name="kimi", shutdown=types.SimpleNamespace(
            remote=mock.AsyncMock(side_effect=error)))
        controller.request_routers["kimi"] = handle
        controller.registered_models["kimi"] = {}
        with self.assertRaises(RuntimeError) as raised:
            await controller.delete("kimi")
        self.assertIs(raised.exception, error)
        self.assertIs(controller.request_routers["kimi"], handle)
        self.assertIn("kimi", controller.registered_models)
        self.assertFalse(events)
        controller.request_routers = {"one": object(), "two": object()}
        visited = []
        async def delete(name):
            async with controller.metadata_lock:
                visited.append(name)
                controller.request_routers.pop(name)
        controller.delete = delete
        await asyncio.wait_for(controller.shutdown(), timeout=1)
        self.assertCountEqual(visited, ["one", "two"])
        self.assertFalse(controller.request_routers)

    async def test_existing_tokens_trace_no_public_output_change_and_stop_limit(self):
        cls, ns = backend_class()
        def sampling(**kwargs):
            self.assertTrue(set(kwargs) <= {"temperature", "seed", "top_p", "n", "max_tokens"})
            return kwargs
        ns["SamplingParams"] = sampling
        instance = cls("kimi", dict(trace_debug=True))
        instance.status = Status.RUNNING
        async def generate(inputs, params, request_id):
            self.assertEqual(params["max_tokens"], 32)
            yield types.SimpleNamespace(request_id=request_id, metrics=None,
                prompt_token_ids=inputs["prompt_token_ids"], outputs=[types.SimpleNamespace(
                    text="4", logprobs=None, finish_reason="stop", token_ids=[42])])
        instance.engine = types.SimpleNamespace(generate=generate, abort=mock.AsyncMock(), shutdown=mock.Mock())
        for request_id, tokens in (("A1", [10, 20]), ("A2", [10, 20]), ("B", [30, 40])):
            result = await instance.generate(dict(model="kimi", input_tokens=tokens, request_id=request_id,
                temperature=0, seed=0, top_p=1, n=1, max_tokens=32))
            self.assertNotIn("token_ids", result["choices"][0])
            self.assertEqual(result["usage"]["prompt_tokens"], 2)
        self.assertEqual(await instance.get_current_tokens(), [[10, 20, 42], [10, 20, 42], [30, 40, 42]])
        self.assertEqual(await instance.request_trace.request_count(), 3)
        # Existing trace_debug leaves finished entries; do not claim general stop works.
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(instance.stop(), timeout=0.01)
        await instance.shutdown()


if __name__ == "__main__":
    unittest.main()
