"""Native Store lifecycle candidate; no registration, admission or service CLI."""
import hashlib
import json

from buffer_codec import CODEC, VISION_CACHE
from checkpoint_contract import load_targets
from contract import validate_config
from marlin_aux import PROFILE
from native_call import load_native_return, NativeCallFailure, TORCH_SHA, _store_directory
from native_index import read_index
from native_install import install_native_state
from rank_export import normalize_rank_cuda_auxiliary
from vllm_cpu_hook import _verify_function, BASE_SHA256, UTILS_SHA256


def _require_rank_device(model, device):
    import torch
    expected = torch.device(device)
    tensors = list(model.parameters()) + list(model.buffers())
    if not tensors or any(t.device != expected for t in tensors):
        raise ValueError('all initialized rank state must use the admitted CUDA device')
    if any(getattr(getattr(m, 'quant_method', None), 'uses_meta_device', False) for m in model.modules()):
        raise ValueError('online or meta quantization is unsupported')


def make_native_store_loader(base_loader, native_module, *, call_options, config_raw,
                             checkpoint_contract, profile, vllm_version):
    """Compose pinned Base loading with native call, raw audit and slot installation.

    This does not create a worker, enforce its authorization window, verify TP
    communication groups, cancel Store DMA, or publish serving readiness.
    """
    validate_config(config_raw)
    targets = load_targets(checkpoint_contract)
    keys = {'storage_path', 'model_path', 'expected_sidecar_sha256', 'rank', 'device_id',
            'max_allocation_bytes', 'timeout_seconds'}
    if (not isinstance(call_options, dict) or set(call_options) != keys or profile != PROFILE
            or vllm_version != '0.19.1'):
        raise ValueError('fixed native options, profile and ABI required')
    options = dict(call_options)
    for name in ('rank', 'device_id'):
        if type(options[name]) is not int or options[name] not in range(8):
            raise ValueError('explicit TP8 rank and device required')
    for name in ('max_allocation_bytes', 'timeout_seconds'):
        if type(options[name]) is not int or options[name] <= 0:
            raise ValueError('finite positive native budget required')
    _, directory = _store_directory(options['storage_path'], options['model_path'])
    raw = (directory / 'kimi_raw_state.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != options['expected_sidecar_sha256']:
        raise ValueError('sidecar digest mismatch before initialization')
    sidecar = json.loads(raw)
    index = read_index(directory, options['expected_sidecar_sha256'])
    if (sidecar['rank'] != options['rank'] or sidecar['state'] != 'before_quantization_postprocessing'
            or not targets <= set(index) or sidecar.get('state_bindings') is None):
        raise ValueError('rank source coverage or phase differs before initialization')
    allocation_bytes = sum(size for offset, size in {(row[0], row[1]) for row in index.values()})
    if not 0 < allocation_bytes <= options['max_allocation_bytes']:
        raise ValueError('rank source exceeds allocation budget before initialization')
    original = _verify_function(base_loader.load_model, BASE_SHA256, 'BaseModelLoader')
    _verify_function(original.__globals__['process_weights_after_loading'], UTILS_SHA256)
    _verify_function(native_module.load_dict_non_blocking, TORCH_SHA)
    platform = original.__globals__['current_platform']
    device = 'cuda:' + str(options['device_id'])

    class NativeStoreLoader(base_loader):
        def __init__(self, load_config):
            super().__init__(load_config)
            self.lifecycle_status = 'not_started'
            self.weights_status = 'not_started'
            self.native_call_receipt = None
            self.restore_receipt = None
            self.native_state = None
            self.discard_worker_required = False

        def download_model(self, model_config):
            raise ValueError('native Store loader only consumes authenticated existing assets')

        def load_model(self, vllm_config, model_config, prefix=''):
            import torch
            if self.lifecycle_status != 'not_started' or self.weights_status != 'not_started':
                raise ValueError('one-shot native lifecycle already attempted')
            self.lifecycle_status = 'loading'
            self.discard_worker_required = True
            try:
                load_device = vllm_config.load_config.device
                if load_device is None:
                    load_device = vllm_config.device_config.device
                parallel = vllm_config.parallel_config
                if (not platform.is_cuda() or torch.device(load_device) != torch.device(device)
                        or parallel.tensor_parallel_size != 8 or parallel.pipeline_parallel_size != 1
                        or parallel.enable_expert_parallel is not False
                        or parallel.use_sequence_parallel_moe is not False
                        or model_config.use_mla is not True
                        or model_config.multimodal_config.mm_encoder_tp_mode != 'data'
                        or model_config.quantization != 'compressed-tensors' or model_config.dtype != torch.bfloat16
                        or vllm_config.speculative_config is not None):
                    raise ValueError('frozen native CUDA profile required before initialization')
                model = super().load_model(vllm_config, model_config, prefix)
            except BaseException:
                self.lifecycle_status = 'failed'
                raise
            self.lifecycle_status = 'returned'  # A component result, not serving admission.
            return model

        def load_weights(self, model, model_config):
            if self.lifecycle_status != 'loading' or self.weights_status != 'not_started':
                raise ValueError('native weight load requires the active one-shot Base lifecycle')
            self.weights_status = 'loading'
            try:
                _require_rank_device(model, device)
                receipts = normalize_rank_cuda_auxiliary(model, device=device,
                    checkpoint_contract=checkpoint_contract, rank=options['rank'], profile=profile,
                    config_raw=config_raw, vllm_version=vllm_version, phase='target_initialized_before_restore',
                    buffer_codecs={VISION_CACHE: CODEC} if VISION_CACHE in dict(
                        model.named_buffers(remove_duplicate=False)) else None)
                try:
                    self.native_state, self.native_call_receipt = load_native_return(native_module, **options)
                except NativeCallFailure as error:
                    self.native_state, self.native_call_receipt = error.native_state, error.receipt
                    raise
                self.restore_receipt = install_native_state(directory, model, self.native_state,
                    options['expected_sidecar_sha256'], rank=options['rank'],
                    allocation=self.native_call_receipt['allocation'], checkpoint_contract=checkpoint_contract,
                    destination_auxiliary_receipts=receipts)
                self.native_state = None  # Model anchor owns all returned wrappers now.
                self.weights_status = 'installed'
            except BaseException:
                self.weights_status = 'failed'
                raise

    return NativeStoreLoader
