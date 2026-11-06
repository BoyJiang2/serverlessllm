from pathlib import Path
import subprocess,json,zipfile,hashlib,datetime
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');root=Path('/home/boyjiang/sllm-v4-pro/code');head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip();assert head=='6a12b666bf270a0c9e33a850f47363fd3bcd115c'
rows=[]
for result,prefix in [('build-service-a2-result.json','sllm/'),('build-store-a4-result.json','sllm_store/')]:
 r=json.loads((base/result).read_bytes())['wheels'][0];assert hashlib.sha256(Path(r['file']).read_bytes()).hexdigest()==r['sha256']
 with zipfile.ZipFile(r['file']) as z:
  for name in z.namelist():
   if not name.startswith(prefix) or not name.endswith(('.py','.json','.proto','py.typed')):continue
   relative=name if prefix=='sllm/' else 'sllm_store/'+name
   blob=subprocess.check_output(['git','-C',str(root),'show',head+':'+relative]);data=z.read(name);assert blob==data,relative
   rows.append(dict(path=name,repository_path=relative,sha256=hashlib.sha256(data).hexdigest(),wheel_sha256=r['sha256']))
assert any(r['path']=='sllm_store/vllm_v4_loader.py' for r in rows)
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),source_commit=head,files=rows,scope='All shipped SLLM/Store Python, JSON, proto and typing source files compared with actual Git blobs; native binary/installation compared separately')
(base/'local-wheel-git-source-manifest.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(source_commit=head,matching_source_files=len(rows))))
