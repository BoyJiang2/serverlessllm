from pathlib import Path
import hashlib,json,subprocess,sys
root=Path('/home/boyjiang/sllm-v4-pro/code')
base=root/'docs/evidence/v4-attempts-p3/cpu-attempt-check-20261106'
protocol=base/'preregistration-cpu.json'
sha='2a1607b34d15b1076a7009fe6ba2467d055f07c6556c234af4037a9bd5813184'
assert hashlib.sha256(protocol.read_bytes()).hexdigest()==sha
suite=json.loads(Path('/home/boyjiang/sllm-v4-pro/evidence/results-suite-receipt.json').read_bytes())
assert suite['tests_run']==257 and suite['cpu_suite_passed']
diff=subprocess.check_output(['git','-C',str(root),'diff','--binary','HEAD','--','benchmarks','sllm','sllm_store','tests'])
assert hashlib.sha256(diff).hexdigest()==suite['working_diff_sha256']
files=[protocol,*sorted((base/'execution').rglob('*.json'))]
before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
out=Path(sys.argv[1])
command=[sys.executable,str(root/'benchmarks/v4_results.py'),'--preregistration',str(protocol),
         '--prereg-sha256',sha,'--execution-root',str(base/'execution'),'--output',str(out)]
run=subprocess.run(command,check=True,capture_output=True,text=True,timeout=30,
 env=dict(__import__('os').environ,PYTHONPATH=str(root/'sllm_store')))
summary=json.loads(run.stdout)
assert summary['runtime_qualified'] is False
assert sum(c['started'] for c in summary['conditions'].values())==1
assert sum(c['unstarted'] for c in summary['conditions'].values())==11
assert all(c['formal_successes']==0 and c['formal_success_rate'] is None for c in summary['conditions'].values())
assert before=={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
manifest=json.loads((out/'manifest.json').read_bytes())
assert manifest['artifact_kind']=='derived_not_execution'
for row in manifest['output_files']:
 assert hashlib.sha256((out/row['path']).read_bytes()).hexdigest()==row['sha256']
report=dict(is_formal_attempt=False,formal_ids_consumed=0,source_fixture_is_synthetic=True,
 mock_clock_in_source_fixture=True,weights_loaded=False,gpu_execution=False,performance_measurement=False,
 read_only_execution_inputs=True,source_head=suite['source_head'],working_diff_sha256=suite['working_diff_sha256'],
 scope='Actual CPU CLI replay of prior synthetic journal; derived data only; no performance or runtime qualification',
 source_protocol_sha256=sha,derived_result_files=len(list(out.glob('*/result.json'))),
 derived_files=[dict(path=str(p.relative_to(out)),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                for p in sorted(out.rglob('*.json'))])
(out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
