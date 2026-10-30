"""Private source-export plugin. Registration alone never admits computation."""
import hashlib
import json
import os
from pathlib import Path
import re

SPEC_ENV = 'SLLM_KIMI_SOURCE_SPEC'
SHA_ENV = 'SLLM_KIMI_SOURCE_SPEC_SHA256'
_registration = None


def _load_spec(path, expected_sha):
    from contract import REVISION, validate_config
    from checkpoint_contract import load_targets
    from stream_writer import CHUNK_BYTES
    if not re.fullmatch('[0-9a-f]{64}', expected_sha):
        raise ValueError('explicit spec SHA256 required')
    source = Path(path)
    if not source.is_absolute():
        raise ValueError('bounded absolute spec path required')
    with source.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('bounded source spec exceeds 64KiB')
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('source plugin spec hash differs')
    spec = json.loads(raw)
    keys = {'schema', 'node', 'mode', 'revision', 'run_id', 'holder', 'code_commit',
            'checkpoint_path', 'run_root', 'output_root', 'checkpoint_contract_path', 'chunk_bytes'}
    if (set(spec) != keys or spec['schema'] != 'kimi-source-worker-plugin-v1'
            or spec['node'] != 'qjhs003' or spec['mode'] != 'export' or spec['revision'] != REVISION
            or not re.fullmatch('kimi-export-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', spec['run_id'])
            or not re.fullmatch('[0-9a-f]{32}', spec['holder'])
            or not re.fullmatch('[0-9a-f]{40}', spec['code_commit'])
            or type(spec['chunk_bytes']) is not int or not 4 <= spec['chunk_bytes'] <= CHUNK_BYTES):
        raise ValueError('fixed source identity, schema and byte bound required')
    for key in ('checkpoint_path', 'run_root', 'output_root', 'checkpoint_contract_path'):
        value = Path(spec[key])
        if not value.is_absolute():
            raise ValueError('absolute worker paths required')
        spec[key] = value.resolve(strict=True)
    checkpoint, run, output = (spec[k] for k in ('checkpoint_path', 'run_root', 'output_root'))
    contract = spec['checkpoint_contract_path']
    if (not all(p.is_dir() for p in (checkpoint, run, output)) or output == run
            or not output.is_relative_to(run) or not contract.is_relative_to(run)
            or not source.resolve().is_relative_to(run)
            or checkpoint.is_relative_to(run) or run.is_relative_to(checkpoint)):
        raise ValueError('isolated run paths and separate read-only checkpoint required')
    config_raw = (checkpoint / 'config.json').read_bytes()
    contract_raw = contract.read_bytes()
    validate_config(config_raw)
    load_targets(contract_raw)
    return spec, config_raw, contract_raw


def _runtime():
    import torch
    import vllm
    if (vllm.__version__ != '0.19.1' or str(torch.__version__) != '2.10.0+cu129'
            or torch.version.cuda != '12.9'):
        raise ValueError('source worker requires exact vLLM and CUDA PyTorch ABI')
    from vllm.model_executor import model_loader as registry
    from vllm.model_executor.model_loader.base_loader import BaseModelLoader
    from vllm.model_executor.model_loader.default_loader import DefaultModelLoader
    return registry, BaseModelLoader, DefaultModelLoader


def register():
    global _registration
    path, digest = os.environ.get(SPEC_ENV), os.environ.get(SHA_ENV)
    if path is None and digest is None:
        return None
    if not path or not digest:
        raise ValueError('source plugin needs both spec path and SHA')
    spec, config_raw, contract_raw = _load_spec(path, digest)
    registry, base, default = _runtime()
    from loader_registry import FORMATS, register_candidate_loader
    from marlin_aux import PROFILE
    from vllm_export_hook import make_checkpoint_export_loader
    if _registration is not None:
        previous_sha, previous_registry, cls = _registration
        if (previous_sha != digest or previous_registry is not registry
                or registry._LOAD_FORMAT_TO_MODEL_LOADER.get(FORMATS['export']) is not cls):
            raise ValueError('source registration identity changed within worker')
        return FORMATS['export']
    cls = make_checkpoint_export_loader(base, default, checkpoint_path=spec['checkpoint_path'],
        output_root=spec['output_root'], config_raw=config_raw, checkpoint_contract=contract_raw,
        profile=PROFILE, vllm_version='0.19.1', torch_version='2.10.0+cu129',
        chunk_bytes=spec['chunk_bytes'])
    name = register_candidate_loader(registry, cls, mode='export')
    _registration = digest, registry, cls
    return name


def materialize_plugin_metadata(directory):
    """Create only dedicated distribution metadata; never install globally."""
    parent = Path(directory).resolve(strict=True)
    target = parent / 'serverlessllm_kimi_source_candidate-0.1.0.dist-info'
    target.mkdir()  # Preserve partial evidence and refuse overwrite/retry.
    for name, contents in {
        'METADATA': 'Metadata-Version: 2.1\nName: serverlessllm-kimi-source-candidate\nVersion: 0.1.0\n',
        'entry_points.txt': '[vllm.general_plugins]\nsllm_kimi_source_candidate = source_plugin:register\n',
    }.items():
        with (target / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(contents)
    return target
