"""Multi-handle Store producer candidate. No launcher or runtime admission."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import stat
import time
import types

from native_call import TORCH_SHA, CLIENT_SHA, _capsule_address, _store_directory, NativeCallFailure
from native_index import read_index
from record_allocations import plan_independent_records, restore_independent_records
from state_bindings import validate_state_bindings
from vllm_cpu_hook import _verify_function

PATCH_SHA = 'c057a1aa51f1c63be8671099d8a0a75da865d8fadffec667df4696fa4cf8287c'


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _bound_runtime(native_module, build_receipt, expected_build_sha256):
    """Bind local checkpoint binary and reviewed Store build receipt, not live server identity."""
    if not isinstance(expected_build_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_build_sha256):
        raise ValueError('external Store build receipt SHA required')
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(build_receipt, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular Store build receipt required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(65537)
    finally:
        os.close(fd)
    if len(raw) > 65536:
        raise ValueError('Store build receipt length exceeds 64 KiB')
    if hashlib.sha256(raw).hexdigest() != expected_build_sha256:
        raise ValueError('Store build receipt digest differs')
    build = json.loads(raw)
    if (build.get('compiled_native_store') is not True or build.get('model_source_sha256') != PATCH_SHA
            or build.get('schema') != 'kimi-independent-native-build-v1'):
        raise ValueError('compiled IPC-close patch receipt required')
    function = _verify_function(native_module.load_dict_non_blocking, TORCH_SHA)
    original = function.__globals__
    _verify_function(original['_get_uuid'], TORCH_SHA)
    client = original['SllmStoreClient']
    for name in ('__init__', 'load_into_cpu', 'load_into_gpu', 'confirm_model_loaded'):
        _verify_function(getattr(client, name), CLIENT_SHA, 'SllmStoreClient')
    module = inspect.getmodule(original['allocate_cuda_memory'])
    if module is None or not getattr(module, '__file__', None):
        raise ValueError('loaded native checkpoint extension required')
    for name in ('allocate_cuda_memory', 'get_cuda_memory_handles', 'get_device_uuid_map', 'restore_tensors'):
        if (not inspect.isbuiltin(original[name]) or inspect.getmodule(original[name]) is not module
                or getattr(module, name, None) is not original[name]):
            raise ValueError('native checkpoint functions do not belong to the same loaded extension')
    if _file_digest(module.__file__) != build.get('checkpoint_library_sha256'):
        raise ValueError('loaded native checkpoint binary differs')
    if _file_digest(build['store_library_path']) != build.get('store_library_sha256'):
        raise ValueError('Store library on disk differs from bound build')
    return original, dict(build_receipt_sha256=expected_build_sha256,
        checkpoint_module_origin_checked=True, checkpoint_library_on_disk_verified=True,
        store_library_on_disk_verified=True, mapped_library_bytes_verified=False,
        live_store_process_verified=False, native_loader_verified=False, runtime_admitted=False)


def _load_records_core(original, index, sidecar, *, model_path, server_address, device_id,
                        expected_device, max_allocation_bytes, timeout_seconds, deadline_monotonic=None):
    """Admitted caller supplies authenticated metadata and source/binary-bound functions.

    CPU tests call this core with explicit fake allocator/IPC/RPC boundaries. The
    public entry below never selects a CPU device or bypasses binary binding.
    """
    if (type(device_id) is not int or device_id not in range(8)
            or type(max_allocation_bytes) is not int or max_allocation_bytes <= 0
            or type(timeout_seconds) is not int or timeout_seconds <= 0):
        raise ValueError('explicit device, byte budget and finite timeout required')
    if (not isinstance(server_address, str) or not server_address.startswith('127.0.0.1:')
            or not server_address[10:].isdigit() or not 1 <= int(server_address[10:]) <= 65535):
        raise ValueError('owned loopback Store endpoint required')
    plan = plan_independent_records(index, sidecar['alias_of'])
    if not 0 < plan['payload_bytes'] <= max_allocation_bytes:
        raise ValueError('record allocations exceed admitted byte budget')
    begin = time.monotonic()
    deadline = begin + timeout_seconds if deadline_monotonic is None else deadline_monotonic
    receipt = dict(stage='prepared', model_path=model_path, layout=plan['layout'],
        allocation_attempts=[], allocated_bytes=0, replica_uuid=None,
        confirmation_received=False, store_request_attempted=False,
        store_cleanup_required=False, worker_cleanup_required=False, cleanup_verified=False,
        channel_close_attempted=False, channel_close_returned=False,
        load_dict_entry_used=False, native_loader_verified=False, runtime_admitted=False)
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('multi-handle call deadline reached')
        return value
    client, state, failure = None, {}, None
    pointers, handles, address_spans = {}, [], []
    try:
        remaining()
        client = original['SllmStoreClient'](server_address)
        stub = client.stub
        def bounded(method, load=False):
            def call(request):
                budget = remaining()
                if load:
                    receipt['store_request_attempted'] = receipt['store_cleanup_required'] = True
                return method(request, timeout=budget)
            return call
        client.stub = types.SimpleNamespace(LoadModelAsync=bounded(stub.LoadModelAsync, True),
                                            ConfirmModel=bounded(stub.ConfirmModel))
        receipt['stage'] = 'cpu_request'
        response = client.load_into_cpu(model_path)
        if getattr(response, 'model_path', None) != model_path:
            raise ValueError('Store host load response differs')
        receipt['stage'] = 'allocating_records'
        for record in plan['records']:
            if not record['bytes']:
                continue
            remaining()
            root, size = record['root'], record['bytes']
            attempt = dict(root=root, bytes=size, address=None, handle_idx=record['handle_idx'])
            receipt['allocation_attempts'].append(attempt)
            receipt['worker_cleanup_required'] = True
            allocated = original['allocate_cuda_memory']({device_id: size})
            if not isinstance(allocated, dict) or set(allocated) != {device_id}:
                raise ValueError('per-record native pointer result differs')
            token = allocated[device_id]
            address = _capsule_address(token)
            if address % 8 or any(address < end and start < address + size for start, end in address_spans):
                raise ValueError('record allocation address misaligned or overlapping')
            address_spans.append((address, address + size))
            pointers[root] = token
            attempt['address'] = address
            receipt['allocated_bytes'] += size
            handle_map = original['get_cuda_memory_handles']({device_id: token})
            handle = handle_map.get(device_id) if isinstance(handle_map, dict) else None
            if not isinstance(handle, bytes) or len(handle) != 64:
                raise ValueError('expected native cudaIpcMemHandle_t bytes')
            handles.append(handle)
        remaining()
        uuid_map = original['get_device_uuid_map']()
        gpu_uuid = uuid_map.get(device_id)
        if not isinstance(gpu_uuid, str) or not gpu_uuid:
            raise ValueError('device UUID missing')
        receipt['replica_uuid'] = original['_get_uuid']()
        receipt['stage'] = 'gpu_request'
        response = client.load_into_gpu(model_path, receipt['replica_uuid'],
            {gpu_uuid: plan['copy_chunks']}, {gpu_uuid: handles})
        if getattr(response, 'model_path', None) != model_path:
            raise ValueError('Store GPU load response differs')
        receipt['stage'] = 'confirming'
        protobuf = type(client).confirm_model_loaded.__globals__['storage_pb2']
        response = client.stub.ConfirmModel(protobuf.ConfirmModelRequest(model_path=model_path,
            replica_uuid=receipt['replica_uuid'], target_device_type=protobuf.DeviceType.DEVICE_TYPE_GPU))
        if getattr(response, 'model_path', None) != model_path:
            raise ValueError('Store completion response differs')
        receipt['confirmation_received'] = True
        receipt['stage'] = 'restoring_records'
        remaining()
        import torch
        state = restore_independent_records(index, plan, pointers, device_id=device_id,
            expected_device=expected_device, transfer_confirmed=True,
            restore_tensors=original['restore_tensors'], make_empty=lambda shape, dtype, device:
                torch.empty(shape, dtype=getattr(torch, dtype.split('.')[1]), device=device))
        remaining()
        receipt['allocation'] = dict(device=str(expected_device), alias_of=dict(plan['alias_of']))
    except BaseException as error:
        failure = error
    finally:
        if client is not None:
            if failure is None:
                receipt['stage'] = 'closing_channel'
            close_started = time.monotonic()
            receipt['channel_close_attempted'] = True
            try:
                client.channel.close()
                receipt['channel_close_returned'] = True
            except BaseException as error:
                receipt.setdefault('channel_close_errors', []).append(type(error).__name__)
                receipt['worker_cleanup_required'] = True
                if failure is None:
                    failure = error
            receipt['channel_close_elapsed_seconds'] = time.monotonic()-close_started
    finished = time.monotonic()
    receipt['elapsed_seconds'] = finished-begin
    receipt['deadline_exceeded'] = finished >= deadline
    if failure is None and receipt['deadline_exceeded']:
        failure = TimeoutError('multi-handle call deadline reached during channel close')
    if failure is not None:
        receipt['error_type'] = type(failure).__name__
        raise NativeCallFailure(receipt, state) from failure
    receipt['stage'] = 'returned'
    return state, receipt


def load_native_records(native_module, *, storage_path, model_path, expected_sidecar_sha256,
                         rank, device_id, max_allocation_bytes, timeout_seconds, server_address,
                         build_receipt, expected_build_sha256):
    """Component entry; parent must separately verify live Store identity and admission."""
    if (type(rank) is not int or rank not in range(8) or type(device_id) is not int or device_id not in range(8)
            or type(max_allocation_bytes) is not int or max_allocation_bytes <= 0
            or type(timeout_seconds) is not int or timeout_seconds <= 0):
        raise ValueError('explicit TP8 identity and finite allocation/time budgets required')
    started = time.monotonic()
    deadline = started + timeout_seconds
    _, directory = _store_directory(storage_path, model_path)
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest differs')
    sidecar = json.loads(raw)
    if sidecar['rank'] != rank or sidecar['state'] != 'before_quantization_postprocessing':
        raise ValueError('source rank or phase differs')
    original, binding = _bound_runtime(native_module, build_receipt, expected_build_sha256)
    index = read_index(directory, expected_sidecar_sha256)
    if validate_state_bindings(sidecar.get('state_bindings'), index, sidecar.get('buffer_encodings', {})) is None:
        raise ValueError('complete state roles required')
    preflight = time.monotonic() - started
    if preflight >= timeout_seconds:
        raise TimeoutError('record preflight consumed complete call budget')
    try:
        result, receipt = _load_records_core(original, index, sidecar, model_path=model_path,
            server_address=server_address, device_id=device_id, expected_device='cuda:' + str(device_id),
            max_allocation_bytes=max_allocation_bytes, timeout_seconds=timeout_seconds, deadline_monotonic=deadline)
    except NativeCallFailure as error:
        error.receipt.update(preflight_seconds=preflight, total_elapsed_seconds=time.monotonic() - started,
                             binding=binding)
        raise
    receipt['binding'] = binding
    receipt.update(preflight_seconds=preflight, total_elapsed_seconds=time.monotonic() - started)
    if receipt['total_elapsed_seconds'] >= timeout_seconds:
        receipt.update(stage='return_boundary', error_type='TimeoutError', deadline_exceeded=True)
        raise NativeCallFailure(receipt, result) from TimeoutError('native record public return deadline reached')
    return result, receipt
