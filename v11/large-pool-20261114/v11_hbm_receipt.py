"""Per-rank inventory of live CUDA tensor storage before B1 backend exit."""
import time


def storage_ranges(tensors):
    ranges = set()
    for tensor in tensors:
        if tensor.is_cuda:
            storage = tensor.untyped_storage()
            if storage.nbytes():
                assert storage.data_ptr()
                ranges.add((str(tensor.device), storage.data_ptr(),
                            storage.data_ptr() + storage.nbytes()))
    return ranges


def union_bytes(ranges):
    total = end = 0
    previous = None
    for device, start, finish in sorted(ranges):
        if device != previous:
            end = 0
            previous = device
        total += max(0, finish - max(start, end))
        end = max(end, finish)
    return total


class V11HbmReceipt:
    def sllm_v11_hbm_receipt(self):
        from sllm_store.event_trace import process_identity

        started = time.monotonic_ns()
        runner = self.model_runner
        model = runner.get_model()
        weights = storage_ranges(model.parameters())
        buffers = storage_ranges(model.buffers())
        kv = storage_ranges(runner.kv_caches)
        weight_bytes, buffer_bytes, kv_bytes = map(union_bytes, (weights, buffers, kv))
        total = union_bytes(weights | buffers | kv)
        return dict(rank=self.rank, identity=process_identity(), started_ns=started,
                    finished_ns=time.monotonic_ns(), parameter_storage_bytes=weight_bytes,
                    buffer_storage_bytes=buffer_bytes, runner_kv_storage_bytes=kv_bytes,
                    union_storage_bytes=total,
                    shared_category_bytes=weight_bytes + buffer_bytes + kv_bytes - total)
