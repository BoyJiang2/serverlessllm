# ---------------------------------------------------------------------------- #
#  ServerlessLLM                                                               #
#  Copyright (c) ServerlessLLM Team 2024                                       #
#                                                                              #
#  Licensed under the Apache License, Version 2.0 (the "License");             #
#  you may not use this file except in compliance with the License.            #
#                                                                              #
#  You may obtain a copy of the License at                                     #
#                                                                              #
#                  http://www.apache.org/licenses/LICENSE-2.0                  #
#                                                                              #
#  Unless required by applicable law or agreed to in writing, software         #
#  distributed under the License is distributed on an "AS IS" BASIS,           #
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.    #
#  See the License for the specific language governing permissions and         #
#  limitations under the License.                                              #
# ---------------------------------------------------------------------------- #
import json
import os
import time
import uuid
from typing import Dict, Optional, Union

import torch

# from accelerate.hooks import add_hook_to_module
from sllm_store._C import (
    allocate_cuda_memory,
    get_cuda_memory_handles,
    get_device_uuid_map,
    restore_tensors,
    save_tensors,
)
from sllm_store.client import SllmStoreClient
from sllm_store.device_map_utils import _expand_tensor_name
from sllm_store.logger import init_logger
from sllm_store.utils import (
    calculate_device_memory,
    calculate_tensor_device_offsets,
)

logger = init_logger(__name__)
_owned_load_quarantine = {}


def _get_uuid():
    return str(uuid.uuid4())


def save_dict(
    state_dict: Dict[str, torch.Tensor], model_path: Union[str, os.PathLike]
):
    tensor_names = list(state_dict.keys())
    tensor_data_index = {}
    for name, param in state_dict.items():
        param_storage = param.untyped_storage()
        data_ptr = param_storage.data_ptr()
        size = param_storage.size()
        tensor_data_index[name] = (data_ptr, size)

    if not os.path.exists(model_path):
        os.makedirs(model_path, exist_ok=True)

    # save tensors
    tensor_offsets = save_tensors(tensor_names, tensor_data_index, model_path)

    # create tensor index
    tensor_index = {}
    for name, param in state_dict.items():
        # name: offset, size
        tensor_index[name] = (
            tensor_offsets[name],
            tensor_data_index[name][1],
            tuple(param.shape),
            tuple(param.stride()),
            str(param.dtype),
        )

    # save tensor index
    with open(os.path.join(model_path, "tensor_index.json"), "w") as f:
        json.dump(tensor_index, f)


def load_dict(
    model_path: Union[str, os.PathLike],
    device_map: Dict[str, int],
    storage_path: Optional[str] = None,
):
    _, state_dict = load_dict_non_blocking(
        model_path, device_map, storage_path, _confirm_before_restore=True
    )

    return state_dict


def load_dict_owned(model_path, device_map, storage_path=None):
    """Blocking V4 path: adopt allocations before exposing their IPC handles."""
    from sllm_store import _C

    if getattr(_C, "v4_raw_restore_contract", None) != 1:
        raise RuntimeError("Selected Store extension lacks the V4 owned-restore contract")
    _, state_dict = load_dict_non_blocking(
        model_path, device_map, storage_path,
        _confirm_before_restore=True, _own_before_dispatch=True,
    )
    return state_dict


def owned_load_quarantine():
    """Unknown in-flight copies require process isolation, not eager cudaFree."""
    return tuple({"replica_uuid": replica, "model_path": value[0]}
                 for replica, value in _owned_load_quarantine.items())


def _finish_owned_load(client, model_path, replica_uuid, tensor_meta_index,
                       cuda_memory_ptrs, tensor_device_offsets, tensor_copy_chunks):
    # RestoreTensors adopts all allocations; constructing wrappers does not read data.
    state_dict = restore_tensors(tensor_meta_index, cuda_memory_ptrs, tensor_device_offsets)
    dispatched = False
    try:
        handles = get_cuda_memory_handles(cuda_memory_ptrs)
        devices = get_device_uuid_map()
        dispatched = True
        ret = client.load_into_gpu(
            model_path, replica_uuid,
            {devices[device]: chunks for device, chunks in tensor_copy_chunks.items()},
            {devices[device]: [handle] for device, handle in handles.items()},
        )
        if not ret:
            raise RuntimeError(f"Failed to dispatch owned Store load: {model_path}")
        if client.confirm_model_loaded(model_path, replica_uuid) is False:
            raise RuntimeError(f"Failed to confirm owned Store load: {model_path}")
    except BaseException:
        if dispatched:
            # RPC failure cannot establish that the server has closed every IPC handle.
            _owned_load_quarantine[replica_uuid] = (model_path, state_dict)
        raise
    return replica_uuid, state_dict


def load_dict_non_blocking(
    model_path: Optional[Union[str, os.PathLike]],
    device_map: Dict[str, int],
    storage_path: Optional[str] = None,
    *,
    _confirm_before_restore: bool = False,
    _own_before_dispatch: bool = False,
):
    if _own_before_dispatch and not _confirm_before_restore:
        raise ValueError("Owned Store loads require blocking confirmation")
    client = SllmStoreClient("127.0.0.1:8073")
    ret = client.load_into_cpu(model_path)
    if not ret:
        raise ValueError(f"Failed to load model {model_path} into CPU")

    if not storage_path:
        storage_path = os.getenv("STORAGE_PATH", os.path.expanduser("~/models"))
    with open(
        os.path.join(storage_path, model_path, "tensor_index.json"), "r"
    ) as f:
        tensor_index = json.load(f)

    tensor_meta_index = {}
    tensor_data_index = {}
    for name, (offset, size, shape, stride, dtype) in tensor_index.items():
        tensor_meta_index[name] = (shape, stride, dtype)
        tensor_data_index[name] = (offset, size)

    start = time.time()
    expanded_device_map = _expand_tensor_name(
        device_map, list(tensor_index.keys())
    )
    # Native TMA consumers require aligned tensor starts inside the allocation.
    alignment = 256 if _own_before_dispatch else 1
    device_memory = calculate_device_memory(
        expanded_device_map, tensor_data_index, alignment=alignment
    )
    # logger.debug(f"calculate_device_memory {device_memory}")
    if _own_before_dispatch:
        replica_uuid = _get_uuid()
        tensor_device_offsets, tensor_copy_chunks = calculate_tensor_device_offsets(
            expanded_device_map, tensor_data_index, alignment=alignment
        )
    cuda_memory_ptrs = allocate_cuda_memory(device_memory)
    if _own_before_dispatch:
        return _finish_owned_load(
            client, model_path, replica_uuid, tensor_meta_index,
            cuda_memory_ptrs, tensor_device_offsets, tensor_copy_chunks,
        )
    # cuda_memory_ptrs = { k: [v] for k,v in cuda_memory_ptrs.items()}
    cuda_memory_handles = get_cuda_memory_handles(cuda_memory_ptrs)
    device_uuid_map = get_device_uuid_map()
    # logger.debug(f"determine device_uuid_map {device_uuid_map}")
    tensor_device_offsets, tensor_copy_chunks = calculate_tensor_device_offsets(
        expanded_device_map, tensor_data_index
    )
    logger.debug(f"allocate_cuda_memory takes {time.time() - start} seconds")

    replica_uuid = _get_uuid()
    ret = client.load_into_gpu(
        model_path,
        replica_uuid,
        {
            device_uuid_map[device_id]: v
            for device_id, v in tensor_copy_chunks.items()
        },
        {
            device_uuid_map[device_id]: [v]
            for device_id, v in cuda_memory_handles.items()
        },
    )
    if not ret:
        raise ValueError(f"Failed to load model {model_path} into GPU")

    if _confirm_before_restore:
        # Blocking callers establish copy completion and IPC close before ownership.
        client.confirm_model_loaded(model_path, replica_uuid)

    # load model state_dict
    start = time.time()
    state_dict = restore_tensors(
        tensor_meta_index, cuda_memory_ptrs, tensor_device_offsets
    )
    logger.info(f"restore state_dict takes {time.time() - start} seconds")

    return replica_uuid, state_dict
