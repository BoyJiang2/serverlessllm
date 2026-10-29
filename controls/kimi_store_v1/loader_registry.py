"""Register candidates only inside a separately admitted private worker process."""
from vllm_cpu_hook import _verify_function

SOURCE_SHA = 'a8b9804eba44d4f20901db9528825aa69095fb159766f76079cf2db82095b3e4'
FORMATS = {'export': 'sllm_kimi_export_v1', 'native': 'sllm_kimi_native_v1'}


def register_candidate_loader(registry, loader_class, *, mode):
    """Single-thread worker startup only; never overwrite a foreign registration."""
    if mode not in FORMATS:
        raise ValueError('explicit candidate loader mode required')
    register = _verify_function(registry.register_model_loader, SOURCE_SHA)
    _verify_function(registry.get_model_loader, SOURCE_SHA)
    formats = register.__globals__['_LOAD_FORMAT_TO_MODEL_LOADER']
    if (registry._LOAD_FORMAT_TO_MODEL_LOADER is not formats
            or registry.BaseModelLoader is not register.__globals__['BaseModelLoader']):
        raise ValueError('registry namespace mismatch')
    if not isinstance(loader_class, type) or not issubclass(loader_class, registry.BaseModelLoader):
        raise ValueError('candidate must be a BaseModelLoader subclass')
    name = FORMATS[mode]
    if name in formats:
        if formats[name] is loader_class:
            return name
        raise ValueError('candidate format already belongs to another loader')
    register(name)(loader_class)
    return name
