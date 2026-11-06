from pathlib import Path
import subprocess,json,hashlib,datetime
root=Path('/home/boyjiang/sllm-v4-pro/code');base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip();names=subprocess.check_output(['git','-C',str(root),'ls-files','-z','sllm_store/csrc']).decode().rstrip('\0').split('\0')
names += ['sllm_store/setup.py','sllm_store/CMakeLists.txt','sllm_store/pyproject.toml','sllm_store/requirements-v4.txt','sllm_store/requirements-v4-build.txt','sllm_store/MANIFEST.in']
rows=[]
for name in names:
 data=subprocess.check_output(['git','-C',str(root),'show',head+':'+name]);rows.append(dict(path=name,sha256=hashlib.sha256(data).hexdigest()))
(base/'store-native-source-inputs.json').write_text(json.dumps(dict(source_commit=head,files=rows),indent=2)+'\n');print(json.dumps(dict(source_commit=head,native_source_files=len(rows))))
