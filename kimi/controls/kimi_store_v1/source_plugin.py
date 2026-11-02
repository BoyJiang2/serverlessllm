"""Private source-export plugin. Registration alone never admits computation."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
import traceback
from datetime import datetime, timezone

SPEC_ENV = 'SLLM_KIMI_SOURCE_SPEC'
SHA_ENV = 'SLLM_KIMI_SOURCE_SPEC_SHA256'
BUNDLE_SHA_ENV = 'SLLM_KIMI_SOURCE_BUNDLE_SHA256'
_registration = None
NATIVE_ENV = ('SLLM_KIMI_NATIVE_SPEC', 'SLLM_KIMI_NATIVE_SPEC_SHA256', 'SLLM_KIMI_NATIVE_BUNDLE_SHA256')
_native_registration = None


def _load_native_spec(path, expected_sha):
    from contract import REVISION, validate_config
    from checkpoint_contract import load_targets, read_checkpoint_metadata
    from native_call import _store_directory
    source = Path(path)
    if (not source.is_absolute() or not isinstance(expected_sha, str)
            or not re.fullmatch('[0-9a-f]{64}', expected_sha)):
        raise ValueError('absolute native spec path and external SHA required')
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_BINARY', 0)
                 | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('regular native spec required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(65537)
    finally:
        os.close(fd)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha:
        raise ValueError('native spec length or external SHA differs')
    spec = json.loads(raw)
    keys = {'schema', 'node', 'mode', 'revision', 'run_id', 'holder', 'code_commit',
            'run_root', 'config_path', 'checkpoint_contract_path', 'rank_options', 'device_memory_budgets'}
    if (not isinstance(spec, dict) or set(spec) != keys
            or spec['schema'] != 'kimi-native-worker-plugin-v1' or spec['node'] != 'qjhs003'
            or spec['mode'] != 'native' or spec['revision'] != REVISION
            or any(not isinstance(spec[k], str) or not re.fullmatch(pattern, spec[k]) for k, pattern in (
                ('run_id', 'kimi-native-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}'),
                ('holder', '[0-9a-f]{32}'), ('code_commit', '[0-9a-f]{40}')))):
        raise ValueError('independent native schema and identity required')
    for key in ('run_root', 'config_path', 'checkpoint_contract_path'):
        if not isinstance(spec[key], str) or not Path(spec[key]).is_absolute():
            raise ValueError('absolute native run metadata paths required')
        spec[key] = Path(spec[key]).resolve(strict=True)
    root = spec['run_root']
    if (not root.is_dir() or source.resolve(strict=True) != root / 'native-spec.json'
            or any(not spec[k].is_relative_to(root) for k in ('config_path', 'checkpoint_contract_path'))):
        raise ValueError('native metadata belongs to another run layout')
    rows, budgets = spec['rank_options'], spec['device_memory_budgets']
    strings = {'storage_path', 'model_path', 'expected_sidecar_sha256', 'server_address',
               'build_receipt', 'expected_build_sha256'}
    integers = {'max_allocation_bytes', 'timeout_seconds'}
    if (not isinstance(rows, list) or len(rows) != 8 or not isinstance(budgets, list) or len(budgets) != 8
            or any(type(n) is not int or n <= 0 for n in budgets)
            or any(not isinstance(row, dict) or set(row) != strings | integers
                or any(type(row[k]) is not str for k in strings)
                or any(type(row[k]) is not int or row[k] <= 0 for k in integers)
                for row in rows)):
        raise ValueError('eight plain metadata rank options and device budgets required')
    directories = set()
    for row in rows:
        storage = Path(row['storage_path'])
        build = Path(row['build_receipt'])
        if (not storage.is_absolute() or not build.is_absolute()
                or not build.resolve(strict=True).is_relative_to(root)
                or any(not re.fullmatch('[0-9a-f]{64}', row[k]) for k in
                       ('expected_sidecar_sha256', 'expected_build_sha256'))):
            raise ValueError('native asset paths and external metadata SHA required')
        storage, directory = _store_directory(storage, row['model_path'])
        endpoint = re.fullmatch(r'127\.0\.0\.1:([0-9]{1,5})', row['server_address'])
        if endpoint is None or not 1 <= int(endpoint[1]) <= 65535:
            raise ValueError('native Store endpoint must remain on this node loopback')
        if (not storage.is_dir() or not directory.is_dir() or not build.is_file()
                or storage.is_relative_to(root) or root.is_relative_to(storage) or directory in directories):
            raise ValueError('eight distinct read-only native assets separate from run required')
        directories.add(directory)
    config = read_checkpoint_metadata(spec['config_path'], 'config')
    contract = read_checkpoint_metadata(spec['checkpoint_contract_path'], 'targets')
    validate_config(config)
    load_targets(contract)
    return spec, config, contract


def _native_runtime():
    registry, base, _ = _runtime()
    from sllm_store import torch as native
    from vllm.model_executor.models.kimi_k25 import KimiK25ForConditionalGeneration
    return registry, base, native, KimiK25ForConditionalGeneration


def register_native():
    global _native_registration
    values = tuple(os.environ.get(k) for k in NATIVE_ENV)
    if all(v is None for v in values):
        return None
    if not all(values) or any(os.environ.get(k) is not None for k in (SPEC_ENV, SHA_ENV, BUNDLE_SHA_ENV)):
        raise ValueError('complete independent native environment without source activation required')
    path, digest, bundle_digest = values
    spec, config, contract = _load_native_spec(path, digest)
    from source_bundle import verify_native_run
    verify_native_run(Path(__file__).resolve().parent, spec['run_root'] / 'native-code-manifest.json',
                      bundle_digest, path, digest)
    registry, base, native, model_class = _native_runtime()
    from loader_registry import FORMATS
    if _native_registration is not None:
        previous_sha, previous_bundle, previous_registry, cls = _native_registration
        if (previous_sha != digest or previous_bundle != bundle_digest or previous_registry is not registry
                or registry._LOAD_FORMAT_TO_MODEL_LOADER.get(FORMATS['native']) is not cls):
            raise ValueError('native registration identity changed within worker')
        return FORMATS['native']
    from marlin_aux import PROFILE
    from vllm_mixed_hook import register_deferred_mixed_loader
    cls = register_deferred_mixed_loader(registry, base, native, model_class,
        rank_options=spec['rank_options'], device_memory_budgets=spec['device_memory_budgets'],
        config_raw=config, checkpoint_contract=contract, profile=PROFILE, vllm_version='0.19.1')
    _native_registration = digest, bundle_digest, registry, cls
    return FORMATS['native']


def _load_spec(path, expected_sha):
    from contract import REVISION, validate_config
    from checkpoint_contract import load_targets, read_checkpoint_metadata
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
            or spec['node'] != 'qjhs003' or spec['mode'] not in ('reference','export') or spec['revision'] != REVISION
            or not re.fullmatch('kimi-export-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', spec['run_id'])
            or not re.fullmatch('[0-9a-f]{32}', spec['holder'])
            or not re.fullmatch('[0-9a-f]{40}', spec['code_commit'])
            or type(spec['chunk_bytes']) is not int or not 4 <= spec['chunk_bytes'] <= CHUNK_BYTES):
        raise ValueError('fixed source identity, schema and byte bound required')
    if spec['mode']=='reference' and spec['output_root'] is not None:
        raise ValueError('reference-only mode must not declare a Store output')
    paths=('checkpoint_path','run_root','checkpoint_contract_path')
    if spec['mode']=='export':paths+=('output_root',)
    for key in paths:
        value = Path(spec[key])
        if not value.is_absolute():
            raise ValueError('absolute worker paths required')
        spec[key] = value.resolve(strict=True)
    checkpoint, run, output = (spec[k] for k in ('checkpoint_path', 'run_root', 'output_root'))
    contract = spec['checkpoint_contract_path']
    if (not all(p.is_dir() for p in (checkpoint, run))
            or (output is not None and (not output.is_dir() or output == run or not output.is_relative_to(run)))
            or not contract.is_relative_to(run)
            or not source.resolve().is_relative_to(run)
            or checkpoint.is_relative_to(run) or run.is_relative_to(checkpoint)):
        raise ValueError('isolated run paths and separate read-only checkpoint required')
    config_raw = read_checkpoint_metadata(checkpoint / 'config.json', 'config')
    contract_raw = read_checkpoint_metadata(contract, 'targets')
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


def _verify_code_bundle(spec, path, digest, bundle_digest):
    from source_bundle import verify_source_run
    return verify_source_run(Path(__file__).resolve().parent,
        spec['run_root'] / 'source-code-manifest.json', bundle_digest, path, digest)


def register():
    global _registration
    path, digest = os.environ.get(SPEC_ENV), os.environ.get(SHA_ENV)
    bundle_digest = os.environ.get(BUNDLE_SHA_ENV)
    if path is None and digest is None and bundle_digest is None:
        return None
    if any(os.environ.get(k) is not None for k in NATIVE_ENV):
        raise ValueError('source and native activation must be separate')
    if not path or not digest or not bundle_digest:
        raise ValueError('source plugin needs both spec path/SHA and bundle SHA')
    spec, config_raw, contract_raw = _load_spec(path, digest)
    _verify_code_bundle(spec, path, digest, bundle_digest)
    registry, base, default = _runtime()
    if spec['mode']=='reference':
        if _registration is not None:
            raise ValueError('reference-only worker cannot reuse an export registration')
        return 'safetensors'
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
        chunk_bytes=spec['chunk_bytes'], run_identity=dict(
            run_id=spec['run_id'], holder=spec['holder'], code_commit=spec['code_commit'],
            spec_sha256=digest, source_manifest_sha256=bundle_digest))
    name = register_candidate_loader(registry, cls, mode='export')
    _registration = digest, registry, cls
    return name


def materialize_plugin_metadata(directory, *, mode='export'):
    """Create only dedicated distribution metadata; never install globally."""
    parent = Path(directory).resolve(strict=True)
    if mode not in ('export', 'native'):
        raise ValueError('explicit private plugin mode required')
    label = 'source' if mode == 'export' else 'native'
    target = parent / ('serverlessllm_kimi_' + label + '_candidate-0.1.0.dist-info')
    target.mkdir()  # Preserve partial evidence and refuse overwrite/retry.
    for name, contents in {
        'METADATA': 'Metadata-Version: 2.1\nName: serverlessllm-kimi-' + label + '-candidate\nVersion: 0.1.0\n',
        'entry_points.txt': '[vllm.general_plugins]\nsllm_kimi_' + label + '_candidate = source_plugin:'
                            + ('register' if mode == 'export' else 'register_native') + '\n',
    }.items():
        with (target / name).open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(contents)
    return target


def _reference_output(requests, prompt, *, fixed):
    """Preserve raw IDs and sampled-token logprobs; not a frozen gold verifier."""
    if (not isinstance(requests, list) or len(requests) != 1
            or requests[0].finished is not True or list(requests[0].prompt_token_ids) != prompt
            or len(requests[0].outputs) != 1):
        raise ValueError('one completed reference request with unchanged prompt required')
    output = requests[0].outputs[0]
    tokens = list(output.token_ids)
    if (not tokens or len(tokens) > (32 if fixed else 128)
            or any(type(t) is not int or not 0 <= t < 163840 for t in tokens)
            or len(set(tokens)) < 2 or not isinstance(output.text, str) or not output.text.strip()
            or (fixed and (len(tokens) != 32 or output.finish_reason != 'length'))
            or (not fixed and output.finish_reason != 'stop')):
        raise ValueError('finite nondegenerate fixed32 or natural-stop reference required')
    if output.logprobs is None or len(output.logprobs) != len(tokens):
        raise ValueError('sampled-token logprobs required for every raw token')
    values = []
    for token, row in zip(tokens, output.logprobs):
        value = row[token].logprob
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('nonfinite sampled-token logprob')
        values.append(value)
    return dict(token_ids=tokens, text=output.text, sampled_logprobs=values,
                finish_reason=output.finish_reason, stop_reason=output.stop_reason,
                gold_verified=False)


def _source_reference_body(engine_factory, sampling_factory, *, spec, identity, prompt_token_ids,
                          kv_cache_memory_bytes, schedule, entered, started):
    """Internal worker body only; caller must first establish host guard/admission.

    Factories are vLLM.LLM and SamplingParams in a real admitted worker. No CLI
    invokes this candidate; local tests supply explicit CPU-only stand-ins.
    """
    from contract import REVISION
    from loader_registry import FORMATS
    from source_schedule import phase_remaining_seconds
    from vllm_export_hook import collect_source_rank_receipts, _source_identity
    identity = _source_identity(identity)
    if spec['mode'] not in ('reference','export'):
        raise ValueError('explicit reference or export mode required')
    exporting=spec['mode']=='export'
    if any(identity[k] != spec[k] for k in ('run_id', 'holder', 'code_commit')):
        raise ValueError('worker spec and reference identity differ')
    if (not isinstance(prompt_token_ids, list) or not 1 <= len(prompt_token_ids) <= 896
            or any(type(t) is not int or not 0 <= t < 163840 for t in prompt_token_ids)
            or type(kv_cache_memory_bytes) is not int or kv_cache_memory_bytes <= 0):
        raise ValueError('bounded raw prompt and explicit reviewed KV byte budget required')
    def remaining(phase):
        return phase_remaining_seconds(schedule, phase, entered=entered,
            elapsed=time.monotonic()-started, now=datetime.now(timezone.utc))
    options = dict(model=str(spec['checkpoint_path']), tokenizer=str(spec['checkpoint_path']),
        revision=REVISION, tokenizer_revision=REVISION, trust_remote_code=True,
        dtype='bfloat16', quantization='compressed-tensors', load_format=FORMATS['export'] if exporting else 'safetensors',
        tensor_parallel_size=8, pipeline_parallel_size=1, data_parallel_size=1,
        prefill_context_parallel_size=1, distributed_executor_backend='mp',
        enable_expert_parallel=False, speculative_config=None,
        moe_backend='marlin', mm_encoder_tp_mode='data', language_model_only=False,
        skip_mm_profiling=True, mm_processor_cache_gb=0, cpu_offload_gb=0,
        enforce_eager=True, enable_prefix_caching=False, enable_chunked_prefill=False,
        seed=0, max_model_len=1024, max_num_seqs=1, max_num_batched_tokens=1024,
        kv_cache_memory_bytes=kv_cache_memory_bytes, generation_config='vllm',
        safetensors_load_strategy='lazy', model_loader_extra_config={'enable_multithread_load': False})
    engine, failure = None, None
    evidence = dict(identity, prompt_token_ids=list(prompt_token_ids), engine_options=options,
        ranks=None, business={}, engine_shutdown_returned=False, resources_released=False)
    try:
        remaining('engine_initialize_and_export')
        engine = engine_factory(**options)
        ranks = collect_source_rank_receipts(spec['output_root'], identity) if exporting else None
        evidence['ranks'] = ranks
        remaining('engine_initialize_and_export')
        business = evidence['business']
        for name, fixed in (('fixed_token_business', True), ('natural_eos_business', False)):
            remaining(name)
            params = sampling_factory(n=1, temperature=0.0, top_p=1.0, top_k=-1, seed=0,
                max_tokens=32 if fixed else 128, min_tokens=32 if fixed else 0,
                ignore_eos=fixed, logprobs=1)
            requests = engine.generate([{'prompt_token_ids': list(prompt_token_ids)}], params, use_tqdm=False)
            business[name] = _reference_output(requests, prompt_token_ids, fixed=fixed)
            remaining(name)
        result = dict(identity, prompt_token_ids=list(prompt_token_ids), engine_options=options,
            ranks=ranks, business=business, reference_collected=True,export_performed=exporting,
            gold_frozen=False, runtime_admitted=False, native_loader_verified=False,
            full_service_verified=False, resources_released=False)
    except BaseException as error:
        failure = error
        error.source_reference_evidence = evidence
        if engine is None:
            error.__notes__ = list(getattr(error, '__notes__', ())) + [
                'engine constructor did not return; partial processes require host-guard cleanup']
        raise
    finally:
        if engine is not None:
            try:
                # Core-client timeout is advisory; independent host guard is mandatory.
                try:
                    timeout = remaining('worker_shutdown')
                except TimeoutError:
                    timeout = 0.0
                engine.llm_engine.engine_core.shutdown(timeout=timeout)
                evidence['engine_shutdown_returned'] = True
                remaining('worker_shutdown')
            except BaseException as cleanup_error:
                evidence['shutdown_error'] = repr(cleanup_error)
                if failure is None:
                    cleanup_error.source_reference_evidence = evidence
                    raise
                failure.__notes__ = list(getattr(failure, '__notes__', ())) + [
                    'engine shutdown also failed: ' + repr(cleanup_error)]
    result['engine_shutdown_returned'] = True
    return result


def _write_reference_event(stream, identity, event, payload):
    row = dict(identity, schema='kimi-source-reference-events-v1', event=event,
        utc=datetime.now(timezone.utc).isoformat(), monotonic_seconds=time.monotonic(), payload=payload)
    raw = (json.dumps(row, allow_nan=False) + '\n').encode('utf-8')
    if len(raw) > 65536:
        raise ValueError('reference event exceeds 64KiB evidence bound')
    if stream.write(raw) != len(raw):
        raise OSError('short reference evidence write')
    stream.flush()
    os.fsync(stream.fileno())


def _run_source_worker(engine_factory, sampling_factory, *, worker_input_path, worker_input_sha256):
    """Internal admitted-parent API, not a standalone or guard-authorizing CLI."""
    from source_bundle import verify_source_worker_input
    from source_schedule import phase_remaining_seconds, _utc
    entered, started = datetime.now(timezone.utc), time.monotonic()
    root, document, spec, _, schedule, identity = verify_source_worker_input(
        worker_input_path, worker_input_sha256)
    if Path(__file__).resolve(strict=True).parent != root / 'code':
        raise ValueError('must execute the verified source worker module')
    now = datetime.now(timezone.utc)
    if entered < _utc(document['batch_entered_utc']) or now < entered:
        raise TimeoutError('source worker clock regressed or batch has not started')
    # Deadlines derive from the frozen parent anchor, not this worker's startup.
    phase_remaining_seconds(schedule, 'engine_initialize_and_export', entered=entered,
                            elapsed=time.monotonic()-started, now=now)
    return _run_source_reference(engine_factory, sampling_factory, spec=spec, identity=identity,
        prompt_token_ids=document['prompt_token_ids'], kv_cache_memory_bytes=document['kv_cache_memory_bytes'],
        schedule=schedule, entered=entered, started=started, worker_input_sha256=worker_input_sha256)


def _run_source_reference(engine_factory, sampling_factory, *, worker_input_sha256=None, **options):
    """Reserve one existing-run journal before computation; never overwrite it."""
    from vllm_export_hook import _source_identity
    identity = _source_identity(options['identity'])
    if worker_input_sha256 is not None and (not isinstance(worker_input_sha256, str)
            or not re.fullmatch('[0-9a-f]{64}', worker_input_sha256)):
        raise ValueError('external source worker input SHA required')
    spec = options['spec']
    root = Path(spec['run_root']).resolve(strict=True)
    checkpoint = Path(spec['checkpoint_path']).resolve(strict=True)
    output = None if spec['mode']=='reference' and spec['output_root'] is None else Path(spec['output_root']).resolve(strict=True)
    if (not root.is_dir() or (output is not None and (not output.is_dir() or output == root or not output.is_relative_to(root)))
            or root.is_relative_to(checkpoint) or checkpoint.is_relative_to(root)
            or any(identity[k] != spec[k] for k in ('run_id', 'holder', 'code_commit'))):
        raise ValueError('existing isolated run directory required')
    path = root / 'source-reference-events.jsonl'
    with path.open('xb', buffering=0) as stream:
        start = dict(prompt_token_ids=list(options['prompt_token_ids']),
                     kv_cache_memory_bytes=options['kv_cache_memory_bytes'])
        if worker_input_sha256 is not None:
            start['worker_input_sha256'] = worker_input_sha256
        _write_reference_event(stream, identity, 'started', start)
        try:
            result = _source_reference_body(engine_factory, sampling_factory, **options)
        except BaseException as error:
            trace = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
            description = repr(error)
            payload = dict(error_type=type(error).__name__, error_repr=description[:8192],
                error_repr_truncated=len(description) > 8192,
                traceback_tail=trace[-16384:], traceback_truncated=len(trace) > 16384,
                evidence=getattr(error, 'source_reference_evidence', None))
            if worker_input_sha256 is not None:
                payload['worker_input_sha256'] = worker_input_sha256
            try:
                _write_reference_event(stream, identity, 'failed', payload)
            except BaseException as evidence_error:
                error.__notes__ = list(getattr(error, '__notes__', ())) + [
                    'terminal evidence write failed: ' + repr(evidence_error)]
            raise
        if worker_input_sha256 is not None:
            result['worker_input_sha256'] = worker_input_sha256
        try:
            _write_reference_event(stream, identity, 'reference_returned', result)
        except BaseException as error:
            error.source_reference_evidence = result
            raise
    return result


def read_reference_events(path, identity):
    """Read bounded evidence; a missing terminal record proves no completion."""
    from vllm_export_hook import _source_identity, _metadata_stream
    from loader_registry import FORMATS
    identity = _source_identity(identity)
    with _metadata_stream(Path(path)) as stream:
        raw = stream.read(131073)
    if len(raw) > 131072 or not raw.endswith(b'\n'):
        raise ValueError('reference journal oversized or has a partial final line')
    lines = raw.splitlines()
    if any(len(line) + 1 > 65536 for line in lines):
        raise ValueError('reference journal line exceeds 64KiB')
    rows = [json.loads(line) for line in lines]
    if not 1 <= len(rows) <= 2:
        raise ValueError('one start and at most one terminal reference record required')
    for row in rows:
        if (not isinstance(row, dict)
                or set(row) != set(identity) | {'schema', 'event', 'utc', 'monotonic_seconds', 'payload'}
                or row.get('schema') != 'kimi-source-reference-events-v1'
                or type(row['monotonic_seconds']) not in (int, float)
                or not math.isfinite(row['monotonic_seconds']) or row['monotonic_seconds'] < 0
                or any(row.get(k) != v for k, v in identity.items())):
            raise ValueError('reference journal identity differs')
        from source_schedule import _utc
        _utc(row['utc'])
    if rows[0]['event'] != 'started' or (len(rows) == 2 and rows[1]['event'] not in ('failed', 'reference_returned')):
        raise ValueError('reference journal event order differs')
    if rows[-1]['monotonic_seconds'] < rows[0]['monotonic_seconds']:
        raise ValueError('reference journal monotonic clock regressed')
    if len(rows) == 2 and rows[1]['event'] == 'reference_returned':
        payload = rows[1]['payload']
        if (not isinstance(payload, dict) or any(payload.get(k) != v for k, v in identity.items())
                or payload.get('reference_collected') is not True
                or payload.get('engine_shutdown_returned') is not True
                or any(payload.get(k) is not False for k in ('runtime_admitted', 'gold_frozen',
                    'native_loader_verified', 'full_service_verified', 'resources_released'))):
            raise ValueError('reference result scope differs')
        if payload.get('export_performed') is False and (payload.get('ranks') is not None
                or payload.get('engine_options',{}).get('load_format')!='safetensors'):
            raise ValueError('reference-only result must not carry Store completion')
        if payload.get('export_performed') is True and payload.get('engine_options',{}).get('load_format')!=FORMATS['export']:
            raise ValueError('export result loader scope differs')
    return dict(status='incomplete' if len(rows) == 1 else rows[1]['event'], records=rows,
                journal_sha256=hashlib.sha256(raw).hexdigest(), resources_released=False,
                runtime_admitted=False, full_service_verified=False)
