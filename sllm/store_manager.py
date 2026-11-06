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

import asyncio
import copy
import os
import time
from typing import List, Mapping, Optional

import ray

from sllm.hardware_info_collector import collect_all_info
from sllm.logger import init_logger
from sllm.model_downloader import (
    VllmModelDownloader,
    download_lora_adapter,
    download_transformers_model,
)
from sllm.utils import get_worker_nodes
from sllm_store.client import SllmStoreClient

logger = init_logger(__name__)


@ray.remote(num_cpus=1)
def inspect_v4_grouped_store(backend_config):
    from sllm_store.v4_service import grouped_registration_plan

    return grouped_registration_plan(backend_config, os.environ["STORAGE_PATH"])


class SllmLocalStore:
    def __init__(
        self,
        node_id: str,
        client: SllmStoreClient,
        mem_pool_size: int,
        chunk_size: int,
        hardware_info: Mapping,
    ):
        self.node_id = node_id
        self.client = client
        self.hardware_info = hardware_info
        self.disk_models = {}
        self.queued_models = {}
        self.grouped_models = {}
        self.grouped_chunks = {}
        self.grouped_host_attempts = {}
        self.grouped_host_waiters = {}
        self.failed_grouped_registrations = {}

        self.pinned_memory_pool = {}
        self.chunk_size = chunk_size
        self.pinned_memory_pool_chunks = mem_pool_size // chunk_size
        self.pinned_memory_pool_usage = 0

        self.io_queue = []
        self.lock = asyncio.Lock()

        # Start loading loop
        self.loader = asyncio.create_task(self.loading_loop())

        logger.info(
            f"Initialized local store for node {self.node_id}"
            f" with {self.pinned_memory_pool_chunks} chunks"
            f" (chunk size: {chunk_size})"
        )

    async def register_model(
        self, model_name: str, backend: str, backend_config
    ):
        async with self.lock:
            if (backend == "vllm"
                    and backend_config.get("load_format") == "sllm_v4_grouped_store"):
                return await self._register_grouped_model(model_name, backend_config)
            if model_name in self.disk_models:
                logger.error(f"{model_name} already registered")
                return
            model_path = self._get_model_path(model_name, backend)
            if backend == "transformers":
                model_size = self.client.register_model(model_path)
                self.disk_models[model_name] = ([model_path], model_size)
            elif backend == "vllm":
                tensor_parallel_size = backend_config.get(
                    "tensor_parallel_size", 1
                )
                model_size = 0
                model_path_list = []
                for rank in range(tensor_parallel_size):
                    model_rank_path = os.path.join(model_path, f"rank_{rank}")
                    model_size += self.client.register_model(model_rank_path)
                    model_path_list.append(model_rank_path)
                self.disk_models[model_name] = (model_path_list, model_size)
            logger.info(f"{model_name} registered, {self.disk_models}")

        return model_size

    async def _register_grouped_model(self, model_name, backend_config):
        if model_name in self.disk_models:
            if self.grouped_models.get(model_name) != backend_config.get("model_loader_extra_config"):
                raise ValueError("Registered grouped model identity/path changed")
            return self.disk_models[model_name][1]
        if model_name in self.failed_grouped_registrations:
            raise RuntimeError("Previous grouped registration failed; isolate the Store before retry")
        plan = await inspect_v4_grouped_store.options(
            resources={f"worker_id_{self.node_id}": 0.01}
        ).remote(backend_config)
        if (len(plan) != 496 or len({path for path, _ in plan}) != 496
                or any(type(size) is not int or size <= 0 for _, size in plan)):
            raise ValueError("Worker returned an incomplete grouped registration plan")
        paths = {path for path, _ in plan}
        if any(a["inflight"] in paths or paths.intersection(a["registered"])
               for a in self.failed_grouped_registrations.values()):
            raise RuntimeError("Previous grouped dataset registration failed; isolate the Store before retry")
        attempt = {"registered": [], "inflight": None, "failed": False}
        self.failed_grouped_registrations[model_name] = attempt
        model_size = 0
        try:
            for path, expected_size in plan:
                attempt["inflight"] = path
                size = await asyncio.to_thread(self.client.register_model, path)
                if type(size) is not int or size != expected_size:
                    raise RuntimeError(f"Grouped Store registration size mismatch: {path}")
                attempt["registered"].append(path)
                attempt["inflight"] = None
                model_size += size
            self.disk_models[model_name] = (list(attempt["registered"]), model_size)
            self.grouped_models[model_name] = copy.deepcopy(backend_config["model_loader_extra_config"])
            self.grouped_chunks[model_name] = sum(
                (size + self.chunk_size - 1) // self.chunk_size for _, size in plan
            )
            self.failed_grouped_registrations.pop(model_name)
        except BaseException:
            # The native Store has no unregister transaction. Keep partial state,
            # including a possibly still-running cancelled RPC, out of admission.
            attempt["failed"] = True
            raise
        return model_size

    async def get_store_info(self):
        async with self.lock:
            delta_time = 0
            if len(self.io_queue) > 0:
                delta_time = self.io_queue[-1]["estimated_time"] - time.time()
                if delta_time < 0:
                    delta_time = 0
            return [self.disk_models, self.pinned_memory_pool, delta_time]

    async def load_to_host(self, model_name: str) -> bool:
        if model_name in self.grouped_models:
            return await self._queue_grouped_host_load(model_name)
        async with self.lock:
            if model_name not in self.disk_models:
                logger.error(f"{model_name} not found on node {self.node_id}")
                return False
            if model_name in self.pinned_memory_pool:
                logger.info(
                    f"{model_name} already loaded to node {self.node_id}"
                )
                return True
            elif model_name in self.queued_models:
                logger.info(
                    f"{model_name} is being loaded to node {self.node_id}"
                )
                return True

            _, model_size = self.disk_models[model_name]
            start_time = time.time()
            if len(self.io_queue) > 0:
                start_time = max(
                    self.io_queue[-1]["estimated_time"], start_time
                )
            disk_bandwidth = self.hardware_info.get("disk_bandwidth", 1)
            estimated_completion_time = start_time + model_size / disk_bandwidth
            self.io_queue.append(
                {
                    "model_name": model_name,
                    "estimated_time": estimated_completion_time,
                }
            )
            self.queued_models[model_name] = True
            logger.info(
                f"{model_name} is being loaded to node {self.node_id},"
                " estimated completion time: "
                f"{self._format_time(estimated_completion_time)}"
            )
            return True

    async def _queue_grouped_host_load(self, model_name):
        async with self.lock:
            attempt = self.grouped_host_attempts.get(model_name)
            if attempt and attempt["failed"]:
                raise RuntimeError("Grouped host state failed; isolate the Store before retry")
            if self.grouped_chunks[model_name] > self.pinned_memory_pool_chunks:
                raise MemoryError("Complete grouped Store exceeds the pinned pool after per-group rounding")
            already_loaded = model_name in self.pinned_memory_pool
            if model_name not in self.grouped_host_waiters:
                self.grouped_host_waiters[model_name] = asyncio.get_running_loop().create_future()
                self.queued_models[model_name] = True
                start = max(time.time(), self.io_queue[-1]["estimated_time"] if self.io_queue else 0)
                size = self.disk_models[model_name][1]
                self.io_queue.append(dict(model_name=model_name,
                    estimated_time=start + size / self.hardware_info.get("disk_bandwidth", 1)))
            completion = self.grouped_host_waiters[model_name]
        # Cancelling a caller must not cancel a native disk read or its owner record.
        await asyncio.shield(completion)
        if already_loaded:
            await self.confirm_grouped_host(model_name)
        return True

    async def confirm_grouped_host(self, model_name):
        async with self.lock:
            attempt = self.grouped_host_attempts[model_name]
            if attempt["failed"] or model_name not in self.pinned_memory_pool:
                raise RuntimeError("Grouped host state is not available")
            try:
                for path, token in attempt["receipts"].items():
                    await asyncio.to_thread(self.client.confirm_cpu_model_loaded, path, token)
            except BaseException:
                attempt["failed"] = True
                self.pinned_memory_pool.pop(model_name, None)
                raise
        return True

    async def _load_grouped_to_host(self, model_name):
        chunks = self.grouped_chunks[model_name]
        if not await self._lru_eviction(self.disk_models[model_name][1], chunks):
            raise MemoryError("Pinned pool cannot admit the complete grouped Store")
        attempt = dict(receipts={}, inflight=None, failed=False, unloaded=[])
        self.grouped_host_attempts[model_name] = attempt
        # Reserve the full rounded cohort, including on partial failure.
        self.pinned_memory_pool_usage += chunks
        try:
            for path in self.disk_models[model_name][0]:
                attempt["inflight"] = path
                token = await asyncio.to_thread(self.client.load_cpu_confirmed, path)
                if not isinstance(token, str) or not token:
                    raise RuntimeError("CPU copy did not return a submission identity")
                attempt["receipts"][path] = token
                attempt["inflight"] = None
            for path, token in attempt["receipts"].items():
                await asyncio.to_thread(self.client.confirm_cpu_model_loaded, path, token)
            self.pinned_memory_pool[model_name] = time.time()
        except BaseException:
            attempt["failed"] = True
            raise

    async def unload_grouped_host(self, model_name):
        async with self.lock:
            if model_name in self.queued_models or model_name not in self.pinned_memory_pool:
                return False
            return await self._unload_grouped_host(model_name)

    async def _unload_grouped_host(self, model_name):
        attempt = self.grouped_host_attempts[model_name]
        if attempt["failed"]:
            return False
        try:
            for path in self.disk_models[model_name][0]:
                attempt["inflight"] = path
                response = await asyncio.to_thread(self.client.unload_from_cpu, path)
                if not response or getattr(response, "model_path", None) != path:
                    raise RuntimeError("Grouped host unload was not confirmed")
                attempt["unloaded"].append(path)
                attempt["inflight"] = None
        except BaseException:
            attempt["failed"] = True
            self.pinned_memory_pool.pop(model_name, None)
            raise
        self.pinned_memory_pool.pop(model_name)
        self.pinned_memory_pool_usage -= self.grouped_chunks[model_name]
        self.grouped_host_attempts.pop(model_name)
        self.grouped_host_waiters.pop(model_name)
        return True

    async def loading_loop(self):
        while True:
            async with self.lock:
                if len(self.io_queue) == 0:
                    await asyncio.sleep(1)
                    continue
                model_info = self.io_queue[0]
                logger.info(
                    f"Loading {model_info['model_name']} to node {self.node_id}"
                )

            model_name = model_info["model_name"]
            if model_name in self.grouped_models:
                completion = self.grouped_host_waiters[model_name]
                try:
                    await self._load_grouped_to_host(model_name)
                except BaseException as error:
                    if not completion.done():
                        completion.set_exception(error)
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    logger.error(f"Failed grouped host load {model_name}: {error}")
                else:
                    completion.set_result(True)
                finally:
                    self.io_queue.pop(0)
                    self.queued_models.pop(model_name)
                continue
            model_path_list, model_size = self.disk_models[model_name]
            can_load = await self._lru_eviction(model_size)
            if not can_load:
                logger.warning(
                    f"{model_name} cannot be loaded to node {self.node_id}"
                )
                await asyncio.sleep(1)
                continue
            logger.info(f"Loading {model_name} to node {self.node_id}")
            ret = 1
            for model_path in model_path_list:
                ret = ret and self.client.load_into_cpu(model_path)
            self.io_queue.pop(0)
            self.queued_models.pop(model_name)
            if not ret:
                logger.error(f"Failed to load {model_name}")
                continue
            self.pinned_memory_pool[model_name] = time.time()
            self.pinned_memory_pool_usage += (
                model_size + self.chunk_size - 1
            ) // self.chunk_size
            logger.info(f"{model_name} loaded to host")

    async def _lru_eviction(self, model_size, required_chunks=None):
        # evict the least recently used models until the model can be loaded
        async with self.lock:
            sorted_models = sorted(
                self.pinned_memory_pool.items(), key=lambda x: x[1]
            )
            logger.info(f"Sorted models: {sorted_models}")
            if required_chunks is None:
                required_chunks = (
                    model_size + self.chunk_size - 1
                ) // self.chunk_size
            logger.info(
                f"Pinned memory pool usage: {self.pinned_memory_pool_usage} / {self.pinned_memory_pool_chunks}"
                f" requires {required_chunks} chunks"
            )
            while (
                self.pinned_memory_pool_usage + required_chunks
                > self.pinned_memory_pool_chunks
                and len(sorted_models) > 0
            ):
                model_name, _ = sorted_models.pop(0)
                if model_name not in self.queued_models:
                    if model_name in self.grouped_models:
                        try:
                            if not await self._unload_grouped_host(model_name):
                                return False
                        except Exception as error:
                            logger.error(f"Failed grouped host eviction {model_name}: {error}")
                            return False
                        continue
                    model_path_list, _ = self.disk_models[model_name]
                    for model_path in model_path_list:
                        self.client.unload_from_cpu(model_path)
                    self.pinned_memory_pool.pop(model_name)
                    unloaded_chunks = (
                        self.disk_models[model_name][1] + self.chunk_size - 1
                    ) // self.chunk_size
                    self.pinned_memory_pool_usage -= unloaded_chunks
                    logger.info(
                        f"{model_name} evicted {unloaded_chunks} chunks"
                    )
            return (
                self.pinned_memory_pool_usage + required_chunks
                <= self.pinned_memory_pool_chunks
            )

    def _get_model_path(self, model_name, backend):
        return os.path.join(backend, model_name)

    def _format_time(self, t):
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


# @ray.remote(num_cpus=1, resources={"control_node": 0.1})
class StoreManager:
    def __init__(self):
        logger.info("Initializing store manager")
        self.hardware_info = {}

        self.metadata_lock = asyncio.Lock()
        # Storage info
        self.round_robin_index = 0
        self.local_servers = {}
        self.model_info = {}
        self.model_storage_info = {}

    async def initialize_cluster(self) -> bool:
        logger.info("Initializing cluster and collecting hardware info")

        # Get worker nodes
        worker_node_info = get_worker_nodes()
        if not worker_node_info:
            logger.error("No worker nodes found")
            return False

        # Initialize hardware_info dictionary
        self.hardware_info = {}
        # Collect hardware info from each node
        hardware_info_futures = {
            node_id: collect_all_info.options(
                resources={f"worker_id_{node_id}": 0.01}
            ).remote()
            for node_id in worker_node_info
        }

        # Gather hardware info
        for node_id, future in hardware_info_futures.items():
            try:
                hardware_info = await future
                self.hardware_info[node_id] = hardware_info
                logger.info(f"Hardware info collected for node {node_id}")
            except Exception as e:
                logger.error(
                    f"Failed to collect hardware info from node {node_id}: {e}"
                )
                continue

        uninitialized_nodes = list(self.hardware_info.keys())

        while len(uninitialized_nodes) > 0:
            for node_id in uninitialized_nodes:
                if node_id in worker_node_info:
                    node_address = worker_node_info[node_id]["address"]
                    try:
                        sllm_store_client = SllmStoreClient(
                            f"{node_address}:8073"
                        )
                        local_server_config = (
                            sllm_store_client.get_server_config()
                        )
                    except Exception as e:
                        logger.warning(
                            f"Failed to connect to node {node_id}: {e}"
                        )
                        continue
                    else:
                        if not local_server_config:
                            logger.warning(
                                f"Failed to get server config for node {node_id}"  # noqa: E501
                            )
                            continue
                        if "chunk_size" not in local_server_config:
                            logger.error(
                                f"Chunk size not found in server config for node {node_id}"  # noqa: E501
                            )
                        chunk_size = local_server_config["chunk_size"]
                        if "mem_pool_size" not in local_server_config:
                            logger.error(
                                f"Memory pool size not found in server config for node {node_id}"
                            )
                        mem_pool_size = local_server_config["mem_pool_size"]
                        self.local_servers[node_id] = SllmLocalStore(
                            node_id,
                            sllm_store_client,
                            mem_pool_size,
                            chunk_size,
                            self.hardware_info[node_id],
                        )
                        uninitialized_nodes.remove(node_id)
                        logger.info(
                            f"Node {node_id} initialized, chunk size: {chunk_size}"  # noqa: E501
                        )
                        break
            logger.info(
                f"Waiting for nodes {uninitialized_nodes} to be initialized"  # noqa: E501
            )
            await asyncio.sleep(1)

        return True

    async def get_hardware_info(self):
        return self.hardware_info

    async def get_model_info(self, model_name: Optional[str] = None):
        logger.info(f"Getting info for {model_name}")
        async with self.metadata_lock:
            if model_name is not None:
                return self.model_info.get(model_name, {})
            else:
                return self.model_info

    async def get_store_info(self, node_id: Optional[str] = None):
        async with self.metadata_lock:
            if node_id is not None:
                if node_id not in self.local_servers:
                    logger.error(f"Node {node_id} not found")
                    return {}
                return await self.local_servers[node_id].get_store_info()
            else:
                node_info = {}
                for node_id in self.local_servers:
                    node_info[node_id] = await self.local_servers[
                        node_id
                    ].get_store_info()
                return node_info

    async def confirm_grouped_host(self, node_id: str, model_name: str) -> bool:
        async with self.metadata_lock:
            local_server = self.local_servers[node_id]
        return await local_server.confirm_grouped_host(model_name)

    async def unload_grouped_host(self, node_id: str, model_name: str) -> bool:
        async with self.metadata_lock:
            local_server = self.local_servers[node_id]
        return await local_server.unload_grouped_host(model_name)

    async def load_to_host(self, node_id: str, model_name: str) -> bool:
        async with self.metadata_lock:
            if node_id not in self.local_servers:
                logger.error(f"Node {node_id} not found")
                return False
            local_server = self.local_servers[node_id]
        logger.info(f"Loading {model_name} to node {node_id}")
        return await local_server.load_to_host(model_name)

    async def register(self, model_config):
        model_name = model_config.get("model")
        backend = model_config.get("backend", None)
        if backend is None:
            logger.error(f"Backend not specified for {model_name}")
            return
        backend_config = model_config.get("backend_config", {})
        placement_config = model_config.get("placement_config", {})
        if model_name not in self.model_info:
            self.model_storage_info[model_name] = {}
            logger.info(f"Registering new {model_name}")

            backend = model_config.get("backend", None)
            pretrained_model_name_or_path = backend_config.get(
                "pretrained_model_name_or_path", None
            )
            # 1. download this model to one worker using round-robin
            worker_node_info = get_worker_nodes()

            n_nodes = len(worker_node_info)
            assert n_nodes > 0, "No worker nodes found"

            for node_id, node_info in worker_node_info.items():
                node_address = node_info["address"]
                if node_id not in self.local_servers:
                    if self.local_servers:
                        first_node = next(iter(self.local_servers.values()))
                        self.local_servers[node_id] = SllmLocalStore(
                            node_id,
                            SllmStoreClient(f"{node_address}:8073"),
                            1,
                            first_node.chunk_size,
                            first_node.hardware_info,
                        )
                    else:
                        logger.error(f"Node {node_id} not found")
                        raise ValueError(f"Node {node_id} not found")

            local_disk = []
            if placement_config and "local_disk" in placement_config:
                local_disk = placement_config["local_disk"]
                if not all(
                    [node_id in worker_node_info for node_id in local_disk]
                ):
                    logger.error(
                        f"Invalid target nodes {local_disk}, worker nodes: {worker_node_info}"  # noqa: E501
                    )
                    return
            else:
                # round-robin
                node_id = list(worker_node_info.keys())[
                    self.round_robin_index % n_nodes
                ]
                self.round_robin_index += 1
                local_disk = [node_id]

            memory_pool = []
            if placement_config and "memory_pool" in placement_config:
                memory_pool = placement_config["memory_pool"]
                if not all(
                    [node_id in worker_node_info for node_id in memory_pool]
                ):
                    logger.error(
                        f"Invalid target nodes {memory_pool}, worker nodes: {worker_node_info}"  # noqa: E501
                    )
                    return

            logger.info(
                f"Downloading model {pretrained_model_name_or_path} to nodes {local_disk}"  # noqa: E501
            )
            for node_id in local_disk:
                if backend == "transformers":
                    hf_model_class = backend_config.get("hf_model_class", None)
                    torch_dtype = backend_config.get("torch_dtype", "float16")
                    if hf_model_class is None:
                        logger.error(
                            "hf_model_type not specified in backend_config."
                        )
                        break
                    await self.download_transformers_model(
                        model_name,
                        pretrained_model_name_or_path,
                        node_id,
                        hf_model_class,
                        torch_dtype,
                    )
                elif (backend == "vllm" and backend_config.get("load_format")
                      == "sllm_v4_grouped_store"):
                    # Worker-local admission verifies an existing COMPLETE Store;
                    # never invoke the legacy converter for this load format.
                    pass
                elif backend == "vllm":
                    await self.download_vllm_model(
                        model_name,
                        pretrained_model_name_or_path,
                        node_id,
                        model_config.get("num_gpus", 1),
                        backend_config.get("tensor_parallel_size", 1),
                        backend_config.get("torch_dtype", "float16"),
                    )
                else:
                    logger.error(f"Backend {backend} not supported")
                    break
                local_server = self.local_servers[node_id]
                model_size = await local_server.register_model(
                    model_name, backend, backend_config
                )
                # record the storage info
                self.model_storage_info[model_name][node_id] = True
                logger.info(f"{model_name} downloaded to node {node_id}")
                if node_id in memory_pool:
                    # preload to memory pool
                    await self.load_to_host(
                        node_id, model_name
                    )
                    logger.info(f"{model_name} loaded to memory pool")
            self.model_info[model_name] = model_size
            logger.info(f"{model_name} registered")
        else:
            # TODO: apply new placement config, if given
            pass

    async def register_lora_adapter(
        self,
        base_model_name,
        adapter_name,
        adapter_path,
        backend_config,
    ) -> int:
        if base_model_name not in self.model_storage_info:
            logger.error(
                f"Base model {base_model_name} not found in storage info"
            )
            return -1

        # Get the first node_id where the model is stored
        node_id = next(iter(self.model_storage_info[base_model_name].keys()))

        hf_model_class = backend_config.get("hf_model_class", None)
        torch_dtype = backend_config.get("torch_dtype", "float16")
        logger.info(f"Downloading {adapter_path} to {node_id}")
        return await download_lora_adapter.options(
            resources={"worker_node": 0.1, f"worker_id_{node_id}": 0.1}
        ).remote(
            base_model_name,
            adapter_name,
            adapter_path,
            hf_model_class,
            torch_dtype,
        )

    async def download_transformers_model(
        self,
        model_name,
        pretrained_model_name_or_path,
        node_id,
        hf_model_class,
        torch_dtype,
    ) -> int:
        logger.info(
            f"Downloading {pretrained_model_name_or_path} to node {node_id}"
        )
        return await download_transformers_model.options(
            resources={"worker_node": 0.1, f"worker_id_{node_id}": 0.1}
        ).remote(
            model_name,
            pretrained_model_name_or_path,
            torch_dtype,
            hf_model_class,
        )

    async def download_vllm_model(
        self,
        model_name,
        pretrained_model_name_or_path,
        node_id,
        num_gpus,
        tensor_parallel_size,
        torch_dtype,
    ):
        logger.info(
            f"Downloading {pretrained_model_name_or_path} to node {node_id}"
        )
        vllm_backend_downloader = (
            ray.remote(VllmModelDownloader)
            .options(
                num_gpus=num_gpus,
                resources={"worker_node": 0.1, f"worker_id_{node_id}": 0.1},
            )
            .remote()
        )
        return await vllm_backend_downloader.download_vllm_model.remote(
            model_name,
            pretrained_model_name_or_path,
            torch_dtype,
            tensor_parallel_size,
        )
