"""Container-side formal gate verification; host admission is independently required."""
import hashlib
import json
import os
import pathlib
import re
import sys

from attempt_state import PREREG_SHA, checked_json

GOLD_SHA = '9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0'


def verify_gate():
    if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
        raise RuntimeError('optimized Python is forbidden')
    from workspace import SOURCE, run_root
    root = run_root()
    gate = checked_json(root / 'formal-admission.json', os.environ['SLLM_FORMAL_GATE_SHA256'])
    code = os.environ['SLLM_FORMAL_CODE_COMMIT']
    manifest_sha = os.environ['SLLM_FORMAL_SOURCE_MANIFEST_SHA256']
    if not re.fullmatch('[0-9a-f]{40}', code) or not re.fullmatch('[0-9a-f]{64}', manifest_sha):
        raise ValueError('exact code and source manifest required')
    if any(gate.get(k) != v for k, v in dict(decision='approved', scope='formal-runtime',
            code_commit=code, source_manifest_sha256=manifest_sha,
            preregistration_sha256=PREREG_SHA, gold_sha256=GOLD_SHA).items()):
        raise ValueError('current formal admission required; G1 and historical gates are insufficient')
    config = json.loads((root / 'run-manifest.json').read_bytes())
    if gate['execution_run_id'] != config['run_id'] or config['scope'] != 'formal continuation of original forty IDs':
        raise ValueError('formal execution identity differs')
    r2 = checked_json(root / 'r2-approval.json', gate['r2_approval_sha256'])
    if any(r2.get(k) != v for k, v in dict(gate='R2', scope='formal-runtime', verdict='approved',
                                          code_commit=code, manifest_sha256=manifest_sha).items()):
        raise ValueError('current independent R2 approval required')
    source_manifest = checked_json(SOURCE / 'formal-input-manifest.json', manifest_sha)
    checked = set()
    for row in source_manifest['files']:
        path = (SOURCE / row['path']).resolve()
        if not path.is_relative_to(SOURCE.resolve()) or not path.is_file():
            raise ValueError('formal source escaped package')
        if hashlib.sha256(path.read_bytes()).hexdigest() != row['sha256']:
            raise ValueError('formal source differs: ' + row['path'])
        checked.add(row['path'])
    if not {'formal_trials.py', 'formal_gate.py', 'attempt_state.py', 'bootstrap_formal.py',
            'cache_state_formal.py', 'run-manifest.json'} <= checked:
        raise ValueError('incomplete formal entry manifest')
    assets = pathlib.Path('/experiment')
    checked_json(assets / 'protocol/measurement-preregistration.json', PREREG_SHA)
    checked_json(assets / 'validation/native-gold.json', GOLD_SHA)
    from run_store_server13 import verify_store_imports
    verify_store_imports(config['store_import_sha256'])
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
    for key in ('SLLM_VERIFY_WEIGHTS', 'SLLM_SERVICE_WEIGHT_AUDIT_DIR', 'SLLM_EXPORT_RAW_STORE',
                'VLLM_ALLOW_INSECURE_SERIALIZATION', 'SLLM_NORMAL_FLAGS_SMOKE'):
        if os.environ.get(key):
            raise ValueError(key + ' must be absent during formal measurement')
    for key, value in config['formal_required_env'].items():
        if os.environ.get(key) != value:
            raise ValueError('formal environment differs: ' + key)
    from preflight_control13 import check
    check(root)
    return gate
