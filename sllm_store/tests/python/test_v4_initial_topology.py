"""First V4 admission must agree with the fixed TP8 rank/resource contract."""
import copy
import unittest

import test_v4_registration as registration


class InitialTopologyContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = registration.RegistrationBindingContracts()
        await self.case.asyncSetUp()

    async def test_controller_rejects_one_gpu_for_tp8_before_store_or_router(self):
        controller, _ = self.case.controller(registered=False)
        changed = copy.deepcopy(self.case.config)
        changed["num_gpus"] = 1
        with self.assertRaises(ValueError):
            await controller.register(changed)
        controller.store_manager.register.remote.assert_not_awaited()
        controller.router_cls.options.assert_not_called()

    async def test_manager_rejects_implicit_zero_gpu_request_before_worker(self):
        changed = copy.deepcopy(self.case.config)
        changed.pop("num_gpus")
        with self.assertRaises(ValueError):
            await self.case.manager.register(changed)
        self.case.case.inspect.options.return_value.remote.assert_not_awaited()
        self.case.case.client.register_model.assert_not_called()


    async def test_invalid_gpu_counts_and_types_fail_before_controller_side_effects(self):
        for value in (0, 1, 4, 16, True, 8.0, "8", None):
            with self.subTest(num_gpus=value):
                controller, _ = self.case.controller(registered=False)
                changed = copy.deepcopy(self.case.config)
                changed["num_gpus"] = value
                with self.assertRaisesRegex(ValueError, "num_gpus"):
                    await controller.register(changed)
                controller.store_manager.register.remote.assert_not_awaited()
                controller.router_cls.options.assert_not_called()

    async def test_unsupported_rank_layouts_fail_before_binding_or_worker_io(self):
        for key, value in (("tensor_parallel_size", 4), ("tensor_parallel_size", 8.0),
                           ("tensor_parallel_size", True), ("pipeline_parallel_size", 2),
                           ("pipeline_parallel_size", True), ("data_parallel_size", 2),
                           ("data_parallel_size", "1"), ("enable_expert_parallel", True),
                           ("enable_expert_parallel", 0)):
            with self.subTest(field=key, value=value):
                changed = copy.deepcopy(self.case.config)
                changed["backend_config"][key] = value
                with self.assertRaises(ValueError):
                    await self.case.manager.register(changed)
                self.assertFalse(self.case.manager.v4_registrations)
                self.assertFalse(self.case.manager.model_info)
                self.case.case.inspect.options.return_value.remote.assert_not_awaited()
                self.case.case.client.register_model.assert_not_called()

    async def test_missing_tp_or_v4_tokenizer_cannot_fall_back_to_wrong_prompt_path(self):
        for key in ("tensor_parallel_size", "tokenizer_mode"):
            changed = copy.deepcopy(self.case.config)
            changed["backend_config"].pop(key)
            with self.assertRaises(ValueError):
                await self.case.manager.register(changed)
        changed = copy.deepcopy(self.case.config)
        changed["backend_config"]["tokenizer_mode"] = "auto"
        with self.assertRaisesRegex(ValueError, "tokenizer_mode"):
            await self.case.manager.register(changed)
        self.case.case.client.register_model.assert_not_called()

    async def test_valid_default_pp_dp_ep_keeps_eight_gpu_router_request(self):
        controller, _ = self.case.controller(registered=False)
        await controller.register(copy.deepcopy(self.case.config))
        arguments = controller.router_cls.options.return_value.remote.call_args.args
        self.assertEqual(arguments[1]["num_gpus"], 8)
        self.assertEqual(arguments[3]["tensor_parallel_size"], 8)
        controller.store_manager.register.remote.assert_awaited_once()

    async def test_explicit_supported_coordinates_preserve_two_loader_reuse(self):
        config = copy.deepcopy(self.case.config)
        config["backend_config"].update(pipeline_parallel_size=1, data_parallel_size=1,
                                        enable_expert_parallel=False)
        await self.case.manager.register(config)
        native = copy.deepcopy(config)
        native["backend_config"]["load_format"] = "safetensors"
        native["backend_config"].pop("model_loader_extra_config")
        await self.case.manager.register(native)
        await self.case.manager.register(config)
        self.assertEqual(self.case.case.client.register_model.call_count, 496)
        self.case.case.inspect.options.return_value.remote.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
