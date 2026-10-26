"""Container-side formal gate verification; host admission is independently required."""
import hashlib
import json
import os
import pathlib
import re
import sys
import time

from attempt_state import PREREG_SHA, checked_json

GOLD_SHA = '9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0'


def g1_reference_root(config, assets):
    run = config['g1_reference_run_id']
    if not re.fullmatch(r'sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', run):
        raise ValueError('invalid G1 reference run identity')
    reference = pathlib.Path(assets) / 'validation/controls' / run
    if not reference.resolve().is_relative_to(pathlib.Path(assets).resolve()):
        raise ValueError('G1 reference escaped assets')
    return reference


def verify_normal_reference(config, assets):
    reference = g1_reference_root(config, assets)
    acceptance = checked_json(reference / 'G1-acceptance.json', config['g1_acceptance_sha256'])
    if any(acceptance.get(k) != v for k, v in dict(
            status='G1 acceptance passed; no formal samples',
            run_id=config['g1_reference_run_id'],
            code_commit=config['g1_reference_code_commit'],
            manifest_sha256=config['g1_reference_manifest_sha256'],
            ray_only_controls=2, native_lifecycles_per_format=2, pool_initializations=1).items()):
        raise ValueError('actual complete G1 reference required')
    if set(config['full_a4_proofs']) != {'serverless_llm', 'safetensors'}:
        raise ValueError('both full A4 formats required')
    for kind, row in config['full_a4_proofs'].items():
        path = (reference / row['path']).resolve()
        if not path.is_relative_to(reference.resolve()):
            raise ValueError('A4 reference escaped evidence root')
        proof = checked_json(path, row['sha256'])
        if any(proof.get(k) != v for k, v in dict(status='A4 native HTTP lifecycle checks passed',
                cycles=2, weight_format=kind, weight_audit_performed=True).items()):
            raise ValueError('full audited A4 reference required')
        coverage = proof['rank_coverage']
        if len(coverage) != 16 or {(r['cycle'], r['rank']) for r in coverage} != {
                (cycle, rank) for cycle in (1, 2) for rank in range(8)}:
            raise ValueError('full two-cycle eight-rank coverage required')
    return acceptance


def verify_admission(root, source, assets, code, manifest_sha, gate_sha):
    """CPU-only shared admission; caller must also perform actual host preflight."""
    if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
        raise RuntimeError('optimized Python is forbidden')
    gate = checked_json(root / 'formal-admission.json', gate_sha)
    if not re.fullmatch('[0-9a-f]{40}', code) or not re.fullmatch('[0-9a-f]{64}', manifest_sha):
        raise ValueError('exact code and source manifest required')
    if any(gate.get(k) != v for k, v in dict(decision='approved', scope='formal-runtime',
            code_commit=code, source_manifest_sha256=manifest_sha,
            preregistration_sha256=PREREG_SHA, gold_sha256=GOLD_SHA).items()):
        raise ValueError('current formal admission required; G1 and historical gates are insufficient')
    registration = (root / 'run-manifest.json').read_bytes()
    if registration != (source / 'run-manifest.json').read_bytes():
        raise ValueError('run registration differs from reviewed source')
    config = json.loads(registration)
    if gate['execution_run_id'] != config['run_id'] or config['scope'] != 'formal continuation of original forty IDs':
        raise ValueError('formal execution identity differs')
    if gate.get('worker_label') != 'formal13':
        raise ValueError('formal worker identity differs')
    r2 = checked_json(root / 'r2-approval.json', gate['r2_approval_sha256'])
    if any(r2.get(k) != v for k, v in dict(gate='R2', scope='formal-runtime', verdict='approved',
                                          code_commit=code, manifest_sha256=manifest_sha).items()):
        raise ValueError('current independent R2 approval required')
    source_manifest = checked_json(source / 'control-input-manifest.json', manifest_sha)
    checked = set()
    for row in source_manifest['files']:
        path = (source / row['path']).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file():
            raise ValueError('formal source escaped package')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('formal source differs: ' + row['path'])
        checked.add(row['path'])
    if not {'formal_trials.py', 'formal_gate.py', 'attempt_state.py', 'bootstrap_formal.py',
            'cache_state_formal.py', 'run-manifest.json', 'cleanup_guard13.sh'} <= checked:
        raise ValueError('incomplete formal entry manifest')
    checked_json(assets / 'protocol/measurement-preregistration.json', PREREG_SHA)
    checked_json(assets / 'validation/native-gold.json', GOLD_SHA)
    verify_normal_reference(config, assets)
    formats = set()
    for row in gate['normal_flags_proofs']:
        path = (root / row['path']).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError('normal-flags proof escaped run')
        proof = checked_json(path, row['sha256'])
        if (proof.get('status') != 'normal-flags gold smoke lifecycle checks passed'
                or proof.get('cycles') != 2 or proof.get('weight_audit_performed') is not False):
            raise ValueError('normal-flags lifecycle evidence required')
        formats.add(proof['weight_format'])
    if formats != {'serverless_llm', 'safetensors'} or len(gate['normal_flags_proofs']) != 2:
        raise ValueError('both normal-flags formats required')
    return gate


def verify_gate():
    from workspace import SOURCE, run_root, workspace
    root = run_root()
    code = os.environ['SLLM_FORMAL_CODE_COMMIT']
    manifest_sha = os.environ['SLLM_FORMAL_SOURCE_MANIFEST_SHA256']
    gate = verify_admission(root, SOURCE, workspace(), code, manifest_sha,
                            os.environ['SLLM_FORMAL_GATE_SHA256'])
    config = json.loads((root / 'run-manifest.json').read_bytes())
    from run_store_server13 import verify_store_imports
    verify_store_imports(config['store_import_sha256'])
    for key in ('SLLM_VERIFY_WEIGHTS', 'SLLM_SERVICE_WEIGHT_AUDIT_DIR', 'SLLM_EXPORT_RAW_STORE',
                'VLLM_ALLOW_INSECURE_SERIALIZATION', 'SLLM_NORMAL_FLAGS_SMOKE'):
        if os.environ.get(key):
            raise ValueError(key + ' must be absent during formal measurement')
    for key, value in config['formal_required_env'].items():
        if os.environ.get(key) != value:
            raise ValueError('formal environment differs: ' + key)
    # Host-only guard/UID/hostname checks run before container creation. Bind that
    # actual dispatch here instead of querying the container's different namespace.
    identity_path = root / (gate['worker_label'] + '-identity.json')
    stop = time.monotonic() + 5  # Included in the existing supervisor total budget.
    while True:
        try:
            identity = json.loads(identity_path.read_bytes())
            break
        except (FileNotFoundError, json.JSONDecodeError):
            if time.monotonic() >= stop:
                raise RuntimeError('own host dispatch receipt unavailable within5s')
            time.sleep(.05)
    boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    preflight = identity['preflight']
    if (identity['run_id'] != config['run_id'] or identity['spec']['name'] != gate['worker_label']
            or identity['code_commit'] != code
            or identity['manifest_sha256'] != manifest_sha
            or preflight['run_id'] != config['run_id']
            or preflight['boot_id'] != config['runtime_identity']['boot_id'] or boot != preflight['boot_id']
            or preflight['guard_script_sha256'] != hashlib.sha256((SOURCE / 'cleanup_guard13.sh').read_bytes()).hexdigest()):
        raise ValueError('actual guarded host dispatch differs')
    return gate
