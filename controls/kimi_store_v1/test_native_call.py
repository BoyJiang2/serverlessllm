import builtins
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import types
import unittest
import uuid
from unittest.mock import Mock, patch

import torch

import native_call
import test_cpu_tensor_io
import test_install_state


ROOT = Path(__file__).parent / 'fixtures'


def function_code(path, name):
    code = compile(path.read_bytes(), str(path.resolve()), 'exec', dont_inherit=True)
    return next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == name)


class NativeCallTests(unittest.TestCase):
    def runtime(self, directory, events, *, fail=None):
        source = test_cpu_tensor_io.TensorIOTests().model()
        _, digest = test_install_state.InstallTests().export(directory, source)
        class RpcError(Exception):
            def code(self):
                return 'CANCELLED'
        protobuf = types.SimpleNamespace(DeviceType=types.SimpleNamespace(DEVICE_TYPE_CPU=0, DEVICE_TYPE_GPU=1))
        for name in ('LoadModelRequest', 'MemCopyChunkList', 'MemCopyChunk', 'MemCopyHandleList',
                     'MemCopyHandle', 'ConfirmModelRequest'):
            setattr(protobuf, name, lambda **kwargs: types.SimpleNamespace(**kwargs))
        namespace = dict(__builtins__=builtins.__dict__, __name__='native_client_fixture',
            grpc=types.SimpleNamespace(RpcError=RpcError, StatusCode=types.SimpleNamespace(CANCELLED='CANCELLED')),
            storage_pb2=protobuf, logger=Mock())
        captured = {}
        def load(request, timeout):
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 30)
            events.append('cpu' if request.target_device_type == 0 else 'gpu')
            if fail == ('cpu' if request.target_device_type == 0 else 'gpu'):
                raise RpcError('fixture RPC failure')
            if request.target_device_type == 1:
                payload = (directory / 'tensor.data_0').read_bytes()
                for chunks in request.chunks.values():
                    for chunk in chunks.chunks:
                        captured['memory'][chunk.dst_offset:chunk.dst_offset + chunk.size] = \
                            payload[chunk.src_offset:chunk.src_offset + chunk.size]
            return types.SimpleNamespace(model_path='rank0')
        def confirm(request, timeout):
            self.assertGreater(timeout, 0)
            events.append('confirm')
            return types.SimpleNamespace(model_path='wrong' if fail == 'confirm' else 'rank0')
        channel = Mock()
        channel.close.side_effect = lambda: events.append('close')
        stub = types.SimpleNamespace(LoadModelAsync=load, ConfirmModel=confirm)
        namespace['grpc'].insecure_channel = Mock(return_value=channel)
        namespace['storage_pb2_grpc'] = types.SimpleNamespace(StorageStub=Mock(return_value=stub))
        client = builtins.__build_class__(types.FunctionType(
            function_code(ROOT / 'native_client.py', 'SllmStoreClient'), namespace), 'SllmStoreClient')
        globs = dict(__builtins__=builtins.__dict__, json=json, os=os, time=time, uuid=uuid,
                     logger=Mock(), SllmStoreClient=client)
        for name in ('calculate_device_memory', 'calculate_tensor_device_offsets'):
            globs[name] = types.FunctionType(function_code(ROOT / 'native_utils.py', name), globs)
        globs['_expand_tensor_name'] = types.FunctionType(
            function_code(ROOT / 'native_device_map_utils.py', '_expand_tensor_name'), globs)
        globs['_get_uuid'] = types.FunctionType(function_code(ROOT / 'native_torch.py', '_get_uuid'), globs)
        def allocate(memory):
            events.append('allocate')
            captured['memory'] = bytearray(memory[0])
            captured['base'] = torch.frombuffer(captured['memory'], dtype=torch.uint8).data_ptr()
            new_capsule = ctypes.pythonapi.PyCapsule_New
            new_capsule.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p]
            new_capsule.restype = ctypes.py_object
            return {0: new_capsule(captured['base'], None, None)}
        def restore(meta, pointers, offsets):
            events.append('restore')
            self.assertIn('confirm', events)
            self.assertEqual(native_call._capsule_address(pointers[0]), captured['base'])
            if fail == 'restore':
                raise RuntimeError('fixture restore failed')
            result = {}
            for name, (shape, stride, dtype) in meta.items():
                dtype = getattr(torch, dtype.split('.')[1])
                result[name] = (torch.frombuffer(captured['memory'], dtype=dtype,
                    count=math.prod(shape), offset=offsets[0][name]).reshape(shape)
                    if math.prod(shape) else torch.empty(shape, dtype=dtype))
            return result
        globs.update(allocate_cuda_memory=allocate, restore_tensors=restore,
                     get_cuda_memory_handles=lambda pointers: {0: 'fixture-IPC'},
                     get_device_uuid_map=lambda: {0: 'fixture-GPU'})
        function = types.FunctionType(function_code(ROOT / 'native_torch.py', 'load_dict_non_blocking'), globs)
        runtime = types.SimpleNamespace(load_dict_non_blocking=function)
        return runtime, digest, globs

    def call(self, runtime, root, digest, **kwargs):
        return native_call.load_native_return(runtime, storage_path=root, model_path='rank0',
            expected_sidecar_sha256=digest, rank=0, device_id=0,
            max_allocation_bytes=kwargs.pop('cap', 1024), timeout_seconds=30, **kwargs)

    def test_same_call_allocation_and_confirmation_before_tensor_wrappers(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank0'
            runtime, digest, globs = self.runtime(directory, events)
            old_client, old_allocate = globs['SllmStoreClient'], globs['allocate_cuda_memory']
            state, receipt = self.call(runtime, temp, digest)
            self.assertEqual(events, ['cpu', 'allocate', 'gpu', 'confirm', 'restore', 'close'])
            self.assertEqual(receipt['allocation']['allocation_bytes'], 44)
            self.assertEqual(receipt['allocation']['device_offsets']['packed'],
                             receipt['allocation']['device_offsets']['packed_alias'])
            self.assertTrue(receipt['confirmation_received'])
            self.assertTrue(receipt['store_cleanup_required'])
            self.assertFalse(receipt['native_loader_verified'])
            self.assertFalse(receipt['cleanup_verified'])
            self.assertEqual(state['packed'][0].item(), -1)
            self.assertIs(globs['SllmStoreClient'], old_client)
            self.assertIs(globs['allocate_cuda_memory'], old_allocate)

    def test_cpu_gpu_confirmation_or_restore_failure_preserves_cleanup_status(self):
        for stage in ('cpu', 'gpu', 'confirm', 'restore'):
            events = []
            with tempfile.TemporaryDirectory() as temp:
                runtime, digest, _ = self.runtime(Path(temp) / 'rank0', events, fail=stage)
                with self.assertRaises(native_call.NativeCallFailure) as caught:
                    self.call(runtime, temp, digest)
                self.assertEqual(caught.exception.receipt['worker_cleanup_required'], stage != 'cpu')
                self.assertFalse(caught.exception.receipt['cleanup_verified'])
                self.assertTrue(caught.exception.receipt['store_cleanup_required'])
                if stage in ('cpu', 'gpu', 'confirm'):
                    self.assertNotIn('restore', events)
                self.assertEqual(events[-1], 'close')

    def test_small_cap_or_bad_path_refused_without_native_side_effects(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            runtime, digest, _ = self.runtime(Path(temp) / 'rank0', events)
            with self.assertRaisesRegex(ValueError, 'budget'):
                self.call(runtime, temp, digest, cap=1)
            with self.assertRaisesRegex(ValueError, 'relative'):
                native_call.load_native_return(runtime, storage_path=temp, model_path='../rank0',
                    expected_sidecar_sha256=digest, rank=0, device_id=0,
                    max_allocation_bytes=1024, timeout_seconds=30)
            self.assertFalse(events)

    def test_preflight_elapsed_budget_refused_before_client_creation(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            runtime, digest, _ = self.runtime(Path(temp) / 'rank0', events)
            with patch.object(native_call.time, 'monotonic', side_effect=[0, 31, 32]):
                with self.assertRaises(native_call.NativeCallFailure) as caught:
                    self.call(runtime, temp, digest)
            self.assertEqual(caught.exception.receipt['error_type'], 'TimeoutError')
            self.assertFalse(caught.exception.receipt['worker_cleanup_required'])
            self.assertFalse(caught.exception.receipt['store_request_attempted'])
            self.assertFalse(events)

    def test_void_pointer_capsule_required_not_plain_integer(self):
        for invalid in (None, 1234, True, object()):
            with self.assertRaises(ValueError):
                native_call._capsule_address(invalid)


if __name__ == '__main__':
    unittest.main()
