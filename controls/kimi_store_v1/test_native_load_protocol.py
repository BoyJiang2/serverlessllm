"""Pinned Python protocol with mocked Store/CUDA boundaries; no native runtime."""
import ast
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import types
import typing
import unittest
import uuid
from unittest.mock import Mock


class NativeProtocolTests(unittest.TestCase):
    def protocol(self, storage, *, cpu_ok=True, gpu_ok=True, confirm_ok=True):
        path = Path(__file__).parent / 'fixtures/native_torch.py'
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),
            'a4fb54b68a3895bf6015cbefe3bc7a7fd51e0af4f37cda72d1b49c9fabce29e9')
        wanted = {'_get_uuid', 'load_dict', 'load_dict_non_blocking'}
        nodes = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name in wanted]
        client = Mock()
        client.load_into_cpu.return_value = cpu_ok
        client.load_into_gpu.return_value = gpu_ok
        client.confirm_model_loaded.return_value = confirm_ok
        restored = {'packed': object()}
        namespace = dict(os=os, json=json, time=time, uuid=uuid,
            Dict=typing.Dict, Union=typing.Union, Optional=typing.Optional,
            SllmStoreClient=Mock(return_value=client), logger=Mock(),
            _expand_tensor_name=Mock(return_value={'packed': 0}),
            calculate_device_memory=Mock(return_value={0: 8}),
            allocate_cuda_memory=Mock(return_value={0: 1234}),
            get_cuda_memory_handles=Mock(return_value={0: 'mock-handle'}),
            get_device_uuid_map=Mock(return_value={0: 'mock-device'}),
            calculate_tensor_device_offsets=Mock(return_value=({0: {'packed': 0}}, {0: [(0, 8, 0, 0)]})),
            restore_tensors=Mock(return_value=restored))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
        directory = Path(storage) / 'rank0'
        directory.mkdir()
        (directory / 'tensor_index.json').write_text(json.dumps(
            {'packed': [0, 8, [2], [1], 'torch.int32']}), encoding='utf-8')
        return namespace, client, restored

    def test_blocking_returns_physical_state_after_confirmation_call(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace, client, restored = self.protocol(temp)
            result = namespace['load_dict']('rank0', {'': 0}, temp)
        self.assertIs(result, restored)
        namespace['restore_tensors'].assert_called_once_with(
            {'packed': ([2], [1], 'torch.int32')}, {0: 1234}, {0: {'packed': 0}})
        args = client.load_into_gpu.call_args.args
        self.assertEqual(args[0], 'rank0')
        self.assertEqual(args[2:], ({'mock-device': [(0, 8, 0, 0)]}, {'mock-device': ['mock-handle']}))
        client.confirm_model_loaded.assert_called_once_with('rank0', args[1])

    def test_nonblocking_return_does_not_confirm_and_false_confirmation_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace, client, restored = self.protocol(temp, confirm_ok=False)
            replica, result = namespace['load_dict_non_blocking']('rank0', {'': 0}, temp)
            self.assertIs(result, restored)
            self.assertEqual(replica, client.load_into_gpu.call_args.args[1])
            client.confirm_model_loaded.assert_not_called()
            # Existing Python wrapper ignores the false return; this is NOT readiness proof.
            self.assertIs(namespace['load_dict']('rank0', {'': 0}, temp), restored)
            client.confirm_model_loaded.assert_called_once()

    def test_cpu_or_gpu_refusal_never_returns_restored_state(self):
        for stage in ('cpu', 'gpu'):
            with tempfile.TemporaryDirectory() as temp:
                namespace, client, _ = self.protocol(temp, cpu_ok=stage != 'cpu', gpu_ok=stage != 'gpu')
                with self.assertRaises(ValueError):
                    namespace['load_dict']('rank0', {'': 0}, temp)
                namespace['restore_tensors'].assert_not_called()
                client.confirm_model_loaded.assert_not_called()
                if stage == 'cpu':
                    namespace['allocate_cuda_memory'].assert_not_called()


if __name__ == '__main__':
    unittest.main()
