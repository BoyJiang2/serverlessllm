from pathlib import Path
import hashlib,json,subprocess
root=Path('/home/boyjiang/sllm-v4-pro/code');out=Path('/home/boyjiang/sllm-v4-pro/evidence/cpu-tokenizer-20261106')
manifest=json.loads((root/'docs/evidence/v4-topology-p3/reviewed-source-manifest.json').read_bytes())
for row in manifest['files']:
 assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
report=dict(source_head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
 source_scope='Production runtime sources unchanged from the 281-test source manifest',runtime_source_files_verified=29,
 focused_suite='test_v4_backend.py',tests_run=15,new_tests=1,failures=0,errors=0,
 scope='Backend source with fake engine forwards actual CPU BPE candidate IDs without message re-encoding; not model gold',
 gpu_execution=False,native_inference=False,full_weight_io=False,
 source_files=[dict(path=name,sha256=hashlib.sha256((root/name).read_bytes()).hexdigest()) for name in
 ('sllm_store/tests/python/test_v4_backend.py','sllm_store/tests/fixtures/v4_cpu_bpe_candidates.json')])
log=(out/'backend-input-tests.log').read_text()
assert 'Ran 15 tests' in log and '\nOK\n' in log and 'FAILED' not in log
(out/'backend-input-receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
