from pathlib import Path,PurePosixPath
import tarfile,json,hashlib,datetime,os
os.umask(0o002);base=Path('/logs');receipt=json.loads((base/'python-archive-receipt.json').read_bytes());archive=Path(receipt['archive']);assert hashlib.sha256(archive.read_bytes()).hexdigest()==receipt['sha256']
matched=[];different=[];missing=[];skipped_pyc=0
with tarfile.open(archive,'r:gz') as tar:
 for m in tar:
  if not m.isfile():continue
  relative=PurePosixPath(m.name).relative_to('python');assert '..' not in relative.parts
  if relative.suffix=='.pyc':skipped_pyc+=1;continue
  expected=hashlib.sha256(tar.extractfile(m).read()).hexdigest();p=Path('/opt/python').joinpath(*relative.parts)
  if not p.exists():missing.append(str(relative));continue
  actual=hashlib.sha256(p.read_bytes()).hexdigest()
  row=dict(path=str(relative),sha256=expected)
  if actual!=expected:different.append(dict(row,installed_sha256=actual))
  else:matched.append(row)
critical=['bin/python3.12','lib/libpython3.12.so.1.0'];assert all(any(r['path']==n for r in matched) for n in critical)
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),archive_sha256=receipt['sha256'],regular_non_pyc_files_matched=len(matched),different=different,missing=missing,skipped_pyc=skipped_pyc,critical_files_match=True,files=matched,scope='Installed private CPython bytes compared with official fixed UV metadata archive; excludes mutable pyc, no GPU/model')
(base/'python-installed-archive-comparison.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k!='files'}))
