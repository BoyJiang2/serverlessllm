"""Explicit device-bound normalization of source-proven no-actorder scratch.

Call with an explicit source-after-load or target-before-restore phase. This changes
parameter bindings; only source-phase receipts may accompany a subsequent export.
It neither executes nor validates the remaining quantization postprocessing.
"""
import hashlib
import inspect
from pathlib import Path
import re
import types

from contract import CONFIG_SHA256, validate_config

SOURCE_SHA256 = '0d84c593350f59f3ff2d45beb3b3ef99dc1f155e6b5b17ffc806000e5857b70f'
METHOD_MODULE = 'vllm.model_executor.layers.quantization.compressed_tensors.compressed_tensors_moe'
METHOD_CLASS = 'CompressedTensorsWNA16MarlinMoEMethod'
NAMES = ('w13_weight_g_idx', 'w2_weight_g_idx', 'w13_g_idx_sort_indices', 'w2_g_idx_sort_indices')
PROFILE = dict(tp=8, pp=1, expert_parallel=False, sequence_parallel_moe=False,
               use_mla=True, encoder_tp_mode='data')


def _prepare_cpu_auxiliary(layer, *, name, rank, profile, config_raw,
                           vllm_version, phase):
    return _prepare_auxiliary(layer, name=name, rank=rank, profile=profile, config_raw=config_raw,
                              vllm_version=vllm_version, phase=phase, device='cpu')


def _prepare_cuda_auxiliary(layer, *, device, **context):
    import torch
    expected = torch.device(device)
    if expected.type != 'cuda' or expected.index is None:
        raise ValueError('explicit indexed CUDA device required')
    return _prepare_auxiliary(layer, device=expected, **context)


def _prepare_auxiliary(layer, *, name, rank, profile, config_raw, vllm_version, phase, device):
    import torch
    from device_state import owned_device_layout
    from cpu_tensor_io import tensor_layout as cpu_layout
    expected_device = torch.device(device)
    def tensor_layout(tensor):
        return cpu_layout(tensor) if expected_device.type == 'cpu' else owned_device_layout(tensor, expected_device)
    config = validate_config(config_raw)['text_config']
    if (type(rank) is not int or rank not in range(8) or profile != PROFILE
            or vllm_version != '0.19.1' or phase not in ('checkpoint_loaded_before_postprocess', 'target_initialized_before_restore')):
        raise ValueError('unreviewed rank/profile/ABI/phase')
    match = re.fullmatch(r'language_model\.model\.layers\.(\d+)\.mlp\.experts', name)
    if not match or int(match[1]) not in range(1, 61):
        raise ValueError('not a frozen routed expert layer')
    quant = config['quantization_config']['config_groups']['group_0']['weights']
    method = layer.quant_method
    cls = type(method)
    if (quant['actorder'] is not None or method.actorder is not None
            or method.kernel_backend != 'Marlin' or cls.__name__ != METHOD_CLASS
            or cls.__module__ != METHOD_MODULE):
        raise ValueError('only the frozen Marlin no-actorder method is supported')
    if getattr(method.process_weights_after_loading, '__func__', None) is not cls.process_weights_after_loading:
        raise ValueError('quantization method has an instance override')
    source = Path(inspect.getsourcefile(cls.process_weights_after_loading))
    source_raw = source.read_bytes()
    if hashlib.sha256(source_raw).hexdigest() != SOURCE_SHA256:
        raise ValueError('quantization method source differs')
    module_code = compile(source_raw, str(source), 'exec', dont_inherit=True)
    class_code = next(c for c in module_code.co_consts if isinstance(c, types.CodeType) and c.co_name == METHOD_CLASS)
    expected_code = next(c for c in class_code.co_consts if isinstance(c, types.CodeType)
                         and c.co_name == 'process_weights_after_loading')
    if cls.process_weights_after_loading.__code__ != expected_code:
        raise ValueError('quantization method code differs from frozen source')
    dimensions = dict(num_experts=config['n_routed_experts'], hidden_size=config['hidden_size'],
                      intermediate_size_per_partition=config['moe_intermediate_size'] // 8)
    before, replacements = {}, {}
    for leaf in NAMES:
        tensor = getattr(layer, leaf)
        layout = tensor_layout(tensor)
        width = dimensions['hidden_size'] if leaf.startswith('w13') else dimensions['intermediate_size_per_partition']
        if (layer._parameters.get(leaf) is not tensor or tensor.requires_grad
                or layout['dtype'] != 'torch.int32'
                or layout['shape'] != [dimensions['num_experts'], width]):
            raise ValueError('auxiliary binding/layout differs: ' + leaf)
        before[leaf] = layout
        replacements[leaf] = torch.nn.Parameter(
            torch.empty((dimensions['num_experts'], 0), dtype=torch.int32, device=tensor.device),
            requires_grad=False)
    # Reject ties to payload or other state before changing any binding. Never read scratch values.
    pointers = {getattr(layer, leaf).untyped_storage().data_ptr() for leaf in NAMES}
    for other, tensor in list(layer.named_parameters(remove_duplicate=False)) + list(layer.named_buffers(remove_duplicate=False)):
        if other not in NAMES and tensor.numel() and tensor.untyped_storage().data_ptr() in pointers:
            raise ValueError('auxiliary shares storage with other state')
    receipt = dict(layer=name, rank=rank, phase=phase, action='normalize_to_empty_before_postprocess',
                config_sha256=CONFIG_SHA256, method_module=METHOD_MODULE, method_class=METHOD_CLASS,
                method_source_sha256=SOURCE_SHA256, kernel_backend='Marlin', actorder=None,
                profile=dict(profile), vllm_version=vllm_version, before=before,
                after={leaf: tensor_layout(tensor) for leaf, tensor in replacements.items()},
                scratch_payload_read=False, actual_backend_initialized=False,
                all_layers_verified=False, native_loader_verified=False, runtime_admitted=False)
    return replacements, receipt


def normalize_cpu_auxiliary(layer, **kwargs):
    replacements, receipt = _prepare_cpu_auxiliary(layer, **kwargs)
    for leaf, tensor in replacements.items():
        setattr(layer, leaf, tensor)
    return receipt
