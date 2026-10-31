import ctypes
import hashlib
import json
import math
import os
import io
import mmap
import shutil
import struct
import subprocess
from pathlib import Path
import tempfile
import types
import time
import unittest
from unittest.mock import patch

import torch

from native_call import NativeCallFailure, _capsule_address
import native_record_call as call
from native_install import install_native_state
import test_cpu_tensor_io
import test_native_call


@unittest.skipUnless(call.sys.platform == 'linux', 'Linux executable mapping identity required')
class MappedLibraryTests(unittest.TestCase):
    def extension(self):
        import _json
        path = Path(_json.__file__).resolve(strict=True)
        return path, call._file_digest(path)

    def test_actual_stdlib_extension_mapping_and_unmapped_identical_copy(self):
        path, digest = self.extension()
        receipt = call._mapped_extension_identity(path, digest)
        self.assertEqual(receipt['inode'], path.stat().st_ino)
        self.assertTrue(receipt['mapped_inode_verified'])
        self.assertGreater(receipt['executable_segments'], 0)
        self.assertFalse(receipt['mapped_library_bytes_verified'])
        self.assertFalse(receipt['function_address_verified'])
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp) / 'unmapped.so'
            shutil.copyfile(path, copy)
            self.assertEqual(call._file_digest(copy), digest)
            with self.assertRaisesRegex(ValueError, 'no executable self mapping'):
                call._mapped_extension_identity(copy, digest)

    def test_readonly_file_mapping_is_not_executable_library_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'bytes'
            path.write_bytes(b'CPU mapping control')
            with path.open('rb') as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ):
                with self.assertRaisesRegex(ValueError, 'no executable self mapping'):
                    call._mapped_extension_identity(path, call._file_digest(path))

    def test_replaced_path_same_bytes_rejected_while_original_library_remains_loaded(self):
        compiler = shutil.which('g++')
        if compiler is None:
            self.skipTest('existing CPU g++ unavailable')
        with tempfile.TemporaryDirectory() as temp:
            source, library = Path(temp) / 'probe.cpp', Path(temp) / 'probe.so'
            source.write_text('extern "C" int kimi_mapping_probe() { return 7; }\n', encoding='utf-8')
            subprocess.run([compiler, '-shared', '-fPIC', str(source), '-o', str(library)],
                           check=True, capture_output=True, timeout=30)
            handle = ctypes.CDLL(str(library))
            self.assertEqual(handle.kimi_mapping_probe(), 7)
            digest = call._file_digest(library)
            receipt = call._mapped_extension_identity(library, digest)
            moved = library.with_suffix('.old')
            library.rename(moved)
            shutil.copyfile(moved, library)
            self.assertNotEqual(receipt['inode'], library.stat().st_ino)
            self.assertEqual(call._file_digest(library), digest)
            self.assertEqual(handle.kimi_mapping_probe(), 7)
            with self.assertRaisesRegex(ValueError, 'no executable self mapping'):
                call._mapped_extension_identity(library, digest)

    def test_invalid_sha_precedes_open(self):
        with patch.object(call.os, 'open', side_effect=AssertionError('unexpected open')):
            for digest in (None, True, 'a' * 63, 'A' * 64):
                with self.assertRaisesRegex(ValueError, 'external native extension SHA'):
                    call._mapped_extension_identity('not-read', digest)

    def test_maps_growth_and_malformed_rows_fail_closed(self):
        path, digest = self.extension()
        for raw in (b'x' * (4 * 1024**2 + 1), b'malformed\n'):
            with patch.object(Path, 'open', return_value=io.BytesIO(raw)):
                with self.assertRaisesRegex(ValueError, 'mapping inventory'):
                    call._mapped_extension_identity(path, digest)

    def test_fifo_rejected_without_waiting_for_writer_and_fd_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'library.fifo'
            os.mkfifo(path)
            with patch.object(call.os, 'close', wraps=os.close) as close:
                with self.assertRaisesRegex(ValueError, 'bounded regular'):
                    call._mapped_extension_identity(path, 'a' * 64)
            close.assert_called_once()
            with self.assertRaises(OSError):
                os.fstat(close.call_args.args[0])


class P0ComponentTests(unittest.TestCase):
    def options(self):
        return dict(run_id='kimi-p0-20261031T000000Z-01234567',holder='a'*32,
            gpu_uuid='GPU-12345678-1234-1234-1234-123456789abc',
            deadline_monotonic=time.monotonic()+30,max_payload_bytes=40)

    def test_worker_cli_binds_p0_input_and_original_budget_without_claiming_guard(self):
        import io
        from datetime import datetime, timedelta, timezone
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); options=self.options()
            (root/'code').mkdir(); worker=root/'code/native_record_call.py';worker.write_text('# CPU CLI fixture')
            entered=datetime.now(timezone.utc)-timedelta(seconds=20)
            identity=dict(run_id=options['run_id'],holder=options['holder'],code_commit='a'*40,
                spec_sha256='b'*64,source_manifest_sha256='c'*64)
            registration=dict(schema='kimi-p0-worker-input-v1',batch_entered_utc=entered.isoformat(),
                plan_sha256='d'*64)
            receipt=dict(identity,manifest_sha256='c'*64,gpu_uuid=options['gpu_uuid'],
                image_id='sha256:'+'e'*64,component_max_seconds=60,max_payload_bytes=40)
            schedule=dict(guard_fire_by_utc=(entered+timedelta(seconds=30)).isoformat())
            bound=(root/'p0-worker-input.json',root,registration,receipt,schedule,identity)
            with patch.object(call,'__file__',str(worker)), \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound) as verify, \
                    patch.object(call,'run_cuda_ipc_component_probe',return_value=dict(torch_cuda_ipc_verified=True)) as component, \
                    patch('sys.stdout',new_callable=io.StringIO) as output:
                started=time.monotonic()
                call.p0_main(['--worker-input',str(root/'p0-worker-input.json'),'--worker-input-sha','f'*64])
                verify.assert_called_once_with(str(root/'p0-worker-input.json'),'f'*64)
                remaining=component.call_args.kwargs['deadline_monotonic']-started
                self.assertGreater(remaining,3);self.assertLess(remaining,11)
                result=json.loads(output.getvalue())
                self.assertFalse(result['guard_verified']);self.assertFalse(result['resources_released'])
                self.assertFalse(result['runtime_admitted'])
                rows=[json.loads(line) for line in (root/'p0-component-events.jsonl').read_text().splitlines()]
                self.assertEqual([row['stage'] for row in rows],['inputs_verified','component_returned'])
                component.reset_mock()
                with self.assertRaises(FileExistsError):
                    call.p0_main(['--worker-input',str(root/'p0-worker-input.json'),'--worker-input-sha','f'*64])
                component.assert_not_called()
                (root/'p0-component-events.jsonl').unlink()
                with patch.object(call.os,'fsync',side_effect=[None,OSError('terminal fsync')]):
                    with self.assertRaises(OSError) as caught:
                        call.p0_main(['--worker-input',str(root/'p0-worker-input.json'),'--worker-input-sha','f'*64])
                self.assertIs(caught.exception.p0_receipt,component.return_value)

    def test_worker_cli_refuses_source_or_expired_and_persists_owned_failure(self):
        from datetime import datetime,timedelta,timezone
        import source_bundle
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);options=self.options()
            (root/'code').mkdir(); worker=root/'code/native_record_call.py';worker.write_text('# CPU CLI fixture')
            now=datetime.now(timezone.utc)
            registration=dict(schema='kimi-source-watchdog-registration-v1',batch_entered_utc=now.isoformat(),plan_sha256='d'*64)
            receipt=dict(run_id=options['run_id'],holder=options['holder'],code_commit='a'*40,
                spec_sha256='b'*64,manifest_sha256='c'*64,gpu_uuid=options['gpu_uuid'],
                image_id='sha256:'+'e'*64,component_max_seconds=60,max_payload_bytes=40)
            schedule=dict(guard_fire_by_utc=(now+timedelta(seconds=30)).isoformat())
            identity={key:receipt[key] for key in ('run_id','holder','code_commit','spec_sha256')}
            bound=(root/'p0-worker-input.json',root,registration,receipt,schedule,identity)
            with patch.object(call,'__file__',str(worker)), \
                    patch.object(source_bundle,'verify_p0_worker_input',return_value=bound), \
                    patch.object(call,'run_cuda_ipc_component_probe') as component:
                args=['--worker-input',str(root/'p0-worker-input.json'),'--worker-input-sha','f'*64]
                with self.assertRaisesRegex(ValueError,'source-model'):call.p0_main(args)
                registration['schema']='kimi-p0-worker-input-v1'
                schedule['guard_fire_by_utc']=(now-timedelta(seconds=1)).isoformat()
                with self.assertRaises(TimeoutError):call.p0_main(args)
                component.assert_not_called()
                self.assertFalse((root/'p0-component-events.jsonl').exists())
                schedule['guard_fire_by_utc']=(now+timedelta(seconds=30)).isoformat()
                (root/'wrong_worker.py').write_text('# wrong CPU entry')
                with patch.object(call,'__file__',str(root/'wrong_worker.py')):
                    with self.assertRaisesRegex(ValueError,'verified P0 worker'):
                        call.p0_main(args)
                component.assert_not_called()
                error=RuntimeError('controlled CPU component failure');error.p0_receipt=dict(stage='controlled',failed=True)
                component.side_effect=error
                with self.assertRaisesRegex(RuntimeError,'controlled CPU'):call.p0_main(args)
                rows=[json.loads(line) for line in (root/'p0-component-events.jsonl').read_text().splitlines()]
                self.assertEqual(rows[-1]['stage'],'failed')
                self.assertEqual(rows[-1]['details']['component'],error.p0_receipt)
                self.assertFalse(rows[-1]['details']['resources_released'])
                (root/'p0-component-events.jsonl').unlink()
                error.p0_owners=object()
                with patch.object(call.os,'fsync',side_effect=[None,OSError('controlled fsync')]):
                    with self.assertRaises(RuntimeError) as caught:call.p0_main(args)
                self.assertIs(caught.exception,error)
                self.assertIsNotNone(caught.exception.p0_owners)
                self.assertEqual(caught.exception.p0_evidence_error['error_type'],'OSError')
                (root/'p0-component-events.jsonl').unlink()
                component.side_effect=None;result=dict(torch_cuda_ipc_verified=True)
                component.return_value=result
                with patch.object(call.time,'monotonic',side_effect=[100.,100.,200.]):
                    with self.assertRaises(TimeoutError) as caught:call.p0_main(args)
                self.assertIs(caught.exception.p0_receipt,result)
                rows=[json.loads(line) for line in (root/'p0-component-events.jsonl').read_text().splitlines()]
                self.assertEqual(rows[-1]['details']['component'],result)

    def test_small_payload_preserves_packed_bf16_f32_bits_and_alias(self):
        tensors=call._p0_tensors()
        expected=dict(packed=struct.pack('<iiii',-1,-2147483648,0x01234567,0),
            scale=struct.pack('<HHHH',0x8000,0x7fc1,0,0x3f80),
            router=struct.pack('<iiii',-2147483648,2143289345,1065353216,0),empty=b'')
        self.assertEqual(sum(map(len,expected.values())),40)
        self.assertIs(tensors['packed_alias'],tensors['packed'])
        self.assertEqual(call._p0_fingerprints(tensors,'cpu'),
                         {k:hashlib.sha256(v).hexdigest() for k,v in expected.items()})
        tensors['packed_alias']=tensors['packed'].clone()
        with self.assertRaisesRegex(ValueError,'alias'):call._p0_fingerprints(tensors,'cpu')

    def test_cpu_or_wrong_cuda_abi_rejected_before_any_gpu_query(self):
        with patch.object(torch,'__version__','2.10.0+cpu'), \
                patch.object(torch.cuda,'device_count',side_effect=AssertionError('unexpected GPU query')):
            with self.assertRaisesRegex(ValueError,'no CPU fallback') as caught:
                call.run_cuda_ipc_component_probe(**self.options())
        self.assertFalse(caught.exception.p0_receipt['torch_cuda_ipc_verified'])
        self.assertFalse(caught.exception.p0_owners)

    def test_bad_p0_identity_cap_or_original_deadline_rejected_before_device(self):
        for key,value in (('run_id','wrong'),('holder','a'*31),('gpu_uuid','GPU-unknown'),
                          ('max_payload_bytes',39),('max_payload_bytes',True),
                          ('max_payload_bytes',2*1024**2),('deadline_monotonic',float('nan')),
                          ('deadline_monotonic',time.monotonic()+2),('deadline_monotonic',time.monotonic()+1000)):
            options=self.options();options[key]=value
            with patch.object(call,'_p0_device',side_effect=AssertionError('unexpected device')):
                with self.assertRaises(ValueError):call.run_cuda_ipc_component_probe(**options)

    def transport(self,kind):
        events=[];response={}
        class Connection:
            def send(self,message):
                state=message['tensors'];response.update(status='ok',before=message['expected'])
                if kind=='wrong_before':response['before']={}
                changed=call._p0_tensors();changed['packed'][0]=0x13579bdf
                response['after']=call._p0_fingerprints(changed,'cpu')
                if kind!='not_shared':state['packed'][0]=0x13579bdf
            def poll(self,timeout):return kind!='timeout'
            def recv(self):return response
            def close(self):
                events.append('pipe_close')
                if kind=='close_error':raise OSError('owned pipe fault')
        class ChildConnection:
            def close(self):events.append('child_pipe_close')
        class Process:
            pid=None;exitcode=None;alive=False
            def start(self):self.pid=999;self.alive=True;events.append('start')
            def join(self,timeout):
                if kind not in ('timeout','unkillable'):
                    self.alive=False;self.exitcode=1 if kind=='child_error' else 0
            def is_alive(self):return self.alive
            def terminate(self):
                events.append('terminate')
                if kind!='unkillable':self.alive=False;self.exitcode=-15
            def kill(self):events.append('kill')
            def close(self):events.append('process_close')
        process=Process()
        context=types.SimpleNamespace(Pipe=lambda **kw:(Connection(),ChildConnection()),
                                      Process=lambda **kw:process)
        return context,process,events

    def test_parent_cpu_transport_control_is_not_real_cuda_validation(self):
        import torch.multiprocessing as mp
        context,process,events=self.transport('ok')
        with patch.object(call,'_p0_device',return_value=torch.device('cpu')), \
                patch.object(mp,'get_context',return_value=context) as factory:
            result=call.run_cuda_ipc_component_probe(**self.options())
        factory.assert_called_once_with('spawn')
        self.assertTrue(result['torch_cuda_ipc_verified'])  # Explicit CPU device and transport stand-ins.
        self.assertEqual(result['child_exitcode'],0)
        self.assertEqual(result['producer_pid'],os.getpid())
        self.assertEqual(result['consumer_pid'],999)
        self.assertTrue(result['child_start_attempted'])
        self.assertFalse(process.is_alive())
        self.assertIn('process_close',events)
        for name in ('runtime_admitted','store_created','native_loader_verified','full_model_verified','resources_released'):
            self.assertFalse(result[name])

    def test_parent_failure_controls_keep_owners_and_only_stop_owned_consumer(self):
        import torch.multiprocessing as mp
        for kind in ('wrong_before','not_shared','timeout','child_error','close_error','unkillable'):
            with self.subTest(kind=kind):
                context,process,events=self.transport(kind)
                with patch.object(call,'_p0_device',return_value=torch.device('cpu')), \
                        patch.object(mp,'get_context',return_value=context):
                    with self.assertRaises((ValueError,RuntimeError,TimeoutError,OSError)) as caught:
                        call.run_cuda_ipc_component_probe(**self.options())
                self.assertTrue(caught.exception.p0_owners)
                self.assertFalse(caught.exception.p0_receipt['resources_released'])
                if kind=='unkillable':self.assertIn('kill',events)
                else:self.assertFalse(process.is_alive())

    def test_expired_consumer_reports_failure_before_cuda_and_closes_pipe(self):
        from unittest.mock import Mock
        connection=Mock()
        with patch.object(call,'_p0_device',side_effect=AssertionError('unexpected CUDA')):
            call._p0_ipc_child(connection,self.options()['gpu_uuid'],time.monotonic()-1)
        self.assertEqual(connection.send.call_args.args[0]['error_type'],'TimeoutError')
        connection.close.assert_called_once()


class NativeRecordCallTests(unittest.TestCase):
    def test_runtime_binding_requires_mapping_check_and_keeps_memory_and_server_claims_false(self):
        def callback():
            pass
        module = types.SimpleNamespace(__file__='checkpoint-library.so')
        original = dict(_get_uuid=callback, SllmStoreClient=types.SimpleNamespace(**{
            n: callback for n in ('__init__', 'load_into_cpu', 'load_into_gpu', 'confirm_model_loaded')}))
        for name in ('allocate_cuda_memory', 'get_cuda_memory_handles', 'get_device_uuid_map', 'restore_tensors'):
            original[name] = callback
            setattr(module, name, callback)
        native = types.SimpleNamespace(load_dict_non_blocking=types.FunctionType(callback.__code__, original))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'build.json'
            raw = json.dumps(dict(schema='kimi-independent-native-build-v1', compiled_native_store=True,
                model_source_sha256=call.PATCH_SHA, checkpoint_library_sha256='a' * 64,
                store_library_path='store-library.so', store_library_sha256='b' * 64)).encode()
            path.write_bytes(raw)
            for failed in (True, False):
                proof = dict(mapped_inode_verified=True, mapped_library_bytes_verified=False)
                # Python source/C builtin boundaries are explicit stand-ins; this
                # proves only that public binding consumes the mapping gate.
                with patch.object(call, '_verify_function', side_effect=lambda f, *a: f), \
                        patch.object(call.inspect, 'getmodule', return_value=module), \
                        patch.object(call.inspect, 'isbuiltin', return_value=True), \
                        patch.object(call, '_file_digest', return_value='b' * 64) as store_digest, \
                        patch.object(call, '_mapped_extension_identity', side_effect=(
                            ValueError('mapping identity differs') if failed else None), return_value=proof) as mapped:
                    if failed:
                        with self.assertRaisesRegex(ValueError, 'mapping identity differs'):
                            call._bound_runtime(native, path, hashlib.sha256(raw).hexdigest())
                        store_digest.assert_not_called()
                    else:
                        _, receipt = call._bound_runtime(native, path, hashlib.sha256(raw).hexdigest())
                        self.assertIs(receipt['checkpoint_mapping'], proof)
                        self.assertTrue(receipt['checkpoint_mapped_inode_verified'])
                        self.assertFalse(receipt['mapped_library_bytes_verified'])
                        self.assertFalse(receipt['live_store_process_verified'])
                        self.assertFalse(receipt['runtime_admitted'])
                    mapped.assert_called_once_with('checkpoint-library.so', 'a' * 64)

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
