"""CPU checks run in the real target image; these are not model acceptance."""
import types
import unittest
from unittest import mock

import torch
from sllm_store.vllm_kimi20_loader import (
    ServerlessLLMLoader, finish_restored_weights,
)
from vllm.model_executor.layers.quantization.compressed_tensors.compressed_tensors_moe.compressed_tensors_moe_wna16_marlin import CompressedTensorsWNA16MarlinMoEMethod


class KimiStateTest(unittest.TestCase):
    def test_preserves_historical_empty_and_alias_selection(self):
        weight = torch.ones(2)
        empty = torch.empty(384, 0, dtype=torch.int32)
        filtered = ServerlessLLMLoader._filter_subtensors(
            {"a": weight, "z": weight, "empty": empty})
        self.assertEqual(set(filtered), {"z", "empty"})
        self.assertIs(filtered["empty"], empty)

    def test_workspace_only_no_second_repack(self):
        model = torch.nn.Module()
        model.experts = torch.nn.Module()
        method = CompressedTensorsWNA16MarlinMoEMethod.__new__(
            CompressedTensorsWNA16MarlinMoEMethod)
        method.kernel_backend = "Marlin"
        method.marlin_input_dtype = None
        method.process_weights_after_loading = mock.Mock(
            side_effect=AssertionError("must not repack postprocessed weights"))
        model.experts.quant_method = method
        workspace = torch.zeros(4, dtype=torch.int32)
        with mock.patch(
            "vllm.model_executor.layers.quantization.utils.marlin_utils.marlin_make_workspace_new",
            return_value=workspace,
        ) as allocate:
            finish_restored_weights(model, types.SimpleNamespace(dtype=torch.bfloat16), torch.device("cpu"))
        allocate.assert_called_once_with(torch.device("cpu"), 4)
        self.assertIs(model.experts.workspace, workspace)
        method.process_weights_after_loading.assert_not_called()

    def test_unexpected_quant_method_fails(self):
        model = torch.nn.Module()
        model.quant_method = types.SimpleNamespace()
        with self.assertRaises(ValueError):
            finish_restored_weights(model, None, torch.device("cpu"))

    def test_standalone_reload_is_not_implicitly_enabled(self):
        loader = object.__new__(ServerlessLLMLoader)
        with self.assertRaises(NotImplementedError):
            loader.load_weights(None, None)


if __name__ == "__main__":
    unittest.main()
