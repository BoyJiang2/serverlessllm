from pathlib import Path
import hashlib,json,subprocess,sys,types,unittest
from unittest import mock
root=Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0]=[str(root/'sllm_store'),str(root/'sllm_store/tests/python')]
import test_v4_initial_topology as tests
import sllm_store.v4_service as service
parent='4cf40aeeddd23728d746a7b8a2adab1fa090ce25'
old_source=subprocess.check_output(['git','-C',str(root),'show',parent+':sllm_store/sllm_store/v4_service.py'])
old=types.ModuleType('pinned_v4_service');exec(compile(old_source,'<pinned-parent-v4-service>','exec'),old.__dict__)
for name in ('sllm/controller.py','sllm/store_manager.py'):
 assert (root/name).read_bytes()==subprocess.check_output(['git','-C',str(root),'show',parent+':'+name])
suite=unittest.TestSuite(tests.InitialTopologyContracts(name) for name in (
 'test_controller_rejects_one_gpu_for_tp8_before_store_or_router',
 'test_manager_rejects_implicit_zero_gpu_request_before_worker'))
with mock.patch.multiple(service,freeze_registration_config=old.freeze_registration_config,
                         bind_v4_registration=old.bind_v4_registration):
 result=unittest.TextTestRunner(verbosity=2).run(suite)
assert result.testsRun==2 and len(result.failures)==2 and not result.errors
assert all('ValueError not raised' in error for _,error in result.failures)
report=dict(parent_source=parent,old_shared_source_sha256=hashlib.sha256(old_source).hexdigest(),
 tests_run=2,expected_failures=2,unexpected_errors=0,is_formal_attempt=False,gpu_execution=False,
 transport_is_stubbed=True,unchanged_controller_storemanager_bytes_verified=True,
 test_source_sha256=hashlib.sha256((root/'sllm_store/tests/python/test_v4_initial_topology.py').read_bytes()).hexdigest(),
 scope='Two regression tests with exact pre-fix shared helpers and actual unchanged Controller/StoreManager source')
Path('/home/boyjiang/sllm-v4-pro/evidence/topology-red-replay-receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
