"""Independent mixed-device loader candidate; no worker or runtime admission."""
import hashlib
import json
import types

from buffer_codec import CODEC, VISION_CACHE
from checkpoint_contract import load_targets
from contract import validate_config
from marlin_aux import PROFILE
from mixed_device_init import initialize_mixed_vllm_rank, mixed_state_devices
from mixed_postprocess import process_restored_mixed_rank
from native_call import _store_directory, NativeCallFailure
from native_index import read_index, read_metadata
from native_install import install_native_state
from native_record_call import load_native_records, _bound_runtime
from rank_export import normalize_rank_mixed_auxiliary
from vllm_cpu_hook import _verify_function, BASE_SHA256, UTILS_SHA256
from vllm_native_hook import _require_runtime_profile


def make_mixed_store_loader(base_loader, native_module, model_class, *, call_options,
                            config_raw, checkpoint_contract, profile, vllm_version,
                            device_memory_budget_bytes):
    validate_config(config_raw)
    targets = load_targets(checkpoint_contract)
    keys = {'storage_path', 'model_path', 'expected_sidecar_sha256', 'rank', 'device_id',
            'max_allocation_bytes', 'timeout_seconds', 'server_address', 'build_receipt',
            'expected_build_sha256'}
    if (not isinstance(call_options, dict) or set(call_options) != keys
            or profile != PROFILE or vllm_version != '0.19.1'
            or type(device_memory_budget_bytes) is not int or device_memory_budget_bytes <= 0):
        raise ValueError('fixed mixed options, profile and explicit device budget required')
    options = dict(call_options)
    if (any(type(options[n]) is not int or options[n] not in range(8) for n in ('rank', 'device_id'))
            or any(type(options[n]) is not int or options[n] <= 0 for n in
                   ('max_allocation_bytes', 'timeout_seconds'))):
        raise ValueError('explicit TP8 identity and finite budgets required')
    _bound_runtime(native_module, options['build_receipt'], options['expected_build_sha256'])
    _, directory = _store_directory(options['storage_path'], options['model_path'])
    raw = read_metadata(directory, 'kimi_raw_state.json')
    if hashlib.sha256(raw).hexdigest() != options['expected_sidecar_sha256']:
        raise ValueError('sidecar digest differs before initialization')
    sidecar = json.loads(raw)
    index = read_index(directory, options['expected_sidecar_sha256'])
    if (sidecar['rank'] != options['rank'] or sidecar['state'] != 'before_quantization_postprocessing'
            or sidecar.get('state_bindings') is None or not targets <= set(index)):
        raise ValueError('complete target coverage, roles and source phase required')
    payload = sum(size for offset, size in {(r[0], r[1]) for r in index.values()})
    checkpoint_bytes = sum(size for offset, size in {(index[n][0], index[n][1]) for n in targets})
    if not 0 < payload <= options['max_allocation_bytes']:
        raise ValueError('source exceeds allocation budget')
    # Retain the conservative stop until a full mixed-device peak plan is reviewed.
    # This is not a claim that two full GPU copies remain, or a sufficient peak budget.
    if checkpoint_bytes * 2 > device_memory_budget_bytes:
        raise MemoryError('conservative checkpoint budget stop remains pending mixed peak review')
    original = _verify_function(base_loader.load_model, BASE_SHA256, 'BaseModelLoader')
    initialize = original.__globals__['initialize_model']
    process = original.__globals__['process_weights_after_loading']
    _verify_function(process, UTILS_SHA256)
    device = 'cuda:' + str(options['device_id'])

    def initialize_private(**kwargs):
        model, _ = initialize_mixed_vllm_rank(initialize, model_class,
            kwargs['vllm_config'], device=device, prefix=kwargs.get('prefix', ''))
        return model

    def process_private(model, model_config, target_device):
        if str(target_device) != device:
            raise ValueError('Base final target differs from admitted CUDA device')
        process_restored_mixed_rank(process, model, model_config, device=device)

    # Execute the frozen Base body with per-class callbacks, never global mutation.
    namespace = dict(original.__globals__, initialize_model=initialize_private,
                     process_weights_after_loading=process_private)
    private_base = types.FunctionType(original.__code__, namespace, original.__name__,
                                      original.__defaults__, original.__closure__)
    private_base.__kwdefaults__ = original.__kwdefaults__

    class MixedStoreLoader(base_loader):
        def __init__(self, load_config):
            super().__init__(load_config)
            self.lifecycle_status = self.weights_status = 'not_started'
            self.discard_worker_required = False
            self.native_state = self.native_call_receipt = self.restore_receipt = None

        def download_model(self, model_config):
            raise ValueError('authenticated existing assets only')

        def load_model(self, vllm_config, model_config, prefix=''):
            if self.lifecycle_status != 'not_started' or self.weights_status != 'not_started':
                raise ValueError('one-shot mixed lifecycle already attempted')
            self.lifecycle_status, self.discard_worker_required = 'loading', True
            try:
                _require_runtime_profile(vllm_config, model_config, device,
                                         original.__globals__['current_platform'])
                if model_config is not vllm_config.model_config:
                    raise ValueError('Base and initializer must use the same model config object')
                model = private_base(self, vllm_config, model_config, prefix)
            except BaseException:
                self.lifecycle_status = 'failed'
                raise
            self.lifecycle_status = 'returned'
            return model

        def load_weights(self, model, model_config):
            if self.lifecycle_status != 'loading' or self.weights_status != 'not_started':
                raise ValueError('weight restoration requires active one-shot mixed lifecycle')
            self.weights_status = 'loading'
            try:
                codecs = {VISION_CACHE: CODEC} if VISION_CACHE in dict(
                    model.named_buffers(remove_duplicate=False)) else None
                auxiliary = normalize_rank_mixed_auxiliary(model, device=device,
                    checkpoint_contract=checkpoint_contract, buffer_codecs=codecs,
                    rank=options['rank'], profile=profile, config_raw=config_raw,
                    vllm_version=vllm_version, phase='target_initialized_before_restore')
                devices = mixed_state_devices(model, device)
                try:
                    self.native_state, self.native_call_receipt = load_native_records(native_module, **options)
                except NativeCallFailure as error:
                    self.native_state, self.native_call_receipt = error.native_state, error.receipt
                    raise
                if self.native_state.device != device:
                    raise ValueError('native result must occupy the final CUDA device')
                self.restore_receipt = install_native_state(directory, model, self.native_state.tensors,
                    options['expected_sidecar_sha256'], rank=options['rank'],
                    allocation=self.native_call_receipt['allocation'], checkpoint_contract=checkpoint_contract,
                    destination_auxiliary_receipts=auxiliary, independent_records=self.native_state,
                    target_device_map=devices)
                self.native_state = None  # Do not retain all raw records during repacking.
                self.weights_status = 'installed'
            except BaseException:
                self.weights_status = 'failed'
                raise

    return MixedStoreLoader
