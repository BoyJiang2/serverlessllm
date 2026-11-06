"""Fresh post-weight nonweight asset hashes; no imports from model directory."""
from pathlib import Path
import datetime,hashlib,json,sys
job=Path(sys.argv[1]).resolve();model=Path('/mnt/raid/models/DeepSeek-V4-Pro')
assert json.loads((job/'terminal-audit.json').read_bytes())['verified_weight_files']==64
raw=(job/'official-model-info.json').read_bytes();assert hashlib.sha256(raw).hexdigest()=='c44f7a4f5e44a9b9ed78f83316bf59b267212e7353d688e97470fa243c4a3b3c'
meta=json.loads(raw);rows=[]
runtime={'config.json','generation_config.json','tokenizer.json','tokenizer_config.json','model.safetensors.index.json'}
requested=0
for entry in meta['siblings']:
 name=entry['rfilename']
 if name.endswith('.safetensors'):continue
 relative=Path(name);assert not relative.is_absolute() and '..' not in relative.parts
 p=model/relative;assert p.resolve().is_relative_to(model) and not p.is_symlink()
 before=p.stat();assert before.st_size<64*1024**2
 data=p.read_bytes();after=p.stat();requested+=len(data)
 fields=('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')
 stable=all(getattr(before,k)==getattr(after,k) for k in fields)
 sha=hashlib.sha256(data).hexdigest();blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
 matches=len(data)==entry['size'] and (sha==entry['lfs']['sha256'] if 'lfs' in entry else blob==entry['blobId'])
 rows.append(dict(file=name,bytes=len(data),sha256=sha,git_blob_content=blob,matches=matches,stable=stable,
                  runtime_input=name in runtime or name.startswith('encoding/'),
                  file_state={k:getattr(after,k) for k in fields}))
assert len(rows)==27 and all(r['stable'] for r in rows)
assert sum(r['runtime_input'] for r in rows)==16
report=dict(observed_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),revision=meta['sha'],
 nonweight_files=len(rows),matched_nonweight_files=sum(r['matches'] for r in rows),
 runtime_input_files=16,runtime_inputs_all_match=all(r['matches'] for r in rows if r['runtime_input']),
 requested_nonweight_file_bytes=requested,scope='Read-only complete small nonweight files after full weight hash; no tensor payload reread, no code/model import. Cache not neutral.',files=rows)
assert report['runtime_inputs_all_match']
(job/'post-weight-assets.json').write_bytes((json.dumps(report,indent=2)+'\n').encode())
print(json.dumps({k:v for k,v in report.items() if k!='files'}))
