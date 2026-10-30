"""Post-install CUDA target semantics; no loader, admission or kernel replacement."""
from vllm_cpu_hook import _verify_function, UTILS_SHA256
from vllm_native_hook import _require_rank_device


def process_restored_mixed_rank(process_weights, model, model_config, *, device):
    import torch
    expected = torch.device(device)
    if expected.type != 'cuda' or expected.index not in range(8):
        raise ValueError('explicit TP8 CUDA postprocess target required')
    original = _verify_function(process_weights, UTILS_SHA256)
    _verify_function(original.__globals__['device_loading_context'], UTILS_SHA256)
    if (getattr(model, '_kimi_independent_records_bound', False) is not True
            or hasattr(model, '_kimi_native_allocation_tensors')
            or model_config.dtype != torch.bfloat16
            or model_config.quantization != 'compressed-tensors'):
        raise ValueError('independent restored BF16 compressed-tensors rank required')
    if hasattr(model, '_kimi_mixed_postprocess_status'):
        raise ValueError('one-shot mixed postprocessing already attempted')
    _require_rank_device(model, expected)
    if any(getattr(p, '_vllm_is_uva_offloaded', False) for p in model.parameters()):
        raise ValueError('UVA offloading is outside the frozen profile')
    model._kimi_mixed_postprocess_status = 'processing'
    try:
        original(model, model_config, expected)
        _require_rank_device(model, expected)
    except BaseException:
        model._kimi_mixed_postprocess_status = 'failed'
        raise
    model._kimi_mixed_postprocess_status = 'returned'
    return dict(target_device=str(expected), quant_then_attention_postprocess_returned=True,
                native_loader_verified=False, peak_memory_verified=False, runtime_admitted=False)
