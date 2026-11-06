"""V4 registration binding contracts from actual Controller/StoreManager source."""
import ast
import asyncio
import copy
from pathlib import Path
import types
import unittest
from unittest import mock
from typing import List, Optional

import test_v4_service as service_fixture

ROOT = Path(__file__).resolve().parents[3]


def controller_class():
    source = ROOT / "sllm/controller.py"
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "SllmController")
    cls.body = [n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and n.name in ("register", "delete")]
    names = dict(asyncio=asyncio, logger=mock.Mock(), List=List, Optional=Optional,
                 ray=types.SimpleNamespace(kill=mock.Mock()))
    exec(compile(ast.Module(body=[cls], type_ignores=[]), str(source), "exec"), names)
    return names["SllmController"]


class RegistrationBindingContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = service_fixture.RegistrationTests()
        await self.case.asyncSetUp()
        self.manager = self.case.ns["StoreManager"]()
        self.manager.local_servers = {"node-2": self.case.local}
        self.case.ns["get_worker_nodes"] = lambda: {"node-2": dict(address="unused")}
        self.config = dict(model="v4", backend="vllm", num_gpus=8,
            backend_config=dict(self.case.config, tokenizer_mode="deepseek_v4", torch_dtype="bfloat16",
                                pretrained_model_name_or_path="/frozen/DeepSeek-V4-Pro", max_model_len=2048),
            placement_config=dict(local_disk=["node-2"], memory_pool=[]),
            auto_scaling_config=dict(min_instances=0, max_instances=1), router_config={})

    def controller(self, registered=True):
        cls = controller_class()
        controller = cls.__new__(cls)
        controller.running = True
        controller.metadata_lock = asyncio.Lock()
        controller.registered_models = {"v4": copy.deepcopy(self.config)} if registered else {}
        router = types.SimpleNamespace(update=types.SimpleNamespace(remote=mock.AsyncMock()),
                                       start=types.SimpleNamespace(remote=mock.Mock()),
                                       shutdown=types.SimpleNamespace(remote=mock.AsyncMock(return_value=[])))
        controller.request_routers = {"v4": router} if registered else {}
        controller.store_manager = types.SimpleNamespace(register=types.SimpleNamespace(remote=mock.AsyncMock()))
        controller.router_cls = types.SimpleNamespace(options=mock.Mock())
        controller.router_cls.options.return_value.remote.return_value = router
        return controller, router

    async def test_storemanager_changed_store_identity_is_rejected_before_lower_cache(self):
        await self.manager.register(self.config)
        changed = copy.deepcopy(self.config)
        changed["backend_config"]["model_loader_extra_config"]["identity"]["conversion_id"] = "different"
        with self.assertRaises(ValueError):
            await self.manager.register(changed)
        self.assertEqual(self.case.client.register_model.call_count, 496)

    async def test_controller_changed_active_context_is_rejected_before_return(self):
        controller, router = self.controller()
        changed = copy.deepcopy(self.config)
        changed["backend_config"]["max_model_len"] = 4096
        with self.assertRaises(ValueError):
            await controller.register(changed)
        router.update.remote.assert_not_awaited()

    async def test_same_config_and_two_loader_modes_reuse_without_rehash_or_conversion(self):
        self.manager.download_vllm_model = mock.AsyncMock(side_effect=AssertionError("legacy converter"))
        await self.manager.register(self.config)
        for config in (copy.deepcopy(self.config), self.native_config(), copy.deepcopy(self.config)):
            await self.manager.register(config)
        self.assertEqual(self.case.client.register_model.call_count, 496)
        self.case.inspect.options.return_value.remote.assert_awaited_once()
        self.manager.download_vllm_model.assert_not_awaited()

    def native_config(self):
        config = copy.deepcopy(self.config)
        config["backend_config"]["load_format"] = "safetensors"
        config["backend_config"].pop("model_loader_extra_config")
        return config

    async def test_controller_and_storemanager_connect_loader_switch_after_delete(self):
        await self.manager.register(self.config)
        controller, router = self.controller(registered=False)
        controller.store_manager.register.remote.side_effect = self.manager.register
        for config in (self.config, self.native_config(), self.config):
            await controller.register(config)
            self.assertEqual(controller.registered_models["v4"], config)
            await controller.delete("v4")
        self.assertEqual(router.shutdown.remote.await_count, 3)
        self.assertEqual(self.case.client.register_model.call_count, 496)
        self.case.inspect.options.return_value.remote.assert_awaited_once()

    async def test_native_service_requires_prepared_store_and_never_calls_legacy_converter(self):
        self.manager.download_vllm_model = mock.AsyncMock(side_effect=AssertionError("legacy converter"))
        with self.assertRaisesRegex(ValueError, "prepared"):
            await self.manager.register(self.native_config())
        self.manager.download_vllm_model.assert_not_awaited()
        self.case.client.register_model.assert_not_called()
        self.assertFalse(self.manager.model_info)

    async def test_store_path_precision_parallel_context_placement_and_numeric_type_are_frozen(self):
        await self.manager.register(self.config)
        for change in (
            lambda c: c["backend_config"]["model_loader_extra_config"].update(store_path="/another/store"),
            lambda c: c["backend_config"].update(torch_dtype="float16"),
            lambda c: c["backend_config"].update(tensor_parallel_size=4),
            lambda c: c["backend_config"].update(max_model_len=4096),
            lambda c: c["placement_config"].update(local_disk=["elsewhere"]),
            lambda c: c.update(num_gpus=8.0),
            lambda c: c["backend_config"].update(load_format="sllm_v4_grouped_export"),
        ):
            changed = copy.deepcopy(self.config)
            change(changed)
            with self.assertRaises(ValueError):
                await self.manager.register(changed)
        self.assertEqual(self.case.client.register_model.call_count, 496)

    async def test_native_mode_cannot_hide_common_profile_change_or_grouped_metadata(self):
        await self.manager.register(self.config)
        for key, value in (("max_model_len", 4096), ("pretrained_model_name_or_path", "/wrong/checkpoint")):
            changed = self.native_config()
            changed["backend_config"][key] = value
            with self.assertRaises(ValueError):
                await self.manager.register(changed)
        changed = self.native_config()
        changed["backend_config"]["model_loader_extra_config"] = self.config["backend_config"]["model_loader_extra_config"]
        with self.assertRaisesRegex(ValueError, "metadata"):
            await self.manager.register(changed)

    async def test_failed_store_preparation_does_not_admit_native_mode(self):
        self.case.inspect.options.return_value.remote.side_effect = ValueError("incomplete Store")
        with self.assertRaisesRegex(ValueError, "incomplete Store"):
            await self.manager.register(self.config)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            await self.manager.register(self.native_config())
        changed = copy.deepcopy(self.config)
        changed["backend_config"]["model_loader_extra_config"]["store_path"] = "/fixed/store"
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.manager.register(changed)
        self.assertFalse(self.manager.model_info)

    async def test_json_key_order_does_not_break_idempotence_and_nan_is_rejected(self):
        await self.manager.register(self.config)
        reordered = dict(reversed(list(self.config.items())))
        await self.manager.register(reordered)
        changed = copy.deepcopy(self.config)
        changed["backend_config"]["gpu_memory_utilization"] = float("nan")
        with self.assertRaises(ValueError):
            await self.manager.register(changed)
        self.assertEqual(self.case.client.register_model.call_count, 496)

    async def test_caller_mutation_during_store_admission_cannot_change_binding(self):
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def inspect(config):
            entered.set()
            await proceed.wait()
            return self.case.plan
        self.case.inspect.options.return_value.remote.side_effect = inspect
        original = copy.deepcopy(self.config)
        task = asyncio.create_task(self.manager.register(self.config))
        try:
            await entered.wait()
            self.config["backend_config"]["model_loader_extra_config"]["store_path"] = "/mutated/store"
            proceed.set()
            await task
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.case.local.grouped_models["v4"], original["backend_config"]["model_loader_extra_config"])
        await self.manager.register(original)
        with self.assertRaisesRegex(ValueError, "changed"):
            await self.manager.register(self.config)

    async def test_controller_copies_config_before_await_and_allows_new_route_loader_mode(self):
        controller, router = self.controller(registered=False)
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def store_register(config):
            entered.set()
            await proceed.wait()
        controller.store_manager.register.remote.side_effect = store_register
        original = copy.deepcopy(self.config)
        task = asyncio.create_task(controller.register(self.config))
        try:
            await entered.wait()
            self.config["backend_config"]["max_model_len"] = 4096
            proceed.set()
            await task
        finally:
            proceed.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(controller.registered_models["v4"], original)
        await controller.register(original)
        await controller.delete("v4")
        self.assertFalse(controller.registered_models)
        self.assertFalse(controller.request_routers)
        self.config = original
        native = self.native_config()
        await controller.register(native)
        self.assertEqual(controller.registered_models["v4"], native)

    async def test_active_controller_rejects_downgrade_but_same_config_is_idempotent(self):
        controller, router = self.controller()
        await controller.register(copy.deepcopy(self.config))
        router.update.remote.assert_awaited_once()
        changed = self.native_config()
        changed["backend_config"]["tokenizer_mode"] = "auto"
        with self.assertRaisesRegex(ValueError, "Active V4"):
            await controller.register(changed)
        controller.store_manager.register.remote.assert_not_awaited()

    async def test_v4_registration_on_stopped_controller_fails_explicitly(self):
        controller, router = self.controller()
        controller.running = False
        with self.assertRaisesRegex(RuntimeError, "not running"):
            await controller.register(self.config)
        controller.store_manager.register.remote.assert_not_awaited()

    async def test_unknown_placement_nodes_fail_instead_of_returning_success(self):
        changed = copy.deepcopy(self.config)
        changed["placement_config"]["local_disk"] = ["missing"]
        with self.assertRaises(ValueError):
            await self.manager.register(changed)
        self.assertNotIn("v4", self.manager.model_info)
        self.case.client.register_model.assert_not_called()

    async def test_unknown_memory_pool_worker_fails_before_store_registration(self):
        changed = copy.deepcopy(self.config)
        changed["placement_config"]["memory_pool"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "memory_pool"):
            await self.manager.register(changed)
        self.case.client.register_model.assert_not_called()
        self.assertNotIn("v4", self.manager.model_info)

    async def test_stopped_legacy_controller_keeps_existing_return_behavior(self):
        controller, _ = self.controller(registered=False)
        controller.running = False
        self.assertIsNone(await controller.register(dict(model="legacy", backend="vllm")))

    async def test_legacy_registration_keeps_original_converter_and_reuse_behavior(self):
        self.manager.download_vllm_model = mock.AsyncMock()
        self.case.client.register_model.side_effect = [11, 12]
        legacy = dict(model="old", backend="vllm", backend_config=dict(tensor_parallel_size=2))
        await self.manager.register(legacy)
        changed = copy.deepcopy(legacy)
        changed["backend_config"]["tensor_parallel_size"] = 4
        await self.manager.register(changed)
        self.assertEqual(self.manager.model_info["old"], 23)
        self.manager.download_vllm_model.assert_awaited_once()
        self.case.inspect.options.return_value.remote.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
