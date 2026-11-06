from pathlib import Path
import hashlib,json,subprocess,sys,unittest
from unittest import mock
root=Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0]=[str(root/'sllm_store'),str(root/'sllm_store/tests/python')]
import test_v4_registration as tests
parent='92f4380f19a3448b251c59c3cb522a0c25212f20'
sources={root/name:subprocess.check_output(['git','-C',str(root),'show',parent+':'+name])
         for name in ('sllm/controller.py','sllm/store_manager.py')}
original=Path.read_text
def pinned(path,*args,**kwargs):
 if path in sources:return sources[path].decode('utf-8')
 return original(path,*args,**kwargs)
suite=unittest.TestSuite(tests.RegistrationBindingContracts(name) for name in (
 'test_storemanager_changed_store_identity_is_rejected_before_lower_cache',
 'test_controller_changed_active_context_is_rejected_before_return'))
with mock.patch.object(Path,'read_text',pinned):
 result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.testsRun==2 and len(result.failures)==2 and not result.errors
assert all('ValueError not raised' in error for _,error in result.failures)
report=dict(parent_source=parent,tests_run=2,expected_failures=2,unexpected_errors=0,
 is_formal_attempt=False,gpu_execution=False,transport_is_stubbed=True,
 test_source_sha256=hashlib.sha256((root/'sllm_store/tests/python/test_v4_registration.py').read_bytes()).hexdigest(),
 sources=[dict(path=str(path.relative_to(root)),sha256=hashlib.sha256(data).hexdigest()) for path,data in sources.items()],
 scope='Current two regression tests against exact pre-fix Controller/StoreManager Git blobs, via AST fixtures')
Path('/home/boyjiang/sllm-v4-pro/evidence/registration-red-replay-receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
