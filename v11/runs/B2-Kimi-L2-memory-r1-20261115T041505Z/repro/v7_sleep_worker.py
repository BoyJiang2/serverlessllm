"""Read-only worker receipt for the independent native Sleep L1 baseline."""
import hashlib
import json
import uuid
from pathlib import Path


class V7SleepWorkerExtension:
    def sllm_v7_sleep_receipt(self):
        import torch
        from vllm.device_allocator.cumem import CuMemAllocator
        from sllm_store.event_trace import process_identity

        if not self.model_config.enable_sleep_mode:
            raise ValueError("Native sleep mode is not enabled")
        allocator = CuMemAllocator.get_instance()
        allocations, tags = [], {}
        for pointer, data in allocator.pointer_to_data.items():
            size = data.handle[1]
            backup = data.cpu_backup_tensor
            backup_bytes = 0
            if backup is not None:
                backup_bytes = backup.numel() * backup.element_size()
                if backup.device.type != "cpu" or backup_bytes != size:
                    raise ValueError("Sleep backup is not a complete CPU allocation")
            row = tags.setdefault(data.tag, dict(bytes=0, allocations=0, cpu_backup_bytes=0))
            row["bytes"] += size
            row["allocations"] += 1
            row["cpu_backup_bytes"] += backup_bytes
            allocations.append((pointer, size, data.tag))
        weight_ranges = [(p, p + n) for p, n, tag in allocations if tag == "weights"]
        uncovered, parameters = [], []
        for name, tensor in self.model_runner.model.named_parameters():
            if tensor.is_cuda and tensor.numel():
                start = tensor.data_ptr()
                span = 1 + sum((size-1)*stride for size, stride in zip(tensor.shape, tensor.stride()))
                end = start + span * tensor.element_size()
                parameters.append((name, start, list(tensor.shape), list(tensor.stride()), str(tensor.dtype)))
                if any(stride < 0 for stride in tensor.stride()) or not any(a <= start and end <= b for a, b in weight_ranges):
                    uncovered.append(name)
        # Do not read unmapped GPU tensors while sleeping.
        usage = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                 for line in Path("/proc/self/status").read_text().splitlines()
                 if line.startswith(("VmRSS:", "VmLck:", "Threads:"))}
        # torch returns torch._C._CUuuid here, which has no string methods.
        raw_uuid = str(torch.cuda.get_device_properties(torch.cuda.current_device()).uuid)
        return dict(
            identity=process_identity(), rank=self.rank, tags=tags,
            device_ordinal=torch.cuda.current_device(),
            device_uuid=str(uuid.UUID(raw_uuid.removeprefix('GPU-'))),
            allocation_signature=hashlib.sha256(json.dumps(sorted(allocations)).encode()).hexdigest(),
            parameter_signature=hashlib.sha256(json.dumps(sorted(parameters)).encode()).hexdigest(),
            cuda_parameter_count=len(parameters),
            uncovered_cuda_parameters=uncovered, proc_status=usage,
            cuda_allocated_bytes=torch.cuda.memory_allocated(),
            cuda_reserved_bytes=torch.cuda.memory_reserved(),
        )


    def sllm_kimi_checkpoint_diagnostic(self):
        import json
        import time
        from collections import defaultdict
        from contextlib import ExitStack
        from functools import wraps
        from unittest.mock import patch

        from vllm.model_executor.model_loader.reload import layerwise
        from vllm.v1.worker import gpu_model_runner

        totals = defaultdict(lambda: {"calls": 0, "ns": 0})

        def emit(event, **fields):
            print("KIMI_L2_TRACE " + json.dumps(dict(
                rank=self.rank, event=event, ns=time.monotonic_ns(), **fields)), flush=True)

        def timed(label, original, boundary=False):
            @wraps(original)
            def wrapped(*args, **kwargs):
                start = time.monotonic_ns()
                if boundary:
                    emit(label + "_begin")
                try:
                    return original(*args, **kwargs)
                finally:
                    elapsed = time.monotonic_ns() - start
                    totals[label]["calls"] += 1
                    totals[label]["ns"] += elapsed
                    if boundary:
                        emit(label + "_end", elapsed_ns=elapsed)
            return wrapped

        model_type = type(self.model_runner.get_model())
        with ExitStack() as stack:
            for module, name, boundary in (
                (gpu_model_runner, "initialize_layerwise_reload", True),
                (gpu_model_runner, "finalize_layerwise_reload", True),
                (model_type, "load_weights", True),
                (layerwise, "_layerwise_process", False),
                (layerwise, "_copy_and_restore_kernel_tensors", False),
            ):
                stack.enter_context(patch.object(
                    module, name, timed(name, getattr(module, name), boundary)))
            emit("reload_begin")
            try:
                self.reload_weights(is_checkpoint_format=True)
            finally:
                emit("reload_end", totals=totals)
