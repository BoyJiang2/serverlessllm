import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import torch

from native_call import NativeCallFailure, _capsule_address
import native_record_call as call
from native_install import install_native_state
import test_cpu_tensor_io
import test_native_call


class NativeRecordCallTests(unittest.TestCase):
    def test_build_receipt_has_a_hard_read_bound_before_json_or_native_checks(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.json'
            raw=b'{}'+b' '*(65537-2)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'length'):
                call._bound_runtime(object(),path,hashlib.sha256(raw).hexdigest())

    def test_build_receipt_exact_bound_is_allowed_and_invalid_sha_precedes_open(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.json'
            raw=b'{}'+b' '*(65536-2)
            path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'compiled IPC-close'):
                call._bound_runtime(object(),path,hashlib.sha256(raw).hexdigest())
            with patch.object(call.os,'open',side_effect=AssertionError('unexpected open')):
                for digest in (None,True,'a'*63,'A'*64):
                    with self.assertRaisesRegex(ValueError,'external Store'):
                        call._bound_runtime(object(),path,digest)

    def test_build_receipt_rejects_nonregular_fd_and_closes_it(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.json';path.write_bytes(b'{}')
            original=call.os.fstat
            with patch.object(call.os,'fstat',side_effect=lambda fd:types.SimpleNamespace(st_mode=0)), \
                    patch.object(call.os,'close',wraps=os.close) as close:
                with self.assertRaisesRegex(ValueError,'regular Store'):
                    call._bound_runtime(object(),path,'0'*64)
            close.assert_called_once()
            with self.assertRaises(OSError):original(close.call_args.args[0])

    @unittest.skipUnless(hasattr(os,'mkfifo'),'POSIX FIFO unavailable')
    def test_build_receipt_fifo_is_rejected_without_waiting_for_writer(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'build.fifo';os.mkfifo(path)
            with self.assertRaisesRegex(ValueError,'regular Store'):
                call._bound_runtime(object(),path,'0'*64)

    def test_channel_close_error_or_overrun_cannot_return_success(self):
        for kind in ('error','overrun','boundary'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                events=[]
                _,index,sidecar,original,_=self.fixture(Path(temp)/'rank0',events)
                channel=original['SllmStoreClient'].__init__.__globals__['grpc'].insecure_channel.return_value
                clock=[0.0]
                def close():
                    events.append('close')
                    if kind=='error':raise OSError('CPU channel close failed')
                    clock[0]=30.0 if kind=='boundary' else 31.0
                channel.close.side_effect=close
                with patch.object(call.time,'monotonic',side_effect=lambda:clock[0]):
                    with self.assertRaises(NativeCallFailure) as caught:
                        self.run_core(original,index,sidecar)
                receipt=caught.exception.receipt
                self.assertEqual(receipt['stage'],'closing_channel')
                self.assertTrue(receipt['confirmation_received'])
                self.assertEqual(len(caught.exception.native_state.tensors),len(index))
                self.assertFalse(receipt['cleanup_verified'])
                self.assertTrue(receipt['worker_cleanup_required'])
                self.assertEqual(channel.close.call_count,1)
                if kind!='error':
                    self.assertTrue(receipt['deadline_exceeded'])
                    self.assertEqual(receipt['elapsed_seconds'],clock[0])
                    self.assertEqual(receipt['channel_close_elapsed_seconds'],clock[0])

    def test_channel_close_failure_does_not_replace_earlier_rpc_failure(self):
        events=[]
        with tempfile.TemporaryDirectory() as temp:
            _,index,sidecar,original,_=self.fixture(Path(temp)/'rank0',events,fail='gpu')
            channel=original['SllmStoreClient'].__init__.__globals__['grpc'].insecure_channel.return_value
            def close():events.append('close');raise OSError('CPU close also failed')
            channel.close.side_effect=close
            with self.assertRaises(NativeCallFailure) as caught:self.run_core(original,index,sidecar)
            self.assertEqual(caught.exception.receipt['stage'],'gpu_request')
            self.assertEqual(caught.exception.receipt['channel_close_errors'],['OSError'])
            self.assertNotIsInstance(caught.exception.__cause__,OSError)
            self.assertFalse(caught.exception.receipt['channel_close_returned'])
            self.assertNotIn('restore',events)
            channel.close.assert_called_once()

    def test_public_return_boundary_cannot_exceed_original_deadline(self):
        events=[]
        with tempfile.TemporaryDirectory() as temp:
            runtime,index,sidecar,original,digest=self.fixture(Path(temp)/'rank0',events)
            state=object()
            receipt=dict(stage='returned',worker_cleanup_required=True,store_cleanup_required=True,cleanup_verified=False)
            with patch.object(call,'_bound_runtime',return_value=(original,{})), \
                    patch.object(call,'_load_records_core',return_value=(state,receipt)), \
                    patch.object(call.time,'monotonic',side_effect=[0,0,30]):
                with self.assertRaises(NativeCallFailure) as caught:
                    call.load_native_records(runtime,storage_path=temp,model_path='rank0',
                        expected_sidecar_sha256=digest,rank=0,device_id=0,max_allocation_bytes=1024,
                        timeout_seconds=30,server_address='127.0.0.1:8073',build_receipt='CPU fixture',
                        expected_build_sha256='CPU fixture')
            self.assertIs(caught.exception.native_state,state)
            self.assertEqual(caught.exception.receipt['stage'],'return_boundary')
            self.assertEqual(caught.exception.receipt['total_elapsed_seconds'],30)
            self.assertTrue(caught.exception.receipt['deadline_exceeded'])
            self.assertFalse(events)  # Binary binding/core are explicit clock-boundary stand-ins here.

    def fixture(self, directory, events, fail=None):
        runtime, digest, original = test_native_call.NativeCallTests().runtime(directory, events)
        client_globals = original['SllmStoreClient'].__init__.__globals__
        rpc_error = client_globals['grpc'].RpcError
        index = json.loads((directory / 'tensor_index.json').read_bytes())
        sidecar = json.loads((directory / 'kimi_raw_state.json').read_bytes())
        buffers, capsules, by_address, by_handle = [], [], {}, {}
        def allocate(requested):
            events.append('allocate')
            if fail == 'allocation' and buffers:
                raise RuntimeError('fixture allocation failed')
            memory = bytearray(requested[0])
            buffers.append(memory)
            address = torch.frombuffer(memory, dtype=torch.uint8).data_ptr()
            by_address[address] = memory
            create = ctypes.pythonapi.PyCapsule_New
            create.argtypes, create.restype = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p], ctypes.py_object
            token = create(address, None, None)
            capsules.append(token)
            return {0: capsules[0] if fail == 'overlap' else token}
        def handles(pointers):
            token = pointers[0]
            value = str(len(by_handle)).encode().ljust(64, b'\0')
            by_handle[value] = by_address[_capsule_address(token)]
            return {0: value}
        def load(request, timeout):
            self.assertTrue(0 < timeout <= 30)
            kind = 'cpu' if request.target_device_type == 0 else 'gpu'
            events.append(kind)
            if fail == kind:
                raise rpc_error('fixture transport failure')
            if kind == 'gpu':
                payload = (directory / 'tensor.data_0').read_bytes()
                for gpu_uuid, chunks in request.chunks.items():
                    for chunk in chunks.chunks:
                        handle = request.handles[gpu_uuid].handles[chunk.handle_idx].cuda_ipc_handle
                        memory = by_handle[handle]
                        memory[chunk.dst_offset:chunk.dst_offset + chunk.size] = payload[chunk.src_offset:chunk.src_offset+chunk.size]
                        self.assertEqual(chunk.dst_offset, 0)
                self.assertEqual(len(request.handles['fixture-GPU'].handles), 4)
            return types.SimpleNamespace(model_path='rank0')
        def confirm(request, timeout):
            self.assertTrue(0 < timeout <= 30)
            events.append('confirm')
            if fail == 'close_rpc':
                raise rpc_error('fixture propagated close error')
            return types.SimpleNamespace(model_path='wrong' if fail == 'confirm' else 'rank0')
        def restore(meta, pointers, offsets):
            self.assertIn('confirm', events)
            events.append('restore')
            if fail == 'restore': raise RuntimeError('fixture wrapper failed')
            self.assertEqual(len(meta), 1)
            root = next(iter(meta))
            shape, stride, dtype = meta[root]
            memory = by_address[_capsule_address(pointers[0])]
            return {root: torch.frombuffer(memory, dtype=getattr(torch, dtype.split('.')[1]), count=math.prod(shape)).reshape(shape)}
        client_globals['storage_pb2_grpc'].StorageStub.return_value = types.SimpleNamespace(
            LoadModelAsync=load, ConfirmModel=confirm)
        original.update(allocate_cuda_memory=allocate, get_cuda_memory_handles=handles, restore_tensors=restore)
        return runtime, index, sidecar, original, digest

    def run_core(self, original, index, sidecar, cap=1024, endpoint='127.0.0.1:8073'):
        return call._load_records_core(original, index, sidecar, model_path='rank0',
            server_address=endpoint, device_id=0, expected_device='cpu',
            max_allocation_bytes=cap, timeout_seconds=30)

    def test_multi_handle_pipeline_installs_exact_bits_after_single_confirmation(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'rank0'
            runtime, index, sidecar, original, digest = self.fixture(directory, events)
            result, receipt = self.run_core(original, index, sidecar)
            self.assertEqual(receipt['allocated_bytes'], 44)
            self.assertEqual(len(receipt['allocation_attempts']), 4)
            self.assertEqual(events.count('confirm'), 1)
            self.assertEqual(events.count('restore'), 4)
            self.assertLess(events.index('confirm'), events.index('restore'))
            target = test_cpu_tensor_io.TensorIOTests().model()
            audit = install_native_state(directory, target, result.tensors, digest, rank=0,
                allocation=receipt['allocation'], independent_records=result, chunk_bytes=3)
            source = test_cpu_tensor_io.TensorIOTests().model()
            self.assertTrue(torch.equal(target.packed, source.packed))
            self.assertTrue(torch.equal(target.scale.view(torch.int16), source.scale.view(torch.int16)))
            self.assertTrue(audit['payload_verified'])
            self.assertFalse(audit['native_loader_verified'])
            self.assertFalse(hasattr(target, '_kimi_native_allocation_tensors'))
            self.assertFalse(receipt['load_dict_entry_used'])
            self.assertTrue(receipt['worker_cleanup_required'])
            self.assertTrue(receipt['store_cleanup_required'])
            self.assertTrue(receipt['channel_close_attempted'])
            self.assertTrue(receipt['channel_close_returned'])
            self.assertGreaterEqual(receipt['elapsed_seconds'],receipt['channel_close_elapsed_seconds'])

    def test_failures_leave_cleanup_obligations_and_never_return_wrappers_before_confirmation(self):
        for mode in ('cpu', 'allocation', 'overlap', 'gpu', 'confirm', 'close_rpc', 'restore'):
            events = []
            with tempfile.TemporaryDirectory() as temp:
                _, index, sidecar, original, _ = self.fixture(Path(temp) / 'rank0', events, mode)
                with self.assertRaises(NativeCallFailure) as caught:
                    self.run_core(original, index, sidecar)
                self.assertFalse(caught.exception.receipt['cleanup_verified'])
                self.assertTrue(caught.exception.receipt['store_cleanup_required'])
                self.assertEqual(caught.exception.receipt['worker_cleanup_required'], mode != 'cpu')
                if mode != 'restore': self.assertNotIn('restore', events)
                self.assertEqual(events[-1], 'close')

    def test_bad_budget_or_nonlocal_endpoint_refused_before_native_calls(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            _, index, sidecar, original, _ = self.fixture(Path(temp) / 'rank0', events)
            for cap, address in ((1, '127.0.0.1:8073'), (1024, 'qjhs003:8073'), (1024, '127.0.0.1:0')):
                with self.assertRaises(ValueError):
                    self.run_core(original, index, sidecar, cap, address)
            self.assertFalse(events)

    def test_uncompiled_patch_ledger_cannot_authorize_native_binary(self):
        path = Path(__file__).parent / 'native_patch/ledger.json'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaisesRegex(ValueError, 'compiled IPC-close'):
            call._bound_runtime(object(), path, digest)
        with self.assertRaisesRegex(ValueError, 'digest differs'):
            call._bound_runtime(object(), path, '0' * 64)

    def test_public_entry_rejects_uncompiled_binding_before_payload_scan(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            runtime, index, sidecar, original, digest = self.fixture(Path(temp) / 'rank0', events)
            build = Path(__file__).parent / 'native_patch/ledger.json'
            with patch.object(call, 'read_index', side_effect=AssertionError('unexpected payload scan')):
                with self.assertRaisesRegex(ValueError, 'compiled IPC-close'):
                    call.load_native_records(runtime, storage_path=temp, model_path='rank0',
                        expected_sidecar_sha256=digest, rank=0, device_id=0, max_allocation_bytes=1024,
                        timeout_seconds=30, server_address='127.0.0.1:8073', build_receipt=build,
                        expected_build_sha256=hashlib.sha256(build.read_bytes()).hexdigest())
            self.assertFalse(events)

    def test_public_preflight_time_cannot_disappear_from_call_budget(self):
        events = []
        with tempfile.TemporaryDirectory() as temp:
            runtime, index, sidecar, original, digest = self.fixture(Path(temp) / 'rank0', events)
            with patch.object(call, '_bound_runtime', return_value=(original, {})), \
                    patch.object(call.time, 'monotonic', side_effect=[0, 31]):
                with self.assertRaisesRegex(TimeoutError, 'preflight consumed'):
                    call.load_native_records(runtime, storage_path=temp, model_path='rank0',
                        expected_sidecar_sha256=digest, rank=0, device_id=0, max_allocation_bytes=1024,
                        timeout_seconds=30, server_address='127.0.0.1:8073', build_receipt='fixture',
                        expected_build_sha256='fixture')
            self.assertFalse(events)


if __name__ == '__main__':
    unittest.main()
