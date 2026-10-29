import json
from pathlib import Path
import tempfile
import unittest

import torch
import checkpoint_contract as binding
import cpu_tensor_io

ROOT = Path(__file__).resolve().parent
CONFIG = (ROOT.parents[1] / 'evidence/followup-kimi-k26/config.json').read_bytes()
CONTRACT = (ROOT / 'checkpoint-targets.json').read_bytes()


class CheckpointContractTests(unittest.TestCase):
    def test_complete_sealed_name_coverage(self):
        targets = binding.load_targets(CONTRACT)
        document = json.loads(CONTRACT)
        self.assertEqual(len(targets), 1428)
        self.assertEqual(sum(document['target_source_counts'].values()), 208550)
        self.assertEqual(document['source_categories']['expert'], 207360)
        self.assertFalse(document['actual_tp_mapping_verified'])
        self.assertFalse(document['runtime_admitted'])

    def test_changed_contract_or_source_index_rejected(self):
        document = json.loads(CONTRACT)
        document['target_source_counts'].pop('language_model.model.norm.weight')
        with self.assertRaises(ValueError):
            binding.load_targets(binding.encode(document))
        with self.assertRaises(ValueError):
            binding.build_contract(b'{}', CONFIG)
        with self.assertRaises(ValueError):
            binding.build_contract(b'{}', CONFIG + b' ')

    def test_missing_frozen_target_refuses_before_file_creation(self):
        model = torch.nn.Module()
        model.language_model = torch.nn.Module()
        model.language_model.model = torch.nn.Module()
        model.language_model.model.norm = torch.nn.Module()
        model.language_model.model.norm.weight = torch.nn.Parameter(torch.ones(2))
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'rank'
            with self.assertRaisesRegex(ValueError, 'incomplete checkpoint-backed state'):
                cpu_tensor_io.write_cpu_state(target, model, checkpoint_contract=CONTRACT)
            self.assertFalse(target.exists())

    def test_component_only_call_does_not_claim_checkpoint_coverage(self):
        model = torch.nn.Module()
        model.weight = torch.nn.Parameter(torch.ones(2))
        with tempfile.TemporaryDirectory() as temp:
            _, _, info = cpu_tensor_io.write_cpu_state(Path(temp) / 'rank', model)
            self.assertFalse(info['checkpoint_name_coverage_verified'])
            self.assertFalse(info['inventory_policy_verified'])
            self.assertFalse(info['runtime_admitted'])


if __name__ == '__main__':
    unittest.main()
