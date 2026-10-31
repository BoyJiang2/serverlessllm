"""Multi-handle Store producer candidate. No launcher or runtime admission."""
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

from native_call import TORCH_SHA, CLIENT_SHA, _capsule_address, _store_directory, NativeCallFailure
from native_index import read_index, read_metadata
from record_allocations import plan_independent_records, restore_independent_records
from state_bindings import validate_state_bindings
from vllm_cpu_hook import _verify_function

PATCH_SHA = 'c057a1aa51f1c63be8671099d8a0a75da865d8fadffec667df4696fa4cf8287c'


def _p0_tensors():
    """Forty deterministic CPU bytes, never checkpoint or dummy model weights."""
    import torch
    packed = torch.tensor([-1, -2147483648, 0x01234567, 0], dtype=torch.int32)
    return dict(packed=packed, packed_alias=packed,
        scale=torch.tensor([-32768, 32705, 0, 16256], dtype=torch.int16).view(torch.bfloat16),
        router=torch.tensor([-2147483648, 2143289345, 1065353216, 0], dtype=torch.int32).view(torch.float32),
        empty=torch.empty((2, 0), dtype=torch.int32))


def _p0_fingerprints(tensors, device):
    import torch
    from native_payload import iter_host_byte_blocks
    layouts = dict(packed=([4], torch.int32), scale=([4], torch.bfloat16),
                   router=([4], torch.float32), empty=([2, 0], torch.int32))
    if not isinstance(tensors, dict) or set(tensors) != set(layouts) | {'packed_alias'}:
        raise ValueError('P0 tensor coverage differs')
    result = {}
    for name, (shape, dtype) in layouts.items():
        tensor = tensors[name]
        if (not isinstance(tensor, torch.Tensor) or tensor.device != torch.device(device)
                or tensor.dtype != dtype or list(tensor.shape) != shape or not tensor.is_contiguous()):
            raise ValueError('P0 tensor layout differs: ' + name)
        result[name] = hashlib.sha256(b''.join(iter_host_byte_blocks(tensor, 64))).hexdigest()
    if tensors['packed_alias'] is not tensors['packed']:
        raise ValueError('P0 packed alias object differs')
    return result


def _p0_device(gpu_uuid):
    import torch
    if str(torch.__version__) != '2.10.0+cu129' or torch.version.cuda != '12.9':
        raise ValueError('P0 requires frozen CUDA PyTorch; no CPU fallback')
    if torch.cuda.device_count() != 1:
        raise ValueError('P0 requires exactly one explicitly exposed GPU')
    torch.cuda.set_device(0)
    actual = str(getattr(torch.cuda.get_device_properties(0), 'uuid', ''))
    if actual.removeprefix('GPU-').lower() != gpu_uuid.removeprefix('GPU-').lower():
        raise ValueError('P0 actual GPU UUID differs')
    return torch.device('cuda:0')


def _p0_ipc_child(connection, gpu_uuid, deadline):
    """Spawn target: receives CUDA tensors, sends only metadata, then exits."""
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError('P0 consumer entered after original deadline')
        device = _p0_device(gpu_uuid)
        if time.monotonic() >= deadline or not connection.poll(max(0, deadline-time.monotonic())):
            raise TimeoutError('P0 consumer receive deadline exhausted')
        message = connection.recv()
        tensors = message['tensors']
        before = _p0_fingerprints(tensors, device)
        if before != message['expected']:
            raise ValueError('P0 consumer raw bits differ')
        tensors['packed'][0] = 0x13579bdf
        after = _p0_fingerprints(tensors, device)  # Synchronous host copies complete the write.
        del tensors, message
        if time.monotonic() >= deadline:
            raise TimeoutError('P0 consumer return deadline exhausted')
        connection.send(dict(status='ok', before=before, after=after))
    except BaseException as error:
        connection.send(dict(status='failed', error_type=type(error).__name__, error=repr(error)[:1024]))
    finally:
        connection.close()


def run_cuda_ipc_component_probe(*, run_id, holder, gpu_uuid, deadline_monotonic, max_payload_bytes):
    """Internal P0a body only. An admitted guarded worker must call this candidate.

    No CLI, model, Store/native allocator, resource admission or release claim.
    Parent retains producers until its owned spawn child has actually exited.
    """
    if (not isinstance(run_id, str) or not re.fullmatch('kimi-p0-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', run_id)
            or not isinstance(holder, str) or not re.fullmatch('[0-9a-f]{32}', holder)
            or not isinstance(gpu_uuid, str) or not re.fullmatch('GPU-[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', gpu_uuid)
            or type(max_payload_bytes) is not int or not 40 <= max_payload_bytes <= 1024**2
            or type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)):
        raise ValueError('explicit P0 identity, GPU UUID, payload cap and original deadline required')
    started = time.monotonic()
    if not 3 < deadline_monotonic-started <= 300:
        raise ValueError('P0 budget must cover cleanup within at most 300 seconds')
    def remaining():
        value = deadline_monotonic-3-time.monotonic()
        if value <= 0:raise TimeoutError('P0 component compute deadline exhausted')
        return value
    receipt = dict(run_id=run_id, holder=holder, gpu_uuid=gpu_uuid, stage='checking_device',
        mode='KIMI-CUDA-IPC-P0A-UNREVIEWED', payload_bytes=40, max_payload_bytes=max_payload_bytes,
        allocator='torch', producer_pid=os.getpid(), consumer_pid=None, child_start_attempted=False,
        child_exitcode=None, cleanup_errors=[], torch_cuda_ipc_verified=False,
        native_loader_verified=False, store_created=False, full_model_verified=False,
        resources_released=False, runtime_admitted=False)
    gpu, process, parent, child, failure = {}, None, None, None, None
    try:
        remaining()
        device = _p0_device(gpu_uuid)
        remaining()
        cpu = _p0_tensors()
        expected = _p0_fingerprints(cpu, 'cpu')
        for name, tensor in cpu.items():
            if name != 'packed_alias':gpu[name] = tensor.to(device=device, copy=True)
        gpu['packed_alias'] = gpu['packed']
        receipt['stage'] = 'spawning_consumer'
        remaining()
        import torch.multiprocessing as multiprocessing
        context = multiprocessing.get_context('spawn')
        parent, child = context.Pipe(duplex=True)
        process = context.Process(target=_p0_ipc_child, args=(child, gpu_uuid, deadline_monotonic-3), daemon=True)
        receipt['child_start_attempted'] = True
        process.start()
        receipt['consumer_pid'] = process.pid
        child.close()
        remaining()
        parent.send(dict(tensors=gpu, expected=expected))
        receipt['stage'] = 'waiting_consumer'
        if not parent.poll(remaining()):raise TimeoutError('P0 consumer response timed out')
        response = parent.recv()
        remaining()
        if not isinstance(response, dict) or response.get('status') != 'ok' or response.get('before') != expected:
            raise ValueError('P0 consumer did not confirm original raw bits')
        process.join(remaining())
        remaining()
        if process.is_alive() or process.exitcode != 0:
            raise RuntimeError('P0 owned consumer did not exit successfully')
        cpu['packed'][0] = 0x13579bdf
        changed = _p0_fingerprints(cpu, 'cpu')
        observed = _p0_fingerprints(gpu, device)
        remaining()
        if response.get('after') != changed or observed != changed:
            raise ValueError('P0 consumer mutation not visible in producer CUDA storage')
        receipt.update(before=expected, after=observed, torch_cuda_ipc_verified=True)
    except BaseException as error:
        failure = error
    finally:
        for connection in (parent, child):
            if connection is not None:
                try:connection.close()
                except BaseException as error:
                    receipt['cleanup_errors'].append(type(error).__name__)
                    if failure is None:failure = error
        if process is not None and process.pid is not None:
            try:
                for stop in (process.terminate, process.kill):
                    if not process.is_alive():break
                    stop()
                    process.join(max(0, min(1, deadline_monotonic-time.monotonic())))
                receipt['child_exitcode'] = process.exitcode
                if process.is_alive():raise RuntimeError('P0 consumer remains; host guard disposal required')
                process.close()
            except BaseException as error:
                receipt['cleanup_errors'].append(type(error).__name__)
                if failure is None:failure = error
    receipt['elapsed_seconds'] = time.monotonic()-started
    if failure is None and time.monotonic() >= deadline_monotonic:
        failure = TimeoutError('P0 complete worker deadline exceeded')
    if failure is not None:
        receipt['error_type'] = type(failure).__name__
        failure.p0_receipt, failure.p0_owners = receipt, gpu
        raise failure
    receipt['stage'] = 'returned'
    return receipt


def _file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _mapped_extension_identity(path, expected_sha256):
    """Point-in-time inode binding, not a hash of executable memory or function addresses."""
    if sys.platform != 'linux':
        raise ValueError('Linux self mappings required for native extension identity')
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('external native extension SHA required')
    source = Path(path).resolve(strict=True)
    fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 2 * 1024**3:
            raise ValueError('bounded regular native extension required')
        digest, count = hashlib.sha256(), 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            while block := stream.read(1024**2):
                count += len(block)
                if count > 2 * 1024**3:
                    raise ValueError('native extension grew beyond byte bound')
                digest.update(block)
                del block
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        identity = tuple(getattr(before, k) for k in fields)
        if (tuple(getattr(os.fstat(fd), k) for k in fields) != identity
                or tuple(getattr(source.stat(), k) for k in fields) != identity
                or count != before.st_size or digest.hexdigest() != expected_sha256):
            raise ValueError('native extension file changed or SHA differs')
        with Path('/proc/self/maps').open('rb') as stream:
            raw = stream.read(4 * 1024**2 + 1)
        if len(raw) > 4 * 1024**2:
            raise ValueError('self mapping inventory exceeds byte bound')
        segments = 0
        for row in raw.splitlines():
            parts = row.split(None, 5)
            if len(parts) < 5:
                raise ValueError('malformed self mapping inventory')
            major, minor = (int(n, 16) for n in parts[3].split(b':'))
            if ((major, minor, int(parts[4])) == (os.major(before.st_dev), os.minor(before.st_dev), before.st_ino)
                    and b'x' in parts[1]):
                segments += 1
        if not segments:
            raise ValueError('native extension file inode has no executable self mapping')
        if tuple(getattr(source.stat(), k) for k in fields) != identity:
            raise ValueError('native extension file changed after mapping check')
        return dict(pid=os.getpid(), device_major=os.major(before.st_dev), device_minor=os.minor(before.st_dev),
            inode=before.st_ino, file_bytes=count, file_sha256=expected_sha256,
            executable_segments=segments, maps_sha256=hashlib.sha256(raw).hexdigest(),
            mapped_inode_verified=True, mapped_library_bytes_verified=False, function_address_verified=False)
    finally:
        os.close(fd)


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
    mapping = _mapped_extension_identity(module.__file__, build.get('checkpoint_library_sha256'))
    if _file_digest(build['store_library_path']) != build.get('store_library_sha256'):
        raise ValueError('Store library on disk differs from bound build')
    return original, dict(build_receipt_sha256=expected_build_sha256,
        checkpoint_module_origin_checked=True, checkpoint_library_on_disk_verified=True,
        checkpoint_mapping=mapping, checkpoint_mapped_inode_verified=True,
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
    raw = read_metadata(directory, 'kimi_raw_state.json')
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


def p0_main(argv):
    """Internal component worker; host launch/guard/resource admission is separate."""
    import argparse
    from datetime import datetime, timezone
    import sys
    from source_bundle import verify_p0_worker_input
    from source_schedule import _utc
    if sys.flags.optimize or not sys.dont_write_bytecode:
        raise ValueError('P0 worker requires unoptimized python -B')
    parser = argparse.ArgumentParser(description='Bounded Torch IPC P0a worker, not model/Store admission')
    parser.add_argument('--worker-input', required=True)
    parser.add_argument('--worker-input-sha', required=True)
    args = parser.parse_args(argv)
    entered, started = datetime.now(timezone.utc), time.monotonic()
    _, root, worker_input, receipt, schedule, identity = verify_p0_worker_input(
        args.worker_input, args.worker_input_sha)
    if worker_input['schema'] != 'kimi-p0-worker-input-v1':
        raise ValueError('P0 worker cannot use source-model registration')
    if Path(__file__).resolve(strict=True) != root/'code/native_record_call.py':
        raise ValueError('must execute the verified P0 worker file')
    now = datetime.now(timezone.utc)
    if now < entered or entered < _utc(worker_input['batch_entered_utc']):
        raise TimeoutError('P0 worker clock regressed or batch has not started')
    # Verification consumes this same deadline; late startup never renews the batch.
    deadline = started + min(receipt['component_max_seconds'],
        (_utc(schedule['guard_fire_by_utc']) - entered).total_seconds())
    if time.monotonic()+3 >= deadline or now >= _utc(schedule['guard_fire_by_utc']):
        raise TimeoutError('P0 original component budget exhausted')
    path = root/'p0-component-events.jsonl'
    with path.open('xb', buffering=0) as events:
        def record(stage, details):
            raw = (json.dumps(dict(identity, stage=stage, details=details,
                schema='kimi-p0-component-events-v1', runtime_admitted=False,
                observed_utc=datetime.now(timezone.utc).isoformat()), sort_keys=True)+'\n').encode()
            if len(raw) > 16384:
                raise ValueError('bounded P0 component evidence required')
            if events.write(raw) != len(raw):
                raise OSError('incomplete P0 event write')
            os.fsync(events.fileno())
        record('inputs_verified', dict(worker_input_sha256=args.worker_input_sha,
            plan_sha256=worker_input['plan_sha256'], gpu_uuid=receipt['gpu_uuid'],
            image_id=receipt['image_id'], original_guard_fire_by_utc=schedule['guard_fire_by_utc'],
            guard_verified=False))
        try:
            result = run_cuda_ipc_component_probe(run_id=receipt['run_id'], holder=receipt['holder'],
                gpu_uuid=receipt['gpu_uuid'], deadline_monotonic=deadline,
                max_payload_bytes=receipt['max_payload_bytes'])
            finished = datetime.now(timezone.utc)
            if (time.monotonic() >= deadline or finished < entered
                    or finished >= _utc(schedule['guard_fire_by_utc'])):
                error = TimeoutError('P0 worker return deadline exhausted or clock regressed')
                error.p0_receipt = result
                raise error
        except BaseException as error:
            try:
                record('failed', dict(error_type=type(error).__name__, error=str(error)[:1024],
                    component=getattr(error, 'p0_receipt', None), resources_released=False))
            except BaseException as evidence_error:
                error.p0_evidence_error = dict(error_type=type(evidence_error).__name__,
                                               error=str(evidence_error)[:1024])
            raise
        try:
            record('component_returned', dict(component=result, resources_released=False))
        except BaseException as error:
            error.p0_receipt = result
            raise
    finished = datetime.now(timezone.utc)
    if (time.monotonic() >= deadline or finished < entered
            or finished >= _utc(schedule['guard_fire_by_utc'])):
        error = TimeoutError('P0 evidence consumed return budget or clock regressed')
        error.p0_receipt = result
        raise error
    print(json.dumps(dict(result, code_commit=receipt['code_commit'], spec_sha256=receipt['spec_sha256'],
        source_manifest_sha256=receipt['manifest_sha256'], runtime_admitted=False,
        guard_verified=False, resources_released=False), sort_keys=True))


if __name__ == '__main__':
    import sys
    if sys.argv[1:2] != ['p0a']:
        raise SystemExit('only internal p0a worker entry is available; host admission required')
    p0_main(sys.argv[2:])
