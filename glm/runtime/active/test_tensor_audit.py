"""Pinned helper import/callback reachability without torch/CUDA execution."""
import hashlib
import importlib.util
import pathlib
import pickle
import sys
import types
import unittest
from unittest.mock import patch


class AuditDependency(unittest.TestCase):
    def test_historical_helper_bytes_and_callback_import(self):
        path=pathlib.Path(__file__).with_name('tensor_audit.py')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '069ab4d8cfdc3f57819358222b6c1a1e8973c795df2131974eb0a3b9161bbcff')
        spec=importlib.util.spec_from_file_location('tensor_audit',path)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'tensor_audit':module,'torch':types.ModuleType('torch')}):
            spec.loader.exec_module(module)
            self.assertIs(pickle.loads(pickle.dumps(module.audit_model)),module.audit_model)
            self.assertEqual(module.audit_model.__module__,'tensor_audit')


if __name__=='__main__':unittest.main()
