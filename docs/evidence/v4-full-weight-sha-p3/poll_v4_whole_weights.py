from pathlib import Path
import datetime,json,os
job=Path('/home/boyjiang/sllm-v4-pro/evidence/full-weight-sha-20261106T085145Z')
h=json.loads((job/'handle.json').read_bytes());now=datetime.datetime.now(datetime.timezone.utc).isoformat()
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip()==h['boot_id']
observed=dict(observed_utc=now,job=str(job),processes=[])
for kind in ('supervisor','checksum_process'):
 ref=h[kind];p=Path('/proc')/str(ref['pid'])
 if p.exists():
  fields=(p/'stat').read_text().rsplit(')',1)[1].split();assert int(fields[19])==ref['start_ticks']
  observed['processes'].append(dict(kind=kind,**ref,state=fields[0],cmdline=(p/'cmdline').read_bytes().replace(b'\0',b' ').decode(),cwd=os.readlink(p/'cwd') if fields[0]!='Z' else None))
 else:observed['processes'].append(dict(kind=kind,**ref,missing=True))
for name in ('progress.json','result.json','failure.json'):
 if (job/name).exists():observed[name]=json.loads((job/name).read_bytes())
observed['stdout_lines']=(job/'sha256sum.stdout.log').read_text().splitlines()
observed['stderr']=(job/'sha256sum.stderr.log').read_text()
with (job/'observations.jsonl').open('a') as file:file.write(json.dumps(observed)+'\n')
progress=observed.get('progress.json',{})
print(json.dumps(dict(observed_utc=now,processes=observed['processes'],progress=progress,
 stdout_count=len(observed['stdout_lines']),last_stdout=observed['stdout_lines'][-1:] or [],stderr=observed['stderr'],
 terminal_result={k:v for k,v in observed.get('result.json',{}).items() if k!='files'},failure=observed.get('failure.json'))))
