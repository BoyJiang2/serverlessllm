"""Install optional host-boundary hooks; upstream source bytes stay unchanged."""
import os

from sllm_store.event_trace import traced


def install_v4_trace_hooks():
    if not os.environ.get("SLLM_EVENT_DIR"):
        return
    from sllm_store import vllm_v4_loader as loader
    from vllm.distributed import get_tensor_model_parallel_rank
    from vllm.model_executor.model_loader import base_loader
    from vllm.v1.worker import gpu_worker

    loader.check_engine_source()
    rank = lambda args, kwargs: dict(rank=get_tensor_model_parallel_rank())
    targets = [
        (gpu_worker.Worker, "init_device", "device_setup_including_communication", None),
        (gpu_worker, "init_worker_distributed_environment", "communication_groups", None),
        (gpu_worker.Worker, "load_model", "weights_pipeline_including_allocation", None),
        (gpu_worker.Worker, "initialize_from_config", "kv_cache_allocation", None),
        (gpu_worker.Worker, "compile_or_warm_up_model", "compile_graph_and_warmup", None),
        (loader.DefaultModelLoader, "load_weights", "weight_ingestion", rank),
        (loader.V4StoreLoader, "load_weights", "weight_ingestion", rank),
        (loader.V4GroupedStoreLoader, "load_weights", "weight_ingestion", rank),
        (base_loader, "process_weights_after_loading", "quantization_postprocess", rank),
    ]
    for owner, attr, name, _ in targets:
        function = getattr(owner, attr)
        marker = getattr(function, "_sllm_trace_name", None)
        if not callable(function) or marker not in (None, name):
            raise ValueError(f"Conflicting trace hook: {attr}")
    for owner, attr, name, values in targets:
        function = getattr(owner, attr)
        if getattr(function, "_sllm_trace_name", None) != name:
            setattr(owner, attr, traced(name, values_from=values)(function))
