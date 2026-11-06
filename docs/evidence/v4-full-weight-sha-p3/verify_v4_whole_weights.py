"""Single serial coreutils whole-file verifier; writes evidence only to job root."""
from pathlib import Path
import datetime,hashlib,json,os,platform,signal,subprocess,sys,time,traceback

MODEL=Path('/mnt/raid/models/DeepSeek-V4-Pro')
JOB=Path(sys.argv[1]).resolve()
REV='b5968e9190ef611bbf34a7229255be88a0e937c1'
EXPECTED_SHA='6839fc2194f14edbb562a974a903d5c603597df425bd219f1fc345077417bece'
OFFICIAL_SHA='c44f7a4f5e44a9b9ed78f83316bf59b267212e7353d688e97470fa243c4a3b3c'
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,data):
 p=JOB/name;tmp=p.with_suffix(p.suffix+'.tmp')
 tmp.write_bytes((json.dumps(data,indent=2)+'\n').encode());os.replace(tmp,p)
def ident(pid):
 p=Path('/proc')/str(pid)
 return dict(pid=pid,start_ticks=int((p/'stat').read_text().rsplit(')',1)[1].split()[19]))
def snapshot(rows):
 result=[]
 assert not MODEL.is_symlink()
 assert sorted(p.name for p in MODEL.glob('*.safetensors'))==sorted(r['file'] for r in rows)
 for row in rows:
  p=MODEL/row['file'];st=p.lstat()
  assert p.is_file() and not p.is_symlink() and st.st_size==row['bytes'],row['file']
  result.append(dict(file=p.name,realpath=str(p.resolve()),dev=st.st_dev,inode=st.st_ino,
   size=st.st_size,mtime_ns=st.st_mtime_ns,ctime_ns=st.st_ctime_ns,mode=st.st_mode))
 return result
start=time.monotonic();child=None
try:
 assert JOB.parent==Path('/home/boyjiang/sllm-v4-pro/evidence') and JOB.name.startswith('full-weight-sha-')
 expected=JOB/'expected-weight-files.json';official=JOB/'official-model-info.json'
 assert digest(expected)==EXPECTED_SHA and digest(official)==OFFICIAL_SHA
 doc=json.loads(expected.read_bytes());rows=doc['files'];meta=json.loads(official.read_bytes())
 assert doc['revision']==REV==meta['sha'] and len(rows)==64 and len({r['file'] for r in rows})==64
 official_rows={r['rfilename']:r for r in meta['siblings'] if r['rfilename'].endswith('.safetensors')}
 assert set(official_rows)=={r['file'] for r in rows}
 for row in rows:
  ref=official_rows[row['file']]
  assert Path(row['file']).name==row['file'] and row['bytes']==ref['size']==ref['lfs']['size']
  assert row['expected_file_sha256']==ref['lfs']['sha256']
 check=''.join(r['expected_file_sha256']+'  '+r['file']+'\n' for r in rows).encode()
 assert (JOB/'expected-whole-files.sha256').read_bytes().replace(b'\r\n',b'\n')==check
 assert sum(r['bytes'] for r in rows)==864721029744
 before=snapshot(rows);write('before-files.json',before)
 cmd=['/usr/bin/ionice','-c','3','/usr/bin/nice','-n','10','/usr/bin/stdbuf','-oL','/usr/bin/sha256sum','--check',str(JOB/'expected-whole-files.sha256')]
 boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 env=dict(os.environ,LC_ALL='C')
 with (JOB/'sha256sum.stdout.log').open('xb') as out,(JOB/'sha256sum.stderr.log').open('xb') as err:
  child=subprocess.Popen(cmd,cwd=MODEL,stdout=out,stderr=err,env=env)
  handle=dict(schema=1,started_utc=utc(),host=platform.node(),boot_id=boot,supervisor=ident(os.getpid()),
   checksum_process=ident(child.pid),model_path=str(MODEL),revision=REV,command=cmd,
   expected_manifest_sha256=EXPECTED_SHA,official_metadata_sha256=OFFICIAL_SHA,
   runner_sha256=digest(Path(__file__)),logical_total_file_bytes=doc['total_file_bytes'],
   scope='CPU serial read-only whole-file verification, no installs, model/GPU or Store conversion',
   cache_note='This read changes client/server cache; rchar is logical read traffic, not storage/network bytes')
  write('handle.json',handle)
  last_stat=start
  while child.poll() is None:
   elapsed=time.monotonic()-start;lines=(JOB/'sha256sum.stdout.log').read_text().splitlines()
   ok=[r for r in rows if r['file']+': OK' in lines]
   try: io=dict(line.split(': ',1) for line in Path(f'/proc/{child.pid}/io').read_text().splitlines())
   except FileNotFoundError: io={}
   write('progress.json',dict(state='running',observed_utc=utc(),elapsed_seconds=elapsed,
    checked_files=len(lines),matched_files=len(ok),matched_file_bytes=sum(r['bytes'] for r in ok),
    checksum_process=handle['checksum_process'],proc_io=io))
   if time.monotonic()-last_stat>=60:
    assert snapshot(rows)==before,'Weight file identity changed during verification'
    last_stat=time.monotonic()
   if elapsed>24*3600: raise TimeoutError('24-hour verifier bound exceeded')
   time.sleep(10)
  code=child.wait()
 after=snapshot(rows);write('after-files.json',after)
 lines=(JOB/'sha256sum.stdout.log').read_text().splitlines()
 outcomes=[dict(file=r['file'],expected_file_sha256=r['expected_file_sha256'],bytes=r['bytes'],
  matched=lines.count(r['file']+': OK')==1) for r in rows]
 stable=after==before;passed=code==0 and stable and len(lines)==64 and all(r['matched'] for r in outcomes)
 write('result.json',dict(state='complete' if passed else 'failed',completed_utc=utc(),exit_code=code,
  elapsed_seconds=time.monotonic()-start,weight_file_content_verified=passed,source_revision=REV,
  verified_files=sum(r['matched'] for r in outcomes),expected_files=64,total_file_bytes=doc['total_file_bytes'],
  before_after_file_state_equal=stable,files=outcomes,
  whole_repository_revision_verified=False,note='Weights only; nonweight .gitattributes differs. Stable stat is not a writer exclusion lease; later loading must keep inputs sealed.',
  gpu_execution=False,environment_installation=False))
 print('Terminal',passed,code,utc(),flush=True)
 sys.exit(0 if passed else 1)
except BaseException as exc:
 if child is not None and child.poll() is None:
  child.terminate()
  try: child.wait(timeout=30)
  except subprocess.TimeoutExpired:child.kill();child.wait()
 write('failure.json',dict(state='failed',utc=utc(),elapsed_seconds=time.monotonic()-start,
  error_type=type(exc).__name__,error=str(exc),traceback=traceback.format_exc(),weight_file_content_verified=False))
 raise
