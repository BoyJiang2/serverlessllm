"""Stage the opt-in V4 Marlin scale rebind against the exact installed image source."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from weakref import WeakSet


IMAGE = "sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d"
BASE_SHA = "bf4cc2d80d32963dee1e38e3ddacc375e7f3c4d021ba3a9cd4ed544e3567e5e6"
TARGET = "/opt/sllm-v4/lib/python3.12/site-packages/vllm/model_executor/model_loader/reload/layerwise.py"
OPT_IN = "SLLM_V11_V4_MARLIN_REBIND"

# Included verbatim in the isolated layerwise.py overlay so the runtime needs
# only a single read-only file mount.
HELPERS = '''
_V4_MARLIN_PENDING = WeakSet()
_V4_MARLIN_ENV = "SLLM_V11_V4_MARLIN_REBIND"


def _v4_marlin_type(value, path):
    cls = type(value)
    return f"{cls.__module__}.{cls.__name__}" == path


def _v4_marlin_mark(model):
    import os

    if os.environ.get(_V4_MARLIN_ENV) != "1" or not _v4_marlin_type(
        model, "vllm.model_executor.models.deepseek_v4.DeepseekV4ForCausalLM"
    ):
        return
    targets = []
    for layer in model.modules():
        method = getattr(layer, "quant_method", None)
        if getattr(getattr(method, "mxfp4_backend", None), "name", None) == "MARLIN":
            if not _v4_marlin_type(
                method, "vllm.model_executor.layers.quantization.mxfp4.Mxfp4MoEMethod"
            ):
                raise RuntimeError("V4 Marlin rebind: unexpected quantization method")
            targets.append(layer)
    if len(targets) != 61 or any(layer in _V4_MARLIN_PENDING for layer in targets):
        raise RuntimeError("V4 Marlin rebind: expected 61 fresh MXFP4 expert layers")
    _V4_MARLIN_PENDING.update(targets)


def _v4_marlin_range(tensor):
    storage = tensor.untyped_storage()
    pointer, size = storage.data_ptr(), storage.nbytes()
    if not tensor.is_cuda or pointer <= 0 or size <= 0:
        raise RuntimeError("V4 Marlin rebind: expected nonempty CUDA scale storage")
    return pointer, pointer + size


def _v4_marlin_layout(tensor):
    return (tensor.device, tensor.dtype, tuple(tensor.shape), tuple(tensor.stride()),
            tensor.storage_offset(), tensor.untyped_storage().nbytes())


def _v4_marlin_collect(layer, originals, pointer_to_data):
    method = layer.quant_method
    config = method.moe_quant_config
    experts = method.moe_kernel.fused_experts
    if (not _v4_marlin_type(experts, "vllm.model_executor.layers.fused_moe.fused_marlin_moe.MarlinExperts")
        or config is None or config is not experts.quant_config or not config.use_mxfp4_w4a16):
        raise RuntimeError("V4 Marlin rebind: expected one shared MXFP4 W4A16 config")

    rebindings = []
    for name, descriptor in (("w13_weight_scale", config._w1), ("w2_weight_scale", config._w2)):
        original = originals.get(name)
        temporary = getattr(layer, name, None)
        if (original is None or temporary is None or layer._parameters.get(name) is not temporary
            or descriptor.scale is not temporary or descriptor.bias is not None):
            raise RuntimeError(f"V4 Marlin rebind: unexpected source reference for {name}")
        if (str(original.dtype) != "torch.float8_e8m0fnu"
            or _v4_marlin_layout(original) != _v4_marlin_layout(temporary)):
            raise RuntimeError(f"V4 Marlin rebind: layout differs for {name}")
        begin, end = _v4_marlin_range(original)
        _v4_marlin_range(temporary)
        if not any(allocation.tag == "weights" and ptr <= begin
                   and end <= ptr + allocation.handle[1] for ptr, allocation in pointer_to_data.items()):
            raise RuntimeError(f"V4 Marlin rebind: original storage outside weights pool for {name}")
        rebindings.append((name, descriptor, original))
    return rebindings


def _v4_marlin_finish(layer, rebindings):
    if any(layer._parameters.get(name) is not original for name, _, original in rebindings):
        raise RuntimeError("V4 Marlin rebind: original registered parameters were not restored")
    for _, descriptor, original in rebindings:
        descriptor.scale = original
    _V4_MARLIN_PENDING.remove(layer)

'''


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f"expected exactly one pinned layerwise source anchor: {old!r}")
    return source.replace(old, new)


def patch_layerwise(source):
    source = replace(
        source, "from weakref import WeakKeyDictionary\n",
        "from weakref import WeakKeyDictionary, WeakSet\n",
    )
    source = replace(source, "def get_layerwise_info(", HELPERS + "\ndef get_layerwise_info(")
    source = replace(
        source,
        '    model._do_torchao_reload = False\n\n    for layer in model.modules():',
        '    model._do_torchao_reload = False\n    _v4_marlin_mark(model)\n\n    for layer in model.modules():',
    )
    source = replace(
        source, "        info.reset()\n\n\ndef finalize_layerwise_reload",
        "        info.reset()\n\n"
        "    if _V4_MARLIN_PENDING and any(layer in _V4_MARLIN_PENDING for layer in model.modules()):\n"
        "        raise RuntimeError(\"V4 Marlin rebind: target layer did not complete copy and restore\")\n"
        "\n\ndef finalize_layerwise_reload",
    )
    source = replace(
        source,
        "    parameters, buffers = info.kernel_tensors\n    for name, param in parameters.items():\n"
        "        param.data.copy_(getattr(layer, name))",
        "    parameters, buffers = info.kernel_tensors\n"
        "    rebindings = None\n"
        "    if layer in _V4_MARLIN_PENDING:\n"
        "        from vllm.device_allocator.cumem import CuMemAllocator\n\n"
        "        rebindings = _v4_marlin_collect(\n"
        "            layer, parameters, CuMemAllocator.get_instance().pointer_to_data\n"
        "        )\n"
        "    for name, param in parameters.items():\n"
        "        param.data.copy_(getattr(layer, name))",
    )
    return replace(
        source, "    _place_kernel_tensors(layer, info)\n\n\ndef _place_kernel_tensors(",
        "    _place_kernel_tensors(layer, info)\n"
        "    if rebindings is not None:\n"
        "        _v4_marlin_finish(layer, rebindings)\n"
        "\n\ndef _place_kernel_tensors(",
    )


def build(installed_source, destination):
    original = installed_source.read_bytes()
    if sha256(original) != BASE_SHA:
        raise ValueError("layerwise.py does not match the pinned installed image")
    patched = patch_layerwise(original.decode()).encode()
    compile(patched, "layerwise.py", "exec")
    destination.mkdir()
    (destination / "layerwise.py").write_bytes(patched)
    manifest = dict(scope="isolated_v4_pro_marlin_checkpoint_reload_candidate",
                    image=IMAGE, target=TARGET, opt_in_env={OPT_IN: "1"},
                    installed_sha256=BASE_SHA, overlay_sha256=sha256(patched))
    (destination / "candidate-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return patched


def self_test(patched):
    import os
    import sys
    from unittest.mock import patch

    class Layer:
        def __init__(self, method, tensors):
            self.quant_method = method
            self._parameters = tensors

        def __getattr__(self, name):
            return self._parameters[name]

    class Scale:
        is_cuda = True
        device = "cuda:0"
        dtype = "torch.float8_e8m0fnu"
        shape = (2, 16)

        def __init__(self, pointer, value):
            self.pointer, self.value = pointer, value
            self.data = self

        def stride(self):
            return (16, 1)

        def storage_offset(self):
            return 0

        def untyped_storage(self):
            return SimpleNamespace(data_ptr=lambda: self.pointer, nbytes=lambda: 32)

        def copy_(self, source):
            self.value = source.value

    marked = WeakSet()
    allocations = {1000: SimpleNamespace(tag="weights", handle=(0, 256))}
    modules = {"vllm.device_allocator.cumem": SimpleNamespace(
        CuMemAllocator=SimpleNamespace(get_instance=lambda: SimpleNamespace(pointer_to_data=allocations)))}
    scope = dict(WeakSet=lambda: marked,
                 torch=SimpleNamespace(nn=SimpleNamespace(Module=Layer)), LayerReloadingInfo=object,
                 _place_kernel_tensors=lambda layer, info: setattr(
                     layer, "_parameters", info.kernel_tensors[0].copy()))
    tree = ast.parse(patched)
    subset = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and (
            node.name.startswith("_v4_marlin_") or node.name == "_copy_and_restore_kernel_tensors"
        ):
            subset.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id.startswith("_V4_MARLIN_") for target in node.targets
        ):
            subset.append(node)
    exec(compile(ast.Module(body=subset, type_ignores=[]), "candidate-test", "exec"), scope)

    def setup():
        names = ("w13_weight_scale", "w2_weight_scale")
        original = dict(zip(names, (Scale(1000, 1), Scale(1100, 2))))
        current = dict(zip(names, (Scale(2000, 10), Scale(2100, 20))))
        desc = [SimpleNamespace(scale=current[name], bias=None) for name in names]
        config = SimpleNamespace(_w1=desc[0], _w2=desc[1], use_mxfp4_w4a16=True)
        expert_type = type("MarlinExperts", (), {
            "__module__": "vllm.model_executor.layers.fused_moe.fused_marlin_moe"})
        experts = expert_type()
        experts.quant_config = config
        layer = Layer(SimpleNamespace(moe_quant_config=config,
                                      moe_kernel=SimpleNamespace(fused_experts=experts)), current)
        return layer, SimpleNamespace(kernel_tensors=(original, {})), names, desc, original, current

    with patch.dict(sys.modules, modules):
        copy_and_restore = scope["_copy_and_restore_kernel_tensors"]
        layer, info, names, descriptors, original, current = setup()
        marked.add(layer)
        copy_and_restore(layer, info)
        assert [original[name].value for name in names] == [10, 20]
        assert all(desc.scale is original[name] for name, desc in zip(names, descriptors))
        assert layer not in marked
        fresh = dict(zip(names, (Scale(3000, 100), Scale(3100, 200))))
        layer._parameters = fresh
        for name, desc in zip(names, descriptors):
            desc.scale = fresh[name]
        marked.add(layer)
        copy_and_restore(layer, info)
        assert all(desc.scale is original[name] for name, desc in zip(names, descriptors))
        assert [original[name].value for name in names] == [100, 200]

        layer, info, names, descriptors, original, current = setup()
        copy_and_restore(layer, info)
        assert all(desc.scale is current[name] for name, desc in zip(names, descriptors))

        for mismatch in ("scope", "storage", "layout", "reference"):
            layer, info, names, descriptors, original, current = setup()
            marked.add(layer)
            if mismatch == "scope":
                layer.quant_method.moe_kernel.fused_experts.quant_config = object()
            elif mismatch == "storage":
                original[names[1]].pointer = 3000
            elif mismatch == "layout":
                current[names[1]].shape = (1, 32)
            else:
                descriptors[1].scale = Scale(2100, 20)
            try:
                copy_and_restore(layer, info)
            except RuntimeError:
                pass
            else:
                raise AssertionError(f"{mismatch} was accepted")
            assert descriptors[0].scale is current[names[0]]
            assert [original[name].value for name in names] == [1, 2]

    v4_class = type("DeepseekV4ForCausalLM", (), {
        "__module__": "vllm.model_executor.models.deepseek_v4"})
    method_class = type("Mxfp4MoEMethod", (), {
        "__module__": "vllm.model_executor.layers.quantization.mxfp4"})
    method = method_class()
    method.mxfp4_backend = SimpleNamespace(name="MARLIN")
    targeted = [Layer(method, {}) for _ in range(61)]
    v4 = v4_class()
    v4.modules = lambda: targeted
    mark = scope["_v4_marlin_mark"]
    marked.clear()
    with patch.dict(os.environ, {OPT_IN: "0"}):
        mark(v4)
        assert not marked
    with patch.dict(os.environ, {OPT_IN: "1"}):
        mark(SimpleNamespace(modules=lambda: targeted))
        assert not marked
        mark(v4)
        assert set(marked) == set(targeted)
        try:
            mark(v4)
        except RuntimeError:
            pass
        else:
            raise AssertionError("uncompleted scope was accepted on the next reload")

    print("validated two copy/rebind cycles, opt-in model scope, and fail-closed mismatches")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installed_source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    patched = build(args.installed_source, args.destination)
    if args.self_test:
        self_test(patched)
