"""Recovery receipt and plan contracts; all CUDA/worker objects are CPU fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from sllm_store.v4_export_worker import V4ExportWorkerExtension
import v4_store_recovery as recovery
from test_v4_state import identity


class RecoveryContracts(unittest.TestCase):
    def test_shutdown_wait_is_bounded_and_requires_normal_exit(self):
        for remaining, expected in ((200, 90), (28, 23)):
            with self.subTest(remaining=remaining), tempfile.TemporaryDirectory() as directory:
                daemon=mock.Mock(pid=123)
                daemon.wait.return_value=0
                with mock.patch.object(recovery.time,'monotonic_ns',return_value=100_000_000_000):
                    recovery.stop_daemon(daemon,(100+remaining)*1_000_000_000,Path(directory))
                daemon.wait.assert_called_once_with(timeout=expected)
                self.assertEqual(json.loads((Path(directory)/'daemon-shutdown-finished.json').read_bytes())['exit_code'],0)

    def test_shutdown_timeout_nonzero_and_exhausted_budget_stay_failed(self):
        for kind in ('timeout','nonzero','expired'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                daemon=mock.Mock(pid=123)
                if kind=='timeout':daemon.wait.side_effect=subprocess.TimeoutExpired(['cpu-fixture-daemon'],90)
                else:daemon.wait.return_value=1
                deadline=104_000_000_000 if kind=='expired' else 300_000_000_000
                with mock.patch.object(recovery.time,'monotonic_ns',return_value=100_000_000_000):
                    with self.assertRaises((TimeoutError,subprocess.TimeoutExpired,RuntimeError)):
                        recovery.stop_daemon(daemon,deadline,Path(directory))
                if kind=='expired':daemon.send_signal.assert_not_called()
                elif kind=='timeout':
                    self.assertTrue((Path(directory)/'daemon-shutdown-started.json').exists())
                    self.assertFalse((Path(directory)/'daemon-shutdown-finished.json').exists())

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

    def test_diagnostic_plan_labels_smaller_pool_without_rewriting_full_cohort_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);model=root/'model';model.mkdir()
            fixture=Path(__file__).resolve().parents[1]/'fixtures/v4_config.json'
            (model/'config.json').write_bytes(fixture.read_bytes())
            (root/'identity.json').write_text(json.dumps(identity()))
            (root/'pool.json').write_text(json.dumps(dict(chunk_bytes=33554432,
                                                       pool_bytes_for_all_groups=890937278464)))
            argv=['recovery','--model',str(model),'--store',str(root/'store'),'--identity',
                  str(root/'identity.json'),'--pool-config',str(root/'pool.json'),'--diagnostic-pool']
            output=io.StringIO()
            with mock.patch('sys.argv',argv),mock.patch.dict(os.environ,STORAGE_PATH=str(root)),contextlib.redirect_stdout(output):
                recovery.main()
            plan=json.loads(output.getvalue())
            self.assertEqual(plan['pool_bytes'],128*1024**3)
            self.assertEqual(plan['full_cohort_pool_bytes'],890937278464)
            self.assertTrue(plan['diagnostic_pool'])
            self.assertFalse(plan['gpu_or_model_started'])
            self.assertFalse((root/'store').exists())

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
