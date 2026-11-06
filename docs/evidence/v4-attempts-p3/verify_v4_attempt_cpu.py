from pathlib import Path
import hashlib,json,shutil,subprocess,sys
root=Path('/home/boyjiang/sllm-v4-pro/code')
sys.path[:0]=[str(root/'sllm_store'),str(root/'sllm_store/tests/python'),str(root/'benchmarks')]
import test_v4_attempts as fixtures
out=Path(sys.argv[1]);out.mkdir(exist_ok=False)
test=fixtures.AttemptJournalContracts('test_complete_synthetic_evidence_still_cannot_award_runtime_pass')
try:
 test.setUp()
 test.test_complete_synthetic_evidence_still_cannot_award_runtime_pass()
 shutil.copytree(test.root,out/'execution')
 shutil.copyfile(fixtures.PROTOCOL,out/'preregistration-cpu.json')
 rows=fixtures.attempts.collect_attempts(out/'preregistration-cpu.json',fixtures.SHA,[out/'execution'])
 consumed=[r for r in rows if r['consumed']]
 assert len(consumed)==1 and consumed[0]['status']=='evidence_complete_unqualified'
 diff=subprocess.check_output(['git','-C',str(root),'diff','--binary','HEAD','--','benchmarks','sllm','sllm_store','tests'])
 suite=json.loads(Path('/home/boyjiang/sllm-v4-pro/evidence/attempt-suite-receipt.json').read_bytes())
 assert hashlib.sha256(diff).hexdigest()==suite['working_diff_sha256']
 report=dict(is_formal_attempt=False,weights_loaded=False,gpu_execution=False,
  mock_clock=True,source_identity_is_fixture=True,worker_events_synthetic=True,cache_vectors_synthetic=True,release_samples_synthetic=True,
  process_identity_from_actual_linux_proc=True,performance_measurement=False,
  source_head=suite['source_head'],working_diff_sha256=suite['working_diff_sha256'],
  scope='Persistent CPU attempt journal fixture only; no service, native Store, lease, physical release or runtime proof',
  consumed_cpu_ids=[r['attempt_id'] for r in consumed],status=consumed[0]['status'],
  journal_files=[dict(path=str(p.relative_to(out)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
                 for p in sorted(out.rglob('*.json'))])
 (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps(report))
finally:
 test.doCleanups()
