"""Official local SF -> loaded rank -> streamed archive; no model construction."""
import hashlib
import json
from pathlib import Path

from checkpoint_contract import INDEX_SHA256, load_targets
from contract import REVISION, validate_config, plan_rank
from device_export import export_rank_device
from device_state import encode_device_state
from marlin_aux import PROFILE
from stream_writer import CHUNK_BYTES
from vllm_cpu_hook import _verify_function

DEFAULT_SHA = 'e7252bc755341559c568bcc933225bd8ca855124fb553059b430000174a9feaa'
KIMI_SHA = '72541f4debfb3d9642884d4e4d86dccecbec0ddf187727d7e96a4639c74f69ba'


def _checkpoint_manifest(path, config_raw):
    root = Path(path).resolve(strict=True)
    validate_config(config_raw)
    if (root / 'config.json').read_bytes() != config_raw:
        raise ValueError('checkpoint config differs')
    raw = (root / 'model.safetensors.index.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != INDEX_SHA256:
        raise ValueError('checkpoint index differs from frozen official bytes')
    weights = json.loads(raw)['weight_map']
    files = sorted(set(weights.values()))
    if len(weights) != 208550 or len(files) != 64:
        raise ValueError('incomplete official checkpoint manifest')
    paths = []
    for name in files:
        path = (root / name).resolve(strict=True)
        if path.parent != root or not name.endswith('.safetensors') or not path.is_file():
            raise ValueError('nonlocal or invalid checkpoint shard')
        paths.append(str(path))
    return root, set(weights), paths


def load_official_rank(loader, model, model_config, *, checkpoint_path, config_raw, checkpoint_contract):
    """Use the pinned Default/Kimi loaders with local-only files and coverage audit.

    No postprocessing. Only this loader/model instance is intercepted, then its
    original attributes are restored. Concurrent use/mutation is unsupported.
    Any load failure invalidates this rank; do not retry it for conversion.
    """
    if hasattr(model, '_kimi_source_export_status'):
        raise ValueError('one-shot checkpoint source already attempted')
    root, source_names, shards = _checkpoint_manifest(checkpoint_path, config_raw)
    targets = load_targets(checkpoint_contract)
    if (Path(model_config.model).resolve() != root or model_config.revision != REVISION
            or model_config.quantization != 'compressed-tensors'
            or getattr(model, 'secondary_weights', ())):
        raise ValueError('frozen primary local compressed-tensors source required')
    config = loader.load_config
    if (config.load_format != 'safetensors' or config.safetensors_load_strategy != 'lazy'
            or config.model_loader_extra_config.get('enable_multithread_load', False) is not False):
        raise ValueError('single-thread lazy safetensors loading required')
    for name in ('load_weights', 'get_all_weights', '_get_weights_iterator', '_init_ep_weight_filter'):
        _verify_function(getattr(loader, name), DEFAULT_SHA, 'DefaultModelLoader')
    original_load = model.load_weights
    _verify_function(original_load, KIMI_SHA, 'KimiK25ForConditionalGeneration')
    if 'load_weights' in vars(model) or '_prepare_weights' in vars(loader):
        raise ValueError('source loader/model has an instance override')
    pending, seen, loaded, requests = set(source_names), set(), None, 0
    model._kimi_source_export_status = 'loading'

    def local_files(model_name_or_path, subfolder, revision, fall_back_to_pt, allow_patterns_overrides):
        nonlocal requests
        if (Path(model_name_or_path).resolve() != root or subfolder is not None
                or revision != REVISION or allow_patterns_overrides is not None or requests):
            raise ValueError('unexpected source or repeated checkpoint preparation')
        requests += 1
        return str(root), list(shards), True

    def audit_model_load(weights):
        nonlocal loaded
        if loaded is not None:
            raise ValueError('repeated model weight load')
        def tracked():
            for name, tensor in weights:
                if name not in pending:
                    raise ValueError('unknown or duplicate source tensor: ' + name)
                pending.remove(name)
                seen.add(name)
                yield name, tensor
                del tensor
        loaded = original_load(tracked())
        if not isinstance(loaded, set) or not targets <= loaded:
            raise ValueError('incomplete checkpoint-backed target loading')
        return loaded

    try:
        loader._prepare_weights = local_files
        model.load_weights = audit_model_load
        loader.load_weights(model, model_config)
        if pending or requests != 1 or loaded is None:
            raise ValueError('checkpoint iterator not fully consumed')
        model._kimi_source_export_status = 'checkpoint_loaded'
        return dict(source_index_sha256=INDEX_SHA256, source_name_count=len(seen), shard_count=len(shards),
                    loaded_target_count=len(loaded), source_names_verified=True, target_names_verified=True,
                    checkpoint_payload_verified=False, tp_mapping_verified=False,
                    postprocessing_performed=False, native_loader_verified=False, runtime_admitted=False)
    except BaseException:
        model._kimi_source_export_status = 'failed'
        raise
    finally:
        vars(loader).pop('_prepare_weights', None)
        vars(model).pop('load_weights', None)


def convert_checkpoint_rank(directory, loader, model, model_config, *, checkpoint_path, config_raw,
                            checkpoint_contract, device, rank, profile, vllm_version, torch_version,
                            chunk_bytes=CHUNK_BYTES, buffer_codecs=None):
    """One component conversion; independent admitted worker supplies model/loader."""
    if Path(directory).exists():
        raise FileExistsError('conversion output already exists')
    if profile != PROFILE or type(chunk_bytes) is not int or not 4 <= chunk_bytes <= CHUNK_BYTES:
        raise ValueError('reviewed conversion profile and host bound required')
    # Validate metadata/ABI/budget without reading payload before SF loading.
    named, layouts, _ = encode_device_state(model, device=device, buffer_codecs=buffer_codecs)
    plan_rank(config_raw, rank=rank, tensor_parallel_size=8, revision=REVISION,
        tensors={n: dict(shape=r['shape'], dtype=r['dtype'], bytes=r['storage_bytes'], contiguous=True)
                 for n, r in layouts.items()},
        vllm_version=vllm_version, torch_version=torch_version, chunk_bytes=chunk_bytes)
    del named, layouts
    try:
        source = load_official_rank(loader, model, model_config, checkpoint_path=checkpoint_path,
            config_raw=config_raw, checkpoint_contract=checkpoint_contract)
        model._kimi_source_export_status = 'exporting'
        archive = export_rank_device(directory, model, device=device, config_raw=config_raw,
            checkpoint_contract=checkpoint_contract, rank=rank, profile=profile,
            vllm_version=vllm_version, torch_version=torch_version,
            phase='checkpoint_loaded_before_postprocess', chunk_bytes=chunk_bytes, buffer_codecs=buffer_codecs)
        model._kimi_source_export_status = 'exported'
        return dict(source=source, **archive)
    except BaseException:
        model._kimi_source_export_status = 'failed'
        raise
