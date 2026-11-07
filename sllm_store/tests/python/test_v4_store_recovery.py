"""Recovery receipt and plan contracts; all CUDA/worker objects are CPU fixtures."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from sllm_store.v4_export_worker import V4ExportWorkerExtension
import v4_store_recovery as recovery
from test_v4_state import identity


class RecoveryContracts(unittest.TestCase):
    def setUp(self):
        self.sync = mock.Mock()
        self.contexts, self.quarantine = [], []
        modules = {'torch': NS(cuda=NS(synchronize=self.sync)),
                   'sllm_store.torch': NS(owned_load_quarantine=lambda: self.quarantine),
                   'sllm_store.vllm_v4_loader': NS(grouped_restore_contexts=lambda: self.contexts)}
        patch = mock.patch.dict(sys.modules, modules)
        patch.start()
        self.addCleanup(patch.stop)
        self.model = NS(_sllm_v4_stage='postprocessed',
                        _sllm_grouped_completed=tuple(f'expert_{i:04d}' for i in range(61)),
                        _sllm_storage_owners=(NS(is_cuda=True),))
        self.worker = V4ExportWorkerExtension()
        self.worker.load_config = NS(load_format='sllm_v4_grouped_store',
                                     model_loader_extra_config=dict(identity=identity()))
        self.worker.get_model = lambda: self.model
        self.worker.rank, self.worker.device = 2, 'cuda:2'

    def test_complete_recovery_reports_retained_cuda_owners_after_sync(self):
        rows=[]
        for rank in range(8):
            self.worker.rank=rank
            rows.append(self.worker.sllm_v4_recovery_receipt(identity()))
        recovery.validate_receipts(rows, identity())
        self.assertEqual(self.sync.call_count, 8)
        self.assertTrue(all(row['completed_expert_groups']==61 for row in rows))

    def test_missing_reordered_or_failed_groups_cannot_report_success(self):
        original = self.model._sllm_grouped_completed
        for groups in (original[:-1], tuple(reversed(original))):
            self.model._sllm_grouped_completed=groups
            with self.assertRaises(ValueError):
                self.worker.sllm_v4_recovery_receipt(identity())
        self.sync.assert_not_called()

    def test_quarantine_contexts_missing_or_non_cuda_owners_cannot_report_success(self):
        for kind in ('quarantine','contexts','missing','cpu'):
            with self.subTest(kind=kind):
                self.quarantine[:] = [1] if kind=='quarantine' else []
                self.contexts[:] = [1] if kind=='contexts' else []
                self.model._sllm_storage_owners=() if kind=='missing' else (NS(is_cuda=kind!='cpu'),)
                with self.assertRaises(ValueError):
                    self.worker.sllm_v4_recovery_receipt(identity())
        self.sync.assert_not_called()

    def test_wrong_loader_identity_and_delayed_cuda_error_reject(self):
        self.worker.load_config.load_format='sllm_v4_grouped_export'
        with self.assertRaises(ValueError):self.worker.sllm_v4_recovery_receipt(identity())
        self.worker.load_config.load_format='sllm_v4_grouped_store'
        with self.assertRaises(ValueError):self.worker.sllm_v4_recovery_receipt(dict(identity(),conversion_id='foreign'))
        self.sync.side_effect=RuntimeError('delayed CUDA fixture')
        with self.assertRaisesRegex(RuntimeError,'delayed CUDA'):self.worker.sllm_v4_recovery_receipt(identity())

    def test_partial_duplicate_or_foreign_receipt_is_rejected(self):
        row=self.worker.sllm_v4_recovery_receipt(identity())
        rows=[dict(row,rank=i) for i in range(8)]
        for invalid in (rows[:-1],rows[:-1]+[rows[0]],rows[:-1]+[dict(rows[-1],identity={})]):
            with self.assertRaises(ValueError):recovery.validate_receipts(invalid,identity())

    def test_plan_uses_existing_frozen_config_and_restore_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'model';model.mkdir()
            fixture=Path(__file__).resolve().parents[1]/'fixtures/v4_config.json'
            (model/'config.json').write_bytes(fixture.read_bytes())
            with mock.patch.dict(os.environ,STORAGE_PATH=str(root)):
                config=recovery.recovery_config(str(model),str(root/'store'),identity())
            self.assertEqual(config['load_format'],'sllm_v4_grouped_store')
            self.assertEqual(config['kv_cache_dtype'],'fp8')
            self.assertEqual(config['distributed_executor_backend'],'mp')
            self.assertEqual(config['seed'],20261105)
            self.assertFalse((root/'store').exists())


if __name__=='__main__':unittest.main()
