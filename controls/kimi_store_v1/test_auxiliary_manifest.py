import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import torch

from auxiliary_manifest import validate_auxiliary_receipts
import contract
from cpu_restore import restore_cpu_state
from cpu_tensor_io import tensor_layout, write_cpu_state
import native_index
from state_audit import compare_restored_state, fingerprint_state
import test_marlin_aux
import test_cpu_tensor_io


class AuxiliaryManifestTests(unittest.TestCase):
    def prepared_model(self):
        helper = test_marlin_aux.AuxiliaryTests()
        layer = helper.layer()
        model = torch.nn.Module()
        node = model
        for name in ['language_model', 'model', 'layers', '1', 'mlp']:
            child = torch.nn.Module()
            node.add_module(name, child)
            node = child
        node.add_module('experts', layer)
        return model, layer, helper.normalize(layer)

    def layouts(self, model):
        return {n: tensor_layout(t) for n, t in model.named_parameters(remove_duplicate=False)}

    def export(self, directory, model, receipt):
        records, layouts, info = write_cpu_state(directory, model, chunk_bytes=7,
                                                  auxiliary_receipts=[receipt], rank=0)
        inputs = {n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                  for n, r in layouts.items()}
        plan = contract.plan_rank(test_cpu_tensor_io.CONFIG, rank=0, tensor_parallel_size=8,
                                  revision=contract.REVISION, tensors=inputs,
                                  vllm_version='0.19.1', torch_version='2.10.0')
        native_index.publish_index(directory, plan, records, layouts, test_cpu_tensor_io.CONFIG,
                                   aliases=info['alias_of'], auxiliary_receipts=info['auxiliary_receipts'])
        return hashlib.sha256((directory / 'kimi_raw_state.json').read_bytes()).hexdigest()

    def test_normalized_state_exports_and_restores_with_both_receipts(self):
        source, _, receipt = self.prepared_model()
        destination, layer, destination_receipt = self.prepared_model()
        layer.packed.data.zero_()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            digest = self.export(directory, source, receipt)
            before = fingerprint_state(source, rank=0, phase='before_postprocess')
            result = restore_cpu_state(directory, destination, digest, rank=0,
                                       destination_auxiliary_receipts=[destination_receipt], chunk_bytes=7)
            after = fingerprint_state(destination, rank=0, phase='before_postprocess')
            self.assertTrue(compare_restored_state(before, after)['same_phase_cpu_bytes_equal'])
            self.assertEqual(result['auxiliary_receipts'], [receipt])
            self.assertFalse(result['runtime_admitted'])

    def test_missing_receipt_rejected_before_directory_or_destination_write(self):
        source, _, receipt = self.prepared_model()
        destination, layer, _ = self.prepared_model()
        layer.packed.data.zero_()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            with self.assertRaisesRegex(ValueError, 'rank|missing'):
                write_cpu_state(directory, source)
            self.assertFalse(directory.exists())
            digest = self.export(directory, source, receipt)
            before = layer.packed.detach().clone()
            with self.assertRaisesRegex(ValueError, 'missing'):
                restore_cpu_state(directory, destination, digest, rank=0)
            self.assertTrue(torch.equal(layer.packed, before))

    def test_modified_duplicate_or_wrong_rank_receipts_refused(self):
        model, _, receipt = self.prepared_model()
        layouts = self.layouts(model)
        for field, value in [('rank', 1), ('rank', False), ('kernel_backend', 'Flashinfer'),
                             ('method_source_sha256', 'bad'), ('scratch_payload_read', True)]:
            changed = copy.deepcopy(receipt)
            changed[field] = value
            with self.assertRaises(ValueError):
                validate_auxiliary_receipts([changed], layouts, 0)
        with self.assertRaises(ValueError):
            validate_auxiliary_receipts([receipt, receipt], layouts, 0)
        result = validate_auxiliary_receipts([receipt], layouts, 0)
        receipt['before'].clear()
        self.assertTrue(result[0]['before'])

    def test_removed_sidecar_receipt_refused_even_with_new_trusted_digest(self):
        model, _, receipt = self.prepared_model()
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank'
            self.export(directory, model, receipt)
            path = directory / 'kimi_raw_state.json'
            sidecar = json.loads(path.read_bytes())
            del sidecar['auxiliary_receipts']
            raw = json.dumps(sidecar).encode('utf-8')
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError, 'missing'):
                native_index.read_index(directory, hashlib.sha256(raw).hexdigest())


if __name__ == '__main__':
    unittest.main()
