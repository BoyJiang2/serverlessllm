"""Export at the pre-postprocess weight boundary; no worker/service launcher."""
import copy
from pathlib import Path

from buffer_codec import CODEC, VISION_CACHE
from checkpoint_contract import load_targets
from checkpoint_export import convert_checkpoint_rank, DEFAULT_SHA, _checkpoint_manifest
from contract import validate_config
from marlin_aux import PROFILE
from stream_writer import CHUNK_BYTES
from vllm_cpu_hook import _verify_function, BASE_SHA256, UTILS_SHA256
from vllm_native_hook import _require_rank_device, _require_runtime_profile


def current_rank_context():
    import torch
    if not torch.cuda.is_initialized():
        raise ValueError('CUDA worker context must already be initialized')
    from vllm.distributed import (get_pp_group, get_tensor_model_parallel_rank,
                                  get_tensor_model_parallel_world_size)
    return dict(rank=get_tensor_model_parallel_rank(), tp_size=get_tensor_model_parallel_world_size(),
                pp_size=get_pp_group().world_size, device_id=torch.cuda.current_device())


def make_checkpoint_export_loader(base_loader, default_loader, *, checkpoint_path, output_root,
        config_raw, checkpoint_contract, profile, vllm_version, torch_version, rank_context=current_rank_context,
        chunk_bytes=CHUNK_BYTES):
    """rank_context reads established TP/PP groups and this worker's CUDA device.

    The parent must admit a fresh isolated worker before constructing an engine.
    Registration, distributed startup, deadlines and cleanup are not performed.
    """
    validate_config(config_raw)
    load_targets(checkpoint_contract)
    if (profile != PROFILE or vllm_version != '0.19.1'
            or torch_version not in ('2.10.0', '2.10.0+cu129')
            or type(chunk_bytes) is not int or not 4 <= chunk_bytes <= CHUNK_BYTES
            or not callable(rank_context)):
        raise ValueError('fixed export profile, ABI, rank context and host bound required')
    root, names, shards = _checkpoint_manifest(checkpoint_path, config_raw)
    del names, shards
    outputs = Path(output_root).resolve(strict=True)
    if not outputs.is_dir() or outputs == root or outputs.is_relative_to(root):
        raise ValueError('separate existing run output root required')
    original = _verify_function(base_loader.load_model, BASE_SHA256, 'BaseModelLoader')
    _verify_function(original.__globals__['process_weights_after_loading'], UTILS_SHA256)
    _verify_function(default_loader.load_weights, DEFAULT_SHA, 'DefaultModelLoader')
    platform = original.__globals__['current_platform']

    class CheckpointExportLoader(base_loader):
        def __init__(self, load_config):
            super().__init__(load_config)
            self.lifecycle_status = 'not_started'
            self.weights_status = 'not_started'
            self.conversion_receipt = None
            self.discard_worker_required = False
            self.rank = self.device = None

        def download_model(self, model_config):
            raise ValueError('export candidate only consumes authenticated existing local assets')

        def load_model(self, vllm_config, model_config, prefix=''):
            if self.lifecycle_status != 'not_started' or self.weights_status != 'not_started':
                raise ValueError('one-shot export lifecycle already attempted')
            self.lifecycle_status = 'loading'
            self.discard_worker_required = True
            try:
                context = rank_context()
                if (not isinstance(context, dict) or set(context) != {'rank', 'tp_size', 'pp_size', 'device_id'}
                        or any(type(v) is not int for v in context.values())
                        or context['rank'] not in range(8) or context['device_id'] not in range(8)
                        or context['tp_size'] != 8 or context['pp_size'] != 1):
                    raise ValueError('actual TP8/PP1 rank context required before initialization')
                self.rank, self.device = context['rank'], 'cuda:' + str(context['device_id'])
                _require_runtime_profile(vllm_config, model_config, self.device, platform)
                if (outputs / ('rank-' + str(self.rank))).exists():
                    raise FileExistsError('rank archive already exists before initialization')
                model = super().load_model(vllm_config, model_config, prefix)
            except BaseException:
                self.lifecycle_status = 'failed'
                raise
            self.lifecycle_status = 'returned'  # Reference backend only, not full SLLM service.
            return model

        def load_weights(self, model, model_config):
            if self.lifecycle_status != 'loading' or self.weights_status != 'not_started':
                raise ValueError('export weights require the active one-shot Base lifecycle')
            self.weights_status = 'loading'
            try:
                _require_rank_device(model, self.device)
                source_config = copy.copy(self.load_config)
                source_config.load_format = 'safetensors'
                source_config.model_loader_extra_config = dict(self.load_config.model_loader_extra_config)
                source = default_loader(source_config)
                self.conversion_receipt = convert_checkpoint_rank(outputs / ('rank-' + str(self.rank)),
                    source, model, model_config, checkpoint_path=root, config_raw=config_raw,
                    checkpoint_contract=checkpoint_contract, device=self.device, rank=self.rank,
                    profile=profile, vllm_version=vllm_version, torch_version=torch_version,
                    chunk_bytes=chunk_bytes, buffer_codecs={VISION_CACHE: CODEC} if VISION_CACHE in dict(
                        model.named_buffers(remove_duplicate=False)) else None)
                self.weights_status = 'exported'
            except BaseException:
                self.weights_status = 'failed'
                raise

    return CheckpointExportLoader
