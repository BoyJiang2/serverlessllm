"""Isolated, source-bound native call recorder. No imports of native/CUDA code.

Real execution requires a separately admitted worker with an independent guard.
Failure requires worker/context disposal; this module cannot prove raw CUDA or
Store cleanup and never frees allocations while DMA completion is uncertain.
"""
import hashlib
import ctypes
import io
import json
from pathlib import Path, PurePosixPath
import time
import types

from native_index import read_index
from state_bindings import validate_state_bindings
from vllm_cpu_hook import _verify_function

TORCH_SHA = 'a4fb54b68a3895bf6015cbefe3bc7a7fd51e0af4f37cda72d1b49c9fabce29e9'
UTILS_SHA = 'c21d9a8e30121c17ddd96bbcf6a5f6be59f3208a75e86a430194eee611276217'
MAP_SHA = 'd15e8888b8c04230f958d71c2f731acdaed97ea4d78dc1a2c7b0f4ec7238c4de'
CLIENT_SHA = 'e33fb8ca82969dada13ea4e2905530ec6e9741f83dfc6fc33ca067d750ab04c4'


def _capsule_address(token):
    # The pinned pybind11 void* caster returns PyCapsule, not a Python integer.
    get_name = ctypes.pythonapi.PyCapsule_GetName
    get_name.argtypes, get_name.restype = [ctypes.py_object], ctypes.c_char_p
    get_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    get_pointer.argtypes, get_pointer.restype = [ctypes.py_object, ctypes.c_char_p], ctypes.c_void_p
    try:
        address = get_pointer(token, get_name(token))
    except (ValueError, TypeError) as error:
        raise ValueError('expected native void pointer capsule') from error
    if not address:
        raise ValueError('null native allocation')
    return address


class NativeCallFailure(RuntimeError):
    def __init__(self, receipt, state):
        super().__init__('native call failed at ' + receipt['stage'] + '; worker cleanup required')
        self.receipt = receipt
        self.native_state = state  # Retain any constructed owners until controlled disposal.


def _store_directory(storage_path, model_path):
    if not isinstance(model_path, str) or not model_path:
        raise ValueError('relative Store identity required')
    relative = PurePosixPath(model_path)
    if relative.is_absolute() or '..' in relative.parts or '\\' in model_path or ':' in model_path:
        raise ValueError('relative Store identity required')
    root = Path(storage_path).resolve(strict=True)
    directory = (root / model_path).resolve(strict=True)
    if not directory.is_relative_to(root) or directory == root:
        raise ValueError('Store identity escapes storage root')
    return root, directory


def load_native_return(native_module, *, storage_path, model_path, expected_sidecar_sha256,
                       rank, device_id, max_allocation_bytes, timeout_seconds):
    """Run the pinned nonblocking function in a private globals dictionary.

    Captured base/size/offsets belong to this call, not inferred from pointers.
    Blocking RPCs receive remaining deadlines. Host guard is still required for
    filesystem work and native functions, which Python cannot cancel safely.
    """
    if (type(rank) is not int or rank not in range(8) or type(device_id) is not int
            or device_id not in range(8) or type(max_allocation_bytes) is not int
            or max_allocation_bytes <= 0 or type(timeout_seconds) is not int or timeout_seconds <= 0):
        raise ValueError('explicit TP8 rank, device, memory cap and finite time budget required')
    root, directory = _store_directory(storage_path, model_path)
    started = time.monotonic()
    deadline = started + timeout_seconds
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise TimeoutError('native call deadline reached')
        return value
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sidecar_sha256:
        raise ValueError('sidecar digest mismatch')
    sidecar = json.loads(raw)
    if sidecar['rank'] != rank or sidecar['state'] != 'before_quantization_postprocessing':
        raise ValueError('wrong source rank or phase')
    index = read_index(directory, expected_sidecar_sha256)
    if validate_state_bindings(sidecar.get('state_bindings'), index, sidecar.get('buffer_encodings', {})) is None:
        raise ValueError('complete state binding manifest required')
    index_raw = (directory / 'tensor_index.json').read_bytes()
    if hashlib.sha256(index_raw).hexdigest() != sidecar['native_index_sha256']:
        raise ValueError('index changed after preflight')
    function = _verify_function(native_module.load_dict_non_blocking, TORCH_SHA)
    original = function.__globals__
    for name in ('calculate_device_memory', 'calculate_tensor_device_offsets'):
        _verify_function(original[name], UTILS_SHA)
    _verify_function(original['_expand_tensor_name'], MAP_SHA)
    _verify_function(original['_get_uuid'], TORCH_SHA)
    client_class = original['SllmStoreClient']
    for name in ('__init__', 'load_into_cpu', 'load_into_gpu', 'confirm_model_loaded'):
        _verify_function(getattr(client_class, name), CLIENT_SHA, 'SllmStoreClient')
    plan_map = {name: device_id for name in index}
    data_index = {name: (row[0], row[1]) for name, row in index.items()}
    memory = original['calculate_device_memory'](plan_map, data_index)
    offsets, chunks = original['calculate_tensor_device_offsets'](plan_map, data_index)
    if (set(memory) != {device_id} or type(memory[device_id]) is not int
            or not 0 < memory[device_id] <= max_allocation_bytes):
        raise ValueError('native allocation exceeds admitted rank budget')
    for name, encoding in sidecar.get('buffer_encodings', {}).items():
        if offsets[device_id][name] % 8:
            raise ValueError('native compact layout misaligns complex buffer: ' + name)
    receipt = dict(stage='prepared', rank=rank, model_path=model_path, device_id=device_id,
                   allocated_bytes=0, base_address=None, replica_uuid=None,
                   confirmation_received=False, worker_cleanup_required=False,
                   store_request_attempted=False, store_cleanup_required=False,
                   cleanup_verified=False, native_loader_verified=False, runtime_admitted=False)
    clients, state, native_pointers = [], {}, {}
    expected_meta = {n: (row[2], row[3], row[4]) for n, row in index.items()}

    def make_client(*args, **kwargs):
        if clients:
            raise ValueError('unexpected additional native client')
        client = client_class(*args, **kwargs)
        clients.append(client)
        stub = client.stub
        def bounded_rpc(method, *, load=False):
            def call(request):
                timeout = remaining()
                if load:
                    receipt['store_request_attempted'] = True
                    receipt['store_cleanup_required'] = True
                return method(request, timeout=timeout)
            return call
        client.stub = types.SimpleNamespace(LoadModelAsync=bounded_rpc(stub.LoadModelAsync, load=True),
            ConfirmModel=bounded_rpc(stub.ConfirmModel))
        cpu_load = client.load_into_cpu
        def checked_cpu_load(path):
            response = cpu_load(path)
            if getattr(response, 'model_path', None) != model_path:
                raise ValueError('CPU load response identity mismatch')
            return response
        client.load_into_cpu = checked_cpu_load
        load = client.load_into_gpu
        def record_replica(path, replica, copy_chunks, handles):
            receipt['stage'] = 'gpu_request'
            receipt['replica_uuid'] = replica
            response = load(path, replica, copy_chunks, handles)
            if getattr(response, 'model_path', None) != model_path:
                raise ValueError('GPU load response identity mismatch')
            return response
        client.load_into_gpu = record_replica
        return client

    def allocate(requested):
        remaining()
        if requested != memory or receipt['base_address'] is not None:
            raise ValueError('native allocation plan changed')
        receipt['stage'] = 'allocating'
        receipt['worker_cleanup_required'] = True
        pointers = original['allocate_cuda_memory'](requested)
        if not isinstance(pointers, dict) or set(pointers) != {device_id}:
            raise ValueError('native allocation did not return a valid address')
        receipt['base_address'] = _capsule_address(pointers[device_id])
        native_pointers.update(pointers)
        receipt['allocated_bytes'] = memory[device_id]
        return pointers

    def restore(meta, pointers, actual_offsets):
        nonlocal state
        if (meta != expected_meta or actual_offsets != offsets
                or pointers != native_pointers or len(clients) != 1):
            raise ValueError('native restore arguments differ from captured allocation')
        receipt['stage'] = 'confirming'
        protobuf = client_class.confirm_model_loaded.__globals__['storage_pb2']
        request = protobuf.ConfirmModelRequest(model_path=model_path,
            replica_uuid=receipt['replica_uuid'], target_device_type=protobuf.DeviceType.DEVICE_TYPE_GPU)
        response = clients[0].stub.ConfirmModel(request)
        if getattr(response, 'model_path', None) != model_path:
            raise ValueError('native completion response identity mismatch')
        receipt['confirmation_received'] = True
        receipt['stage'] = 'restoring_wrappers'
        remaining()
        state = original['restore_tensors'](meta, pointers, actual_offsets)
        return state

    def read_authenticated_index(path, mode='r'):
        if Path(path).resolve() != directory / 'tensor_index.json' or mode != 'r':
            raise ValueError('unexpected native metadata access')
        return io.StringIO(index_raw.decode('utf-8'))

    namespace = dict(original, SllmStoreClient=make_client, allocate_cuda_memory=allocate,
                     restore_tensors=restore, open=read_authenticated_index)
    private_call = types.FunctionType(function.__code__, namespace)
    try:
        receipt['stage'] = 'cpu_request'
        remaining()
        replica, returned = private_call(model_path, {'': device_id}, str(root))
        remaining()
        if (replica != receipt['replica_uuid'] or returned is not state
                or not receipt['confirmation_received'] or receipt['base_address'] is None):
            raise ValueError('incomplete native call')
        receipt['stage'] = 'returned'
        receipt['elapsed_seconds'] = time.monotonic() - started
        receipt['allocation'] = dict(device='cuda:' + str(device_id),
            base_address=receipt['base_address'], allocation_bytes=receipt['allocated_bytes'],
            device_offsets=dict(offsets[device_id]), alias_of=dict(sidecar.get('alias_of', {})))
        return state, receipt
    except BaseException as error:
        receipt['elapsed_seconds'] = time.monotonic() - started
        receipt['error_type'] = type(error).__name__
        raise NativeCallFailure(receipt, state) from error
    finally:
        for client in clients:
            try:
                client.channel.close()
            except BaseException as error:
                receipt.setdefault('channel_close_errors', []).append(type(error).__name__)
                receipt['worker_cleanup_required'] = True
