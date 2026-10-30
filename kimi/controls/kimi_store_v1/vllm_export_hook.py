"""Export at the pre-postprocess weight boundary; no worker/service launcher."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time

from buffer_codec import CODEC, VISION_CACHE
from checkpoint_contract import load_targets
from checkpoint_export import convert_checkpoint_rank, DEFAULT_SHA, _checkpoint_manifest
from contract import validate_config
from marlin_aux import PROFILE
from native_index import _publish
from stream_writer import CHUNK_BYTES
from vllm_cpu_hook import _verify_function, BASE_SHA256, UTILS_SHA256
from vllm_native_hook import _require_rank_device, _require_runtime_profile


def _source_identity(identity):
    patterns = dict(run_id='kimi-export-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',
        holder='[0-9a-f]{32}', code_commit='[0-9a-f]{40}',
        spec_sha256='[0-9a-f]{64}', source_manifest_sha256='[0-9a-f]{64}')
    if (not isinstance(identity, dict) or set(identity) != set(patterns)
            or any(not isinstance(identity[k], str) or not re.fullmatch(p, identity[k])
                   for k, p in patterns.items())):
        raise ValueError('exact source run identity required')
    return dict(identity)


def _metadata_stream(path):
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular metadata file required')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def _metadata_sha(path):
    digest = hashlib.sha256()
    size = 0
    with _metadata_stream(path) as stream:
        while block := stream.read(65536):
            size += len(block)
            if size > 16 * 1024**2:
                raise ValueError('rank metadata exceeds 16MiB limit')
            digest.update(block)
    return digest.hexdigest()


def collect_source_rank_receipts(output_root, identity):
    """Metadata collection only; does not revalidate payload or admit runtime."""
    identity = _source_identity(identity)
    root, receipts = Path(output_root).resolve(strict=True), []
    for rank in range(8):
        directory = root / ('rank-' + str(rank))
        path = directory / 'source-complete.json'
        if (directory.is_symlink() or not directory.is_dir() or path.is_symlink()
                or any((directory / (name + '.partial')).exists() for name in (
                    'source-complete.json', 'tensor_index.json', 'kimi_raw_state.json', 'config.json'))):
            raise ValueError('regular owned rank receipt directory required')
        with _metadata_stream(path) as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('rank receipt exceeds metadata limit')
        row = json.loads(raw)
        expected_keys = set(identity) | {'schema', 'rank', 'device_id', 'status', 'sidecar_sha256',
            'index_sha256', 'load_model_elapsed_seconds', 'runtime_admitted', 'tp_mapping_verified'}
        if (not isinstance(row, dict) or set(row) != expected_keys
                or any(row[k] != v for k, v in identity.items())
                or row['schema'] != 'kimi-source-rank-completion-v1'
                or type(row['rank']) is not int or row['rank'] != rank
                or type(row['device_id']) is not int or row['device_id'] not in range(8)
                or row['status'] != 'base_load_model_returned'
                or type(row['load_model_elapsed_seconds']) not in (int, float)
                or not math.isfinite(row['load_model_elapsed_seconds'])
                or row['load_model_elapsed_seconds'] < 0
                or row['runtime_admitted'] is not False or row['tp_mapping_verified'] is not False):
            raise ValueError('rank completion identity or scope differs')
        for field, name in (('sidecar_sha256', 'kimi_raw_state.json'), ('index_sha256', 'tensor_index.json')):
            target = directory / name
            if (target.is_symlink() or not target.is_file()
                    or row[field] != _metadata_sha(target)):
                raise ValueError('rank completion metadata hash differs')
        receipts.append(row)
    if len({row['device_id'] for row in receipts}) != 8:
        raise ValueError('eight distinct rank devices required')
    return dict(ranks=receipts, completed_rank_count=8, metadata_verified=True,
                checkpoint_payload_verified=False, tp_mapping_verified=False,
                runtime_admitted=False, native_loader_verified=False)


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
        chunk_bytes=CHUNK_BYTES, run_identity=None):
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
    identity = None if run_identity is None else _source_identity(run_identity)
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
            started = time.monotonic()
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
                if identity is not None:
                    directory = outputs / ('rank-' + str(self.rank))
                    if self.conversion_receipt.get('cleanup_pending'):
                        raise ValueError('archive metadata partial cleanup pending')
                    index_sha = _metadata_sha(directory / 'tensor_index.json')
                    if index_sha != self.conversion_receipt['native_index_sha256']:
                        raise ValueError('exported index changed before Base returned')
                    row = dict(identity, schema='kimi-source-rank-completion-v1', rank=self.rank,
                        device_id=context['device_id'], status='base_load_model_returned',
                        sidecar_sha256=_metadata_sha(directory / 'kimi_raw_state.json'),
                        index_sha256=index_sha, load_model_elapsed_seconds=time.monotonic()-started,
                        runtime_admitted=False, tp_mapping_verified=False)
                    if not _publish(directory, 'source-complete.json', json.dumps(row).encode('utf-8')):
                        raise OSError('rank completion published with partial cleanup pending')
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
