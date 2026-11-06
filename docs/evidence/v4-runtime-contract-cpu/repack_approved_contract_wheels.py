"""Repackage changed Python metadata while retaining the proven native payload verbatim."""
from pathlib import Path,PurePosixPath
import os,json,hashlib,zipfile,subprocess,datetime
os.umask(0o002);base=Path('/logs/runtime-contract-20261106/final');base.mkdir(parents=True,exist_ok=True);work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');src=Path('/opt/src/sllm');python='/opt/sllm-v4/bin/python';version='0.8.0+v4pro.4';stage=work/'runtime-contract-approved-wheel-stage';stage.mkdir(exist_ok=False);results=[]
for name,receipt,source,prefix in [('store','build-store-a4-result.json',src/'sllm_store','sllm_store'),('service','build-service-a2-result.json',src,'sllm')]:
 old=json.loads((Path('/logs')/receipt).read_bytes())['wheels'][0];assert hashlib.sha256(Path(old['file']).read_bytes()).hexdigest()==old['sha256']
 metadata=stage/(name+'-metadata');metadata.mkdir();tree=stage/(name+'-tree');tree.mkdir();output=work/'native-wheels'/('runtime-contract-approved-'+name);output.mkdir(exist_ok=False)
 with (base/('repack-'+name+'.log')).open('x') as log:
  cmd=[python,'setup.py','dist_info','--output-dir',str(metadata)];r=subprocess.run(cmd,cwd=source,stdout=log,stderr=subprocess.STDOUT,env=dict(os.environ,SLLM_BUILD_PROFILE='v4-pro'));assert r.returncode==0
  infos=list(metadata.glob('*.dist-info'));assert len(infos)==1 and version in infos[0].name
  newinfo=tree/infos[0].name;newinfo.mkdir();oldnative={};count=0
  with zipfile.ZipFile(old['file']) as z:
   wheelname=next(n for n in z.namelist() if n.endswith('.dist-info/WHEEL'));wheeldata=z.read(wheelname)
   for member in z.infolist():
    p=PurePosixPath(member.filename);assert not p.is_absolute() and '..' not in p.parts and '.data' not in p.parts[0]
    if '.dist-info' in p.parts[0] or member.is_dir():continue
    target=tree.joinpath(*p.parts);target.parent.mkdir(parents=True,exist_ok=True);data=z.read(member)
    if p.parts[0]==prefix and target.suffix in ('.py','.json','.proto') or target.name=='py.typed':
     sourcefile=(source/prefix).joinpath(*p.parts[1:]);data=sourcefile.read_bytes();count+=1
    target.write_bytes(data)
    if '.so' in target.name:oldnative[member.filename]=hashlib.sha256(z.read(member)).hexdigest()
  for p in infos[0].rglob('*'):
   if p.is_file():q=newinfo/p.relative_to(infos[0]);q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(p.read_bytes())
  (newinfo/'WHEEL').write_bytes(wheeldata)
  r=subprocess.run([python,'-m','wheel','pack',str(tree),'--dest-dir',str(output)],stdout=log,stderr=subprocess.STDOUT);assert r.returncode==0
 wheels=list(output.glob('*.whl'));assert len(wheels)==1
 with zipfile.ZipFile(wheels[0]) as z:
  assert {n:hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if '.so' in PurePosixPath(n).name}==oldnative
  meta=z.read(newinfo.name+'/METADATA').decode();assert 'Version: '+version in meta
  if name=='service':assert 'serverless-llm-store=='+version in meta
 result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=0,wheels=[dict(file=str(wheels[0]),bytes=wheels[0].stat().st_size,sha256=hashlib.sha256(wheels[0].read_bytes()).hexdigest())],original_qualified_wheel=old,reused_native_binaries=oldnative,native_binary_bytes_unchanged=True,source_files_replaced=count,scope='Python/configuration and metadata repack only; existing native payload retained exactly, no compilation, model or GPU execution')
 (base/('repack-'+name+'-result.json')).write_text(json.dumps(result,indent=2)+'\n');results.append(result)
print(json.dumps([dict(wheels=r['wheels'],native_binary_bytes_unchanged=True) for r in results]))
