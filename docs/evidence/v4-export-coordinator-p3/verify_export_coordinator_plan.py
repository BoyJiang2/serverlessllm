"""Verify --plan against the selected local model; do not import an inference stack."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys

root = Path('/home/boyjiang/sllm-v4-pro')
code, evidence = root / 'code', root / 'evidence'
sys.path.insert(0, str(code / 'sllm_store'))
model = Path('/mnt/raid/models/DeepSeek-V4-Pro')
store = Path('/data/brick1/boyjiang/sllm-v4-pro-store/CPU_PLAN_ONLY_NOT_FOR_RUNTIME_20261106')
identity = evidence / 'export-coordinator-plan-identity.json'
assert not store.exists(), 'Plan target unexpectedly exists'
sys.argv = ['v4_convert', '--model', str(model), '--store', str(store), '--identity', str(identity), '--plan']
out = io.StringIO()
with contextlib.redirect_stdout(out):
    runpy.run_module('sllm_store.v4_convert', run_name='__main__')
blocked = [name for name in sys.modules if name.split('.')[0] in ('torch', 'vllm', 'ray') or name.startswith('sllm_store._checkpoint_store')]
assert not blocked, blocked
assert not store.exists()
config = json.loads(out.getvalue())
(evidence / 'export-coordinator-plan-only.json').write_text(json.dumps(config, indent=2)+'\n')
receipt = dict(scope='CPU plan only; placeholder identity is not runtime qualified; no formal attempt',
    host=subprocess.check_output(['hostname'],text=True).strip(),
    model_path=str(model), store_path=str(store), store_path_created=False,
    inference_modules_imported=blocked, plan_passed=True,
    source_head=subprocess.check_output(['git','-C',str(code),'rev-parse','HEAD'],text=True).strip(),
    working_diff_sha256=hashlib.sha256(subprocess.check_output(['git','-C',str(code),'diff','--binary','HEAD'])).hexdigest(),
    identity_sha256=hashlib.sha256(identity.read_bytes()).hexdigest(),
    config_sha256=hashlib.sha256((model/'config.json').read_bytes()).hexdigest())
(evidence / 'export-coordinator-plan-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
