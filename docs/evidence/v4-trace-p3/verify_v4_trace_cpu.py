"""Archive a CPU-only recorder check; worker bodies/source guard are substitutes."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

root = Path('/home/boyjiang/sllm-v4-pro')
code, evidence = root / 'code', root / 'evidence'
sys.path.insert(0, str(code/'sllm_store'))
from sllm_store import event_trace as trace

output = evidence/'cpu-trace-check-20261106'
output.mkdir(exist_ok=False)
os.environ['SLLM_EVENT_DIR'] = str(output/'events')
os.environ['SLLM_EVENT_RUN_ID'] = 'CPU_TRACE_CHECK_NOT_A_FORMAL_ATTEMPT_20261106'
script = code/'sllm_store/tests/python/test_v4_trace.py'
begin = time.monotonic_ns()
trace.record_event('backend_engine_create_begin', cpu_fixture=True)
for rank in range(8):
    subprocess.run([sys.executable,str(script),'--cpu-worker',str(rank)],check=True,
                   capture_output=True,text=True,timeout=10)
trace.record_event('backend_engine_ready', cpu_fixture=True)
trace.record_event('router_instance_published', cpu_fixture=True)
end = time.monotonic_ns()
clock = trace.process_identity()
rows = trace.read_event_window(output/'events',os.environ['SLLM_EVENT_RUN_ID'],begin,end,
    boot_id=clock['boot_id'],time_namespace=clock['time_namespace'])
metrics = runpy.run_path(str(code/'benchmarks/v4_evidence.py'))['phase_metrics'](rows,begin)
rank_processes = {(row['rank'],row['pid']) for row in rows if row.get('rank') is not None}
assert len(rank_processes)==8 and {rank for rank,pid in rank_processes}==set(range(8))
blocked = [name for name in sys.modules if name.split('.')[0] in ('torch','vllm','ray')]
assert not blocked, blocked
receipt = dict(scope='CPU recorder only; manually emitted control events and fake worker bodies/source guard; not model performance or admission',
    is_formal_attempt=False,weights_loaded=False,gpu_execution=False,source_guard_is_stub=True,
    inference_modules_imported=blocked,rank_processes=sorted(rank_processes),
    begin_ns=begin,end_ns=end,clock_identity=clock,
    cpu_fixture_host_boundary_metrics_only=metrics,
    raw_event_files={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((output/'events').glob('*.jsonl'))},
    source_head=subprocess.check_output(['git','-C',str(code),'rev-parse','HEAD'],text=True).strip(),
    working_diff_sha256=hashlib.sha256(subprocess.check_output(['git','-C',str(code),'diff','--binary','HEAD'])).hexdigest())
(output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(dict(cpu_check_passed=True,scope=receipt['scope'],ranks=8,event_files=len(receipt['raw_event_files']),output=str(output))))
