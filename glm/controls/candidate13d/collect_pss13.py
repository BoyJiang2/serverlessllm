"""Slow PSS sample only in this service's private PID namespace, after deletion."""
import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
import json,pathlib,signal,time
root=pathlib.Path('/experiment/controls/sllm-glm51fp8-20261025T145409Z-0b3a25a1')
output=pathlib.Path(sys.argv[1]).resolve()
if not output.is_relative_to(root): raise RuntimeError('PSS output outside own control')
if not pathlib.Path('/.dockerenv').exists() or 'supervise_control13.py' not in pathlib.Path('/proc/1/cmdline').read_bytes().replace(b'\0',b' ').decode(): raise RuntimeError('requires own private service PID namespace')
def expired(signum,frame): raise TimeoutError('PSS sample exceeded45s; unavailable, never zero')
signal.signal(signal.SIGALRM,expired); signal.alarm(45)
store_pid=int(sys.argv[2])
record=dict(begin_ns=time.monotonic_ns(),status='incomplete',store_pid=store_pid,processes=[],scope='non-atomic per-PID reads; service container only, not head/whole-host PSS')
try:
    for proc in pathlib.Path('/proc').glob('[0-9]*'):
        try:
            status=proc.joinpath('status').read_text()
            if next(line.split()[1] for line in status.splitlines() if line.startswith('Uid:'))!='2147': continue
            begin=time.monotonic_ns()
            try: value=proc.joinpath('smaps_rollup').read_text()
            except PermissionError: value=None
            record['processes'].append(dict(pid=int(proc.name),begin_ns=begin,end_ns=time.monotonic_ns(),smaps_rollup=value,unavailable_reason='permission denied' if value is None else None))
        except (FileNotFoundError,ProcessLookupError): continue
    if not any(row['pid']==store_pid and row['smaps_rollup'] is not None for row in record['processes']): raise RuntimeError('Store PSS unavailable')
    record['status']='completed'
except TimeoutError as error: record.update(status='timeout',error=str(error))
finally:
    signal.alarm(0)
    record['end_ns']=time.monotonic_ns()
    with output.open('x') as stream: json.dump(record,stream,indent=2)
