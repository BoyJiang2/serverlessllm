"""Source-bound CPU lifecycle candidate; not a GPU/native Store loader plugin."""
import hashlib
import inspect
from pathlib import Path
import types

from checkpoint_contract import load_targets
from contract import validate_config
from cpu_restore import restore_cpu_state
from rank_export import normalize_rank_cpu_auxiliary
from marlin_aux import PROFILE

BASE_SHA256 = 'd96582d2bb9772d5990e4257cd09c5fcd9b94905d4defd87bee17136321279df'
UTILS_SHA256 = '66e62df58d8f20e823a16cb042b939183f956df7df23d4b5d8fa6c5ce498ac02'


def _verify_function(function, digest, class_name=None):
    function = inspect.unwrap(function)
    path = Path(inspect.getsourcefile(function))
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('vLLM lifecycle source differs')
    code = compile(raw, str(path), 'exec', dont_inherit=True)
    if class_name:
        code = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == class_name)
    expected = next(c for c in code.co_consts if isinstance(c, types.CodeType) and c.co_name == function.__name__)
    if function.__code__ != expected:
        raise ValueError('vLLM lifecycle function differs')
    return function


def make_cpu_store_loader(base_loader, *, directory, expected_sidecar_sha256, rank,
                           config_raw, checkpoint_contract, profile, vllm_version):
    """Compose the existing CPU raw restore with the exact v0.19.1 lifecycle.

    No registration/global patch is performed. Actual vLLM/platform imports,
    complete model execution and GPU/native Store admission remain separate.
    """
    validate_config(config_raw)
    load_targets(checkpoint_contract)
    if vllm_version != '0.19.1' or type(rank) is not int or rank not in range(8) or profile != PROFILE:
        raise ValueError('wrong lifecycle version/rank')
    original = _verify_function(base_loader.load_model, BASE_SHA256, 'BaseModelLoader')
    _verify_function(original.__globals__['process_weights_after_loading'], UTILS_SHA256)
    platform = original.__globals__['current_platform']

    class CpuStoreLoader(base_loader):
        def __init__(self, load_config):
            super().__init__(load_config)
            self.weights_status = 'not_started'
            self.lifecycle_status = 'not_started'
            self.restore_receipt = None

        def download_model(self, model_config):
            raise ValueError('this loader consumes existing audited local assets only')

        def load_model(self, vllm_config, model_config, prefix=''):
            import torch
            if self.lifecycle_status != 'not_started' or self.weights_status != 'not_started':
                raise ValueError('one-shot weight hook already attempted before initialization')
            self.lifecycle_status = 'loading'
            try:
                device = vllm_config.load_config.device
                if device is None:
                    device = vllm_config.device_config.device
                if (torch.device(device).type != 'cpu' or not platform.is_cpu()
                        or model_config.quantization != 'compressed-tensors'):
                    raise ValueError('CPU platform/device and offline compressed-tensors required before initialization')
                model = super().load_model(vllm_config, model_config, prefix)
            except BaseException:
                self.lifecycle_status = 'failed'
                raise
            self.lifecycle_status = 'returned'  # Candidate CPU path, not native or serving readiness.
            return model

        def load_weights(self, model, model_config):
            if self.weights_status != 'not_started':
                raise ValueError('one-shot weight hook already attempted')
            self.weights_status = 'restoring'
            try:
                if (model_config.quantization != 'compressed-tensors' or not platform.is_cpu()
                        or any(t.device.type != 'cpu' for t in list(model.parameters()) + list(model.buffers()))
                        or any(getattr(getattr(m, 'quant_method', None), 'uses_meta_device', False) for m in model.modules())):
                    raise ValueError('CPU/offline weight hook scope violated')
                receipts = normalize_rank_cpu_auxiliary(model, checkpoint_contract=checkpoint_contract,
                    rank=rank, config_raw=config_raw, profile=profile, vllm_version=vllm_version,
                    phase='target_initialized_before_restore')
                self.restore_receipt = restore_cpu_state(directory, model, expected_sidecar_sha256,
                    rank=rank, checkpoint_contract=checkpoint_contract,
                    destination_auxiliary_receipts=receipts)
                self.weights_status = 'installed'  # Postprocessing still belongs to BaseModelLoader.
            except BaseException:
                self.weights_status = 'failed'
                raise

    return CpuStoreLoader
