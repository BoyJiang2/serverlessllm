"""Equal host call boundaries for native and grouped V7 full weight loads."""
import os


def register():
    if not os.environ.get("SLLM_EVENT_DIR"):
        return
    from vllm.distributed import get_tensor_model_parallel_rank
    from vllm.model_executor.model_loader.default_loader import DefaultModelLoader
    from sllm_store.event_trace import traced
    from sllm_store.vllm_v4_loader import V4GroupedStoreLoader

    for cls in (DefaultModelLoader, V4GroupedStoreLoader):
        function = cls.load_model
        marker = getattr(function, "_sllm_trace_name", None)
        if marker not in (None, "full_weight_load"):
            raise ValueError("Conflicting full-load trace hook")
        if marker is None:
            cls.load_model = traced("full_weight_load", values_from=lambda args, kwargs: dict(
                rank=get_tensor_model_parallel_rank()))(function)
