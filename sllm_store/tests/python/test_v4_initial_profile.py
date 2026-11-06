"""First service admission must match the frozen V4 experimental profile."""
import copy
import unittest

import test_v4_registration as registration
from sllm_store import v4_service as service


class InitialProfileContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = registration.RegistrationBindingContracts()
        await self.case.asyncSetUp()

    async def test_missing_frozen_fields_fail_before_controller_side_effects(self):
        for key in ("torch_dtype", "enforce_eager", "enable_prefix_caching", "max_model_len",
                    "max_num_seqs", "max_num_batched_tokens", "pretrained_model_name_or_path"):
            with self.subTest(field=key):
                controller, _ = self.case.controller(registered=False)
                config = copy.deepcopy(self.case.config)
                del config["backend_config"][key]
                with self.assertRaisesRegex(ValueError, key):
                    await controller.register(config)
                controller.store_manager.register.remote.assert_not_awaited()
                controller.router_cls.options.assert_not_called()

    async def test_changed_profile_fails_before_store_binding_or_worker(self):
        for key, value in (("torch_dtype", "float16"), ("enforce_eager", False),
                           ("enforce_eager", 1), ("enable_prefix_caching", True),
                           ("enable_prefix_caching", 0), ("max_model_len", 4096),
                           ("max_num_seqs", True), ("max_num_seqs", 2),
                           ("max_num_batched_tokens", 2048.0),
                           ("pretrained_model_name_or_path", "deepseek-ai/DeepSeek-V4-Pro")):
            with self.subTest(field=key, value=value):
                config = copy.deepcopy(self.case.config)
                config["backend_config"][key] = value
                with self.assertRaisesRegex(ValueError, key):
                    await self.case.manager.register(config)
                self.assertFalse(self.case.manager.v4_registrations)
                self.assertFalse(self.case.manager.model_info)
                self.case.case.inspect.options.return_value.remote.assert_not_awaited()
                self.case.case.client.register_model.assert_not_called()

    def test_unreviewed_overrides_and_features_are_rejected(self):
        for key, value in (("speculative_config", {"method": "mtp"}),
                           ("hf_overrides", {"num_hidden_layers": 1}),
                           ("quantization", "fp8"), ("dtype", "float16"),
                           ("model", "other"), ("tokenizer", "other"),
                           ("enable_lora", True), ("lora_adapters", {"a": "other"}),
                           ("max_num_seq", 1)):
            with self.subTest(field=key):
                config = copy.deepcopy(self.case.config)
                config["backend_config"][key] = value
                with self.assertRaisesRegex(ValueError, key):
                    service.freeze_registration_config(config)

    async def test_two_frozen_loading_modes_remain_accepted(self):
        await self.case.manager.register(copy.deepcopy(self.case.config))
        await self.case.manager.register(self.case.native_config())
        self.assertEqual(self.case.case.client.register_model.call_count, 496)
        self.case.case.inspect.options.return_value.remote.assert_awaited_once()

    async def test_initial_scaling_must_not_prestart_or_create_multiple_tp8_instances(self):
        for scaling in (None, {}, {"min_instances": 1, "max_instances": 1},
                        {"min_instances": 0, "max_instances": 10},
                        {"min_instances": False, "max_instances": 1},
                        {"min_instances": 0, "max_instances": 1.0}):
            with self.subTest(scaling=scaling):
                controller, _ = self.case.controller(registered=False)
                config = copy.deepcopy(self.case.config)
                if scaling is None:
                    config.pop("auto_scaling_config")
                else:
                    config["auto_scaling_config"] = scaling
                with self.assertRaisesRegex(ValueError, "auto_scaling_config"):
                    await controller.register(config)
                controller.store_manager.register.remote.assert_not_awaited()
                controller.router_cls.options.assert_not_called()

    def test_legacy_configuration_is_not_restricted_by_v4_profile(self):
        config = {"model": "legacy", "backend": "vllm", "backend_config": {"enforce_eager": False}}
        self.assertIs(service.freeze_registration_config(config), config)


if __name__ == "__main__":
    unittest.main()
