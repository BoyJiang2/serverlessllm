import copy
import datetime
import hashlib
import importlib.util
import json
import pathlib
import sys
import tempfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parent
ENTRY = ROOT/'capsule/controls/debug_stack_v1/debug_stack.py'
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('glm_store_cold_proposal', ENTRY)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
draft = json.loads((ROOT/'review-draft.json').read_bytes())
source = module.verified_source(module.HOST/'code/source-debug-fb98c22540f8-58d1d50ae064')
module.verify_runtime_assets(draft['runtime_reference'])
expected_stdout = []

def rejects(label, run, phrase):
    try:
        run()
    except RuntimeError as e:
        assert phrase in str(e), e
        expected_stdout.append(label)
    else:
        raise AssertionError(label)

rejects('unapproved draft', lambda: module.verify_supplement_review(draft, source), 'not approved')
now = datetime.datetime.now(datetime.timezone.utc)
review = dict(draft, verdict='PASS')
review['proposed_attempts'] = [dict(condition='store_disk_cold', run_id='sllm-debug-20261117T000000Z-ffffffff', holder='1'*32, status='not_started')]
module.verify_supplement_review(review, source)
expected_stdout.append('approved fixture with pinned runtime')
for field in ('runtime_reference','source_tree_sha256','field_budget'):
    bad = dict(review, **{field: {}})
    rejects(field, lambda: module.verify_supplement_review(bad, source), 'runtime pins')
bad = dict(review,parent_review_sha256='0'*64)
rejects('parent review pin', lambda: module.verify_supplement_review(bad, source), 'digest differs')
bad = dict(review,tool_sha256={})
rejects('tool closure pin', lambda: module.verify_supplement_review(bad, source), 'not approved')
review_sha = hashlib.sha256(json.dumps(review).encode()).hexdigest()
sample = review['proposed_attempts'][0]
boot = 'synthetic-boot'
auth = dict(sample,boot_id=boot,review_sha256=review_sha,node='qjhs003',uid=2147,
            authority_reference='unit test only',window_start=(now-datetime.timedelta(seconds=10)).isoformat(),
            window_end=(now+datetime.timedelta(seconds=4200)).isoformat(),prior_final_sha256={})
run = sample['run_id']
with tempfile.TemporaryDirectory() as temp:
    fake = pathlib.Path(temp)
    debug = fake/'debug'
    debug.mkdir()
    with patch.object(module,'HOST',fake):
        verify = module.verify_supplement_attempt
        verify(review,review_sha,run,'store_disk_cold',boot,auth)
        assert not (debug/run).exists()
        expected_stdout.append('cold-only read-only gate')
        rejects('wrong condition',lambda:verify(review,review_sha,run,'safetensors_disk_cold',boot,auth),'outside reviewed scope')
        rejects('wrong run',lambda:verify(review,review_sha,run[:-1]+'a','store_disk_cold',boot,auth),'identity differs')
        for key in ('holder','boot_id','review_sha256','node','uid'):
            rejects(key,lambda:verify(review,review_sha,run,'store_disk_cold',boot,dict(auth,**{key:'altered'})),'identity differs')
        rejects('prior-chain field must be empty',lambda:verify(review,review_sha,run,'store_disk_cold',boot,dict(auth,prior_final_sha256={'unexpected':'0'*64})),'preceding final')
        rejects('window short',lambda:verify(review,review_sha,run,'store_disk_cold',boot,dict(auth,window_end=(now+datetime.timedelta(seconds=60)).isoformat())),'lacks full 3900')
        (debug/run).mkdir()
        rejects('existing run',lambda:verify(review,review_sha,run,'store_disk_cold',boot,auth),'already has a run directory')
        old = dict(review,scope='glm-controller-supplement-v1')
        rejects('legacy gate rejects one arm',lambda:verify(old,review_sha,run,'store_disk_cold',boot,auth),'sequence or budget')
print(json.dumps(dict(passed=len(expected_stdout),cases=expected_stdout,gpu_or_model_started=False)))
