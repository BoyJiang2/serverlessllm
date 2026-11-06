"""Frozen V4 routes cannot be mutated through the dynamic update entry point."""
import copy
import unittest

import test_v4_registration as registration


class FrozenUpdateContracts(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.case = registration.RegistrationBindingContracts()
        await self.case.asyncSetUp()

    async def test_v4_scaling_update_fails_before_metadata_and_router_changes(self):
        controller, router = self.case.controller()
        before = copy.deepcopy(controller.registered_models)
        with self.assertRaisesRegex(ValueError, "V4.*delete.*register"):
            await controller.update("v4", {"auto_scaling_config": {"min_instances": 2, "max_instances": 2}})
        self.assertEqual(controller.registered_models, before)
        router.update.remote.assert_not_awaited()

    async def test_v4_empty_or_same_update_does_not_silently_succeed(self):
        for request in ({}, {"auto_scaling_config": self.case.config["auto_scaling_config"]}):
            with self.subTest(request=request):
                controller, router = self.case.controller()
                with self.assertRaisesRegex(ValueError, "V4"):
                    await controller.update("v4", copy.deepcopy(request))
                self.assertEqual(controller.registered_models["v4"], self.case.config)
                router.update.remote.assert_not_awaited()

    async def test_legacy_scaling_update_still_reaches_router(self):
        controller, router = self.case.controller()
        controller.registered_models["v4"]["backend_config"]["tokenizer_mode"] = "auto"
        controller.registered_models["v4"]["backend_config"]["load_format"] = "safetensors"
        scaling = {"min_instances": 2, "max_instances": 2}
        await controller.update("v4", {"auto_scaling_config": scaling})
        self.assertEqual(controller.registered_models["v4"]["auto_scaling_config"], scaling)
        router.update.remote.assert_awaited_once_with(scaling)

    async def test_unknown_model_still_requires_registration(self):
        controller, _ = self.case.controller(registered=False)
        with self.assertRaisesRegex(ValueError, "not found"):
            await controller.update("missing", {"auto_scaling_config": {}})


if __name__ == "__main__":
    unittest.main()
