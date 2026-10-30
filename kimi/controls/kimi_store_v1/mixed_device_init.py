"""CPU text / official CUDA vision construction candidate, not a loader."""
from checkpoint_export import KIMI_SHA
from vllm_cpu_hook import _verify_function, UTILS_SHA256


def audit_mixed_registered_state(model, device):
    import torch
    expected = torch.device(device)
    if expected.type != 'cuda' or expected.index not in range(8):
        raise ValueError('explicit TP8 CUDA device required')
    roots = {'language_model': torch.device('cpu'), 'vision_tower': expected, 'mm_projector': expected}
    counts = dict.fromkeys(roots, 0)
    sizes, seen = dict.fromkeys(roots, 0), set()
    for name, tensor in list(model.named_parameters(remove_duplicate=False)) + list(
            model.named_buffers(remove_duplicate=False)):
        root, separator, leaf = name.partition('.')
        if not separator or root not in roots or tensor.device != roots[root]:
            raise ValueError('registered state violates mixed device policy: ' + name)
        if tensor.layout != torch.strided:
            raise ValueError('strided registered state required')
        counts[root] += 1
        storage = tensor.untyped_storage()
        key = (str(tensor.device), storage.data_ptr(), storage.nbytes())
        if key not in seen:
            sizes[root] += storage.nbytes()
            seen.add(key)
    if not all(counts.values()):
        raise ValueError('all three model branches require registered state')
    return dict(registered_counts=counts, registered_storage_bytes=sizes,
                hidden_state_audited=False, runtime_admitted=False,
                native_loader_verified=False, peak_memory_verified=False)


def _require_mixed_constructor(model_class, vllm_config, device):
    import torch
    expected = torch.device(device)
    if expected.type != 'cuda' or expected.index not in range(8):
        raise ValueError('explicit TP8 CUDA device required before construction')
    # Exact constructor keeps the official vision and projector .to() branches.
    original = _verify_function(model_class.__init__, KIMI_SHA, 'KimiK25ForConditionalGeneration')
    platform = original.__globals__['current_platform']
    if not platform.is_cuda() or torch.device(platform.current_device()) != expected:
        raise ValueError('current CUDA device differs before construction')
    if (vllm_config.model_config.dtype != torch.bfloat16
            or torch.get_default_dtype() != torch.bfloat16):
        raise ValueError('active BF16 default and model dtype required')
    return expected


def initialize_mixed_rank(model_class, vllm_config, *, device, prefix=''):
    import torch
    expected = _require_mixed_constructor(model_class, vllm_config, device)
    with torch.device('cpu'):
        model = model_class(vllm_config=vllm_config, prefix=prefix)
    receipt = audit_mixed_registered_state(model, expected)
    return model, receipt


def mixed_state_devices(model, device):
    """Declare devices from the reviewed branch policy, never observed placement."""
    import torch
    audit_mixed_registered_state(model, device)
    roots = {'language_model': torch.device('cpu'), 'vision_tower': torch.device(device),
             'mm_projector': torch.device(device)}
    return {name: roots[name.partition('.')[0]] for name, _ in
            list(model.named_parameters(remove_duplicate=False)) + list(model.named_buffers(remove_duplicate=False))}


def initialize_mixed_vllm_rank(initialize_model, model_class, vllm_config, *, device, prefix=''):
    """Preserve official quant configuration, current-config scope and reload metadata."""
    import torch
    original = _verify_function(initialize_model, UTILS_SHA256)
    expected = _require_mixed_constructor(model_class, vllm_config, device)
    with torch.device('cpu'):
        model = original(vllm_config=vllm_config, model_class=model_class,
                         model_config=vllm_config.model_config, prefix=prefix)
    return model, audit_mixed_registered_state(model, expected)
