from pathlib import Path
import datetime,hashlib,json,os,subprocess
job=Path('/home/boyjiang/sllm-v4-pro/evidence/full-weight-sha-20261106T085145Z');h=json.loads((job/'handle.json').read_bytes());pid=h['checksum_process']['pid'];p=Path('/proc')/str(pid)
f=(p/'stat').read_text().rsplit(')',1)[1].split();assert int(f[19])==h['checksum_process']['start_ticks']
exe=Path(os.readlink(p/'exe'));assert str(exe)=='/usr/bin/sha256sum'
status=dict(line.split(':',1) for line in (p/'status').read_text().splitlines() if ':' in line)
opened=[]
for fd in (p/'fd').iterdir():
 try:
  target=os.readlink(fd)
  if target.startswith(h['model_path']+'/'):
   opened.append(dict(fd=fd.name,target=target,fdinfo=(p/'fdinfo'/fd.name).read_text()))
 except FileNotFoundError:pass
report=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),pid=pid,start_ticks=int(f[19]),
 executable=str(exe),executable_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
 coreutils_version=subprocess.check_output([str(exe),'--version'],text=True).splitlines()[0],
 threads=int(status['Threads']),nice=os.getpriority(os.PRIO_PROCESS,pid),
 ionice=subprocess.check_output(['/usr/bin/ionice','-p',str(pid)],text=True).strip(),open_model_files=opened,
 scope='Process/binary/open-descriptor metadata only; no read via proc fd, no model payload reread')
assert report['threads']==1 and report['nice']==10
(job/'checksum-process-provenance.json').write_bytes((json.dumps(report,indent=2)+'\n').encode());print(json.dumps(report))
