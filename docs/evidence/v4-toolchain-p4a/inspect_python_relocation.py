from pathlib import Path
import tarfile,json,difflib,os
os.umask(0o002);base=Path('/logs');d=json.loads((base/'python-installed-archive-comparison.json').read_bytes());a=json.loads((base/'python-archive-receipt.json').read_bytes());parts=[]
with tarfile.open(a['archive'],'r:gz') as tar:
 for r in d['different']:
  original=tar.extractfile('python/'+r['path']).read().decode();actual=(Path('/opt/python')/r['path']).read_text();parts.extend(difflib.unified_diff(original.splitlines(True),actual.splitlines(True),fromfile='archive/'+r['path'],tofile='installed/'+r['path']))
(base/'python-relocation.diff').write_text(''.join(parts));print(''.join(parts)[:12000])
