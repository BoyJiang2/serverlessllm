"""Rank-local Marlin MXFP4 scale inventory using tensor metadata only."""

from bisect import bisect_right
import hashlib
import time


MARLIN = {"MARLIN", "BATCHED_MARLIN"}
SCALE_NAMES = {"w1": "w13_weight_scale", "w2": "w2_weight_scale"}


def weight_ranges(pointer_to_data):
    ranges = sorted((ptr, ptr + row.handle[1]) for ptr, row in pointer_to_data.items()
                    if row.tag == "weights")
    if any(begin >= end for begin, end in ranges) or any(
        left[1] > right[0] for left, right in zip(ranges, ranges[1:])
    ):
        raise ValueError("invalid weights allocator ranges")
    return ranges


def weight_membership(begin, end, ranges, starts):
    index = bisect_right(starts, begin) - 1
    if index >= 0 and ranges[index][0] <= begin < end <= ranges[index][1]:
        return "contained"
    if (index >= 0 and begin < ranges[index][1]) or (
        index + 1 < len(starts) and starts[index + 1] < end
    ):
        return "partial_overlap"
    return "outside"


def storage_metadata(tensor, ranges, starts):
    if tensor is None:
        return None, None
    if not tensor.is_cuda:
        return {"device": str(tensor.device), "weight_map": "not_cuda"}, None
    storage = tensor.untyped_storage()
    size = storage.nbytes()
    ptr = storage.data_ptr()
    device = str(tensor.device)
    row = dict(device=device, storage_nbytes=size, shape=list(tensor.shape),
               stride=list(tensor.stride()), dtype=str(tensor.dtype),
               storage_offset_elements=tensor.storage_offset())
    if size == 0:
        return dict(row, weight_map="empty"), None
    if ptr == 0:
        return dict(row, weight_map="no_address"), None
    key = (device, ptr, ptr + size)
    # A stable virtual range key within one worker lifetime. CUDA can reuse addresses.
    signature = hashlib.sha256(f"{device}:{ptr}:{size}".encode()).hexdigest()
    return dict(row, storage_key=signature,
                weight_map=weight_membership(ptr, ptr + size, ranges, starts)), key


def storage_totals(keys):
    previous, right = None, 0
    byte_count = 0
    for device, begin, end in sorted(keys):
        if device != previous:
            right, previous = 0, device
        byte_count += max(0, end - max(begin, right))
        right = max(right, end)
    return dict(distinct_ranges=len(keys), storage_union_bytes=byte_count)


def scale_receipt(model, pointer_to_data):
    ranges = weight_ranges(pointer_to_data)
    starts = [begin for begin, _ in ranges]
    layers = []
    keys_by_holder = {name: set() for name in ("registered", "method", "kernel", "outside_weights")}
    for name, layer in model.named_modules():
        method = getattr(layer, "quant_method", None)
        backend = getattr(getattr(method, "mxfp4_backend", None), "name", None)
        if backend not in MARLIN:
            continue
        config = getattr(method, "moe_quant_config", None)
        kernel = getattr(method, "moe_kernel", None)
        experts = getattr(kernel, "fused_experts", None)
        expert_config = getattr(experts, "quant_config", None)
        comparisons = {}
        for side, param_name in SCALE_NAMES.items():
            param = layer._parameters.get(param_name)
            registered_in = "parameter" if param is not None else None
            if param is None:
                param = layer._buffers.get(param_name)
                registered_in = "buffer" if param is not None else None
            refs = {"registered": param,
                    "method": getattr(getattr(config, f"_{side}", None), "scale", None),
                    "kernel": getattr(getattr(expert_config, f"_{side}", None), "scale", None)}
            items, storage_keys = {}, {}
            for holder, tensor in refs.items():
                row, key = storage_metadata(tensor, ranges, starts)
                items[holder], storage_keys[holder] = row, key
                if key is not None:
                    keys_by_holder[holder].add(key)
                    if row["weight_map"] != "contained":
                        keys_by_holder["outside_weights"].add(key)
            comparisons[param_name] = dict(
                registered_in=registered_in, holders=items,
                method_same_object_as_registered=param is not None and refs["method"] is param,
                kernel_same_object_as_registered=param is not None and refs["kernel"] is param,
                method_same_storage_as_registered=storage_keys["registered"] is not None
                and storage_keys["method"] == storage_keys["registered"],
                kernel_same_storage_as_registered=storage_keys["registered"] is not None
                and storage_keys["kernel"] == storage_keys["registered"],
            )
        layers.append(dict(name=name, backend=backend, expert_class=type(experts).__name__,
                           shares_method_quant_config=config is not None and expert_config is config,
                           scales=comparisons))
    keys_by_holder["all"] = set.union(*keys_by_holder.values())
    return dict(layer_count=len(layers), scales_by_layer=layers,
                tagged_weight_map=dict(ranges=len(ranges),
                                       virtual_bytes=sum(end - begin for begin, end in ranges)),
                storage={name: storage_totals(keys) for name, keys in keys_by_holder.items()})


class V11V4ScaleWorkerExtension:
    def sllm_v7_sleep_receipt(self):
        from sllm_store.v7_sleep_worker import V7SleepWorkerExtension

        return V7SleepWorkerExtension.sllm_v7_sleep_receipt(self)

    def sllm_v4_mxfp4_scale_receipt(self, phase):
        from sllm_store.event_trace import process_identity
        from vllm.device_allocator.cumem import CuMemAllocator

        begin = time.monotonic_ns()
        result = scale_receipt(self.model_runner.get_model(),
                               CuMemAllocator.get_instance().pointer_to_data)
        return dict(rank=self.rank, identity=process_identity(), phase=phase,
                    begin_ns=begin, end_ns=time.monotonic_ns(), **result)


def self_test():
    from types import SimpleNamespace as Obj

    def tensor(ptr, size=64, offset=0):
        return Obj(is_cuda=True, device="cuda:0", untyped_storage=lambda:
                   Obj(data_ptr=lambda: ptr, nbytes=lambda: size),
                   shape=(16,), stride=lambda: (1,), dtype="float8_e8m0fnu",
                   storage_offset=lambda: offset)

    def config(scale1, scale2):
        return Obj(_w1=Obj(scale=scale1), _w2=Obj(scale=scale2))

    s1, s2 = tensor(1000), tensor(1100)
    view = tensor(1000, offset=8)
    base_config = config(view, s2)
    experts = Obj(quant_config=base_config)
    method = Obj(mxfp4_backend=Obj(name="MARLIN"), moe_quant_config=base_config,
                 moe_kernel=Obj(fused_experts=experts))
    layer = Obj(quant_method=method, _parameters=dict(w13_weight_scale=s1, w2_weight_scale=s2),
                _buffers={})
    allocations = {1000: Obj(handle=(0, 200), tag="weights"),
                   2000: Obj(handle=(0, 300), tag="kv_cache")}
    model = Obj(named_modules=lambda: (("not-a-marlin-layer", Obj()), ("mlp.experts", layer)))

    before = scale_receipt(model, allocations)
    assert before["layer_count"] == 1
    assert before["storage"]["all"] == dict(distinct_ranges=2, storage_union_bytes=128)
    assert before["storage"]["outside_weights"]["storage_union_bytes"] == 0
    check = before["scales_by_layer"][0]
    assert check["shares_method_quant_config"]
    assert check["scales"]["w13_weight_scale"]["kernel_same_storage_as_registered"]
    assert not check["scales"]["w13_weight_scale"]["kernel_same_object_as_registered"]

    method.moe_quant_config = config(tensor(2000), tensor(1170))
    experts.quant_config = config(tensor(2032), s2)
    after = scale_receipt(model, allocations)
    check = after["scales_by_layer"][0]
    assert not check["shares_method_quant_config"]
    assert not check["scales"]["w13_weight_scale"]["kernel_same_storage_as_registered"]
    assert check["scales"]["w2_weight_scale"]["holders"]["method"]["weight_map"] == "partial_overlap"
    assert after["storage"]["outside_weights"] == dict(distinct_ranges=3, storage_union_bytes=160)
    assert after["storage"]["all"] == dict(distinct_ranges=5, storage_union_bytes=288)
    print("validated Marlin alias, stale/new scale refs, overlap, and tagged range accounting")


if __name__ == "__main__":
    self_test()
