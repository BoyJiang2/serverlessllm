"""Reach normal lifecycle with audit flags absent; no GPU or HTTP operations."""
import hashlib,importlib.util,json,pathlib,sys,tempfile,types,unittest
from unittest.mock import patch

class ReachedLifecycle(Exception):pass

class NormalValidation(unittest.TestCase):
    def exercise_label(self,label=None):
        source=pathlib.Path(__file__).with_name('validation_control13.py')
        spec=importlib.util.spec_from_file_location('prepared_validation',source)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules,{'weight_contract':types.SimpleNamespace(weight_state=lambda value:value)}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);assets=root/'assets';control=root/'control';out=control/'service-bootstrap-normal13/serverless_llm'
            (assets/'validation').mkdir(parents=True);out.mkdir(parents=True)
            def save(path,data):path.write_text(json.dumps(data))
            save(assets/'validation/store-restore-validation.json',dict(status='full Store component restore validation passed'))
            gold=assets/'validation/native-gold.json';save(gold,dict(engine_config=dict(model='frozen',load_format='serverless_llm')))
            proof=root/'full-proof.json';save(proof,dict(status='A4 native HTTP lifecycle checks passed',cycles=2,weight_format='serverless_llm'))
            manifest=dict(run_id='test-run',runtime_identity=dict(boot_id='test-boot'),
                          reference_sha256={'native-gold.json':hashlib.sha256(gold.read_bytes()).hexdigest()})
            if label is not None:manifest['service_worker_label']=label
            save(control/'run-manifest.json',manifest)
            save(out.parent/'store-numa-launch.json',{})
            save(control/((label or 'service13a')+'-identity.json'),{})
            save(control/'monitor-v3-ready.json',dict(registered_store_helper={}))
            workspace=types.SimpleNamespace(run_root=lambda:control,manifest=lambda:manifest)
            real_path=pathlib.Path
            def redirect(value):return assets if str(value)=='/experiment' else real_path(value)
            env={'SLLM_NORMAL_FLAGS_SMOKE':'1','SLLM_FULL_A4_PROOF_FILE':str(proof)}
            with patch.dict(sys.modules,{'workspace':workspace}),patch.dict(module.os.environ,env,clear=True), \
                 patch.object(module,'pathlib',types.SimpleNamespace(Path=redirect)), \
                 patch.object(module.subprocess,'check_output',return_value='0\n'*8), \
                 patch.object(module,'require_helper_binding'), \
                 patch.object(module,'require_helper_only',side_effect=ReachedLifecycle):
                with self.assertRaises(ReachedLifecycle):module.run(out,audit_weights=False)

    def test_normal_flags_do_not_require_audit_directory(self):self.exercise_label()

    def test_normal_role_uses_its_own_identity(self):self.exercise_label('normal13')

if __name__=='__main__':unittest.main()
