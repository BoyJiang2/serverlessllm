from pathlib import Path
import hashlib,json
work=Path('work/v4-weight-provenance')
api=json.loads((work/'official-model-info.json').read_bytes());old=json.loads(Path('work/expected-local-v4-assets.json').read_bytes())
rows={r['rfilename']:r for r in api['siblings']}
weights=[r for r in api['siblings'] if r['rfilename'].endswith('.safetensors')]
assert len(weights)==64
files=[dict(file=r['rfilename'],bytes=r['size'],expected_file_sha256=r['lfs']['sha256'],git_lfs_pointer_blob=r['blobId']) for r in sorted(weights,key=lambda r:r['rfilename'])]
normalized=dict(schema=1,model=api['id'],revision=api['sha'],hash_scope='whole_file_including_safetensors_header',
 official_metadata_sha256=hashlib.sha256((work/'official-model-info.json').read_bytes()).hexdigest(),
 expected_weight_files=64,total_file_bytes=sum(r['bytes'] for r in files),files=files)
(work/'expected-weight-files.json').write_bytes((json.dumps(normalized,indent=2)+'\n').encode())
(work/'expected-whole-files.sha256').write_bytes(''.join(r['expected_file_sha256']+'  '+r['file']+'\n' for r in files).encode())
Path(work/'expected-local-v4-assets.json').write_bytes(Path('work/expected-local-v4-assets.json').read_bytes())
Path(work/'previous-local-model-assets-receipt.json').write_bytes(Path('outputs/local-model-assets-receipt.json').read_bytes())
s=Path('work/inspect_existing_v4_assets.py').read_text(encoding='utf-8-sig')
s=s.replace("out=Path('/home/boyjiang/sllm-v4-pro/evidence')", "out=Path('/home/boyjiang/sllm-v4-pro/evidence/v4-weight-provenance-20261106')")
s=s.replace('assets=[]', '''previous=json.loads((out/'previous-local-model-assets-receipt.json').read_text())
previous_shards={r['file']:r for r in previous['shards']}
assert sum(r['bytes'] for r in expected['assets'])+expected['index']['size']+previous['header_byte_count']<64*1024*1024
assets=[]''')
s=s.replace('data=path.read_bytes();sha=', "with path.open('rb') as stream:data=stream.read(row['bytes']+1)\n    sha=")
s=s.replace('sha256=sha,matches=sha==', "sha256=sha,git_blob_content=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\\0'+data).hexdigest(),matches=sha==")
s=s.replace("data=index_path.read_bytes()", "\nwith index_path.open('rb') as stream:data=stream.read(expected['index']['size']+1)")
s=s.replace("assert len(declared)==64 and set(weight_map.values())==set(declared)", "assert len(declared)==64 and set(weight_map.values())==set(declared)\nassert {p.name for p in root.glob('*.safetensors')}==set(declared)")
s=s.replace('assert 0<n<=16*1024*1024 and n+8<before.st_size','assert n+8==previous_shards[name][\'header_bytes\'] and 0<n<=16*1024*1024 and n+8<before.st_size')
s += '''
# Complete official non-weight inventory; runtime hashes already captured above.
import importlib.util,os,stat
api=json.loads((out/'official-model-info.json').read_text())
runtime={r['file']:r for r in assets}
nonweights=[];inventory=[]
for row in api['siblings']:
    name=row['rfilename'];path=root/name
    if path.exists():
        info=path.stat()
        inventory.append(dict(file=name,bytes=info.st_size,mtime_ns=info.st_mtime_ns,ctime_ns=info.st_ctime_ns,
                              inode=info.st_ino,device=info.st_dev,symlink=path.is_symlink(),realpath=str(path.resolve())))
    if name.endswith('.safetensors'):continue
    status=dict(file=name,runtime_asset=name in runtime or name=='model.safetensors.index.json',present=path.is_file())
    if not status['present']:
        nonweights.append(status);continue
    if name in runtime:
        entry=runtime[name];sha=entry['sha256'];blob=entry['git_blob_content'];size=entry['bytes']
    elif name=='model.safetensors.index.json':
        sha=index_sha;blob=None;size=path.stat().st_size
    else:
        if path.stat().st_size>32*1024*1024:raise ValueError('unexpected oversized metadata file')
        with path.open('rb') as stream:content=stream.read(32*1024*1024+1)
        if len(content)>32*1024*1024:raise ValueError('metadata read cap exceeded')
        sha=hashlib.sha256(content).hexdigest();blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\\0'+content).hexdigest();size=len(content)
    status.update(bytes=size,sha256=sha,git_blob_content=blob,
                  matches_fixed_revision=(size==row['size'] and (sha==row['lfs']['sha256'] if row.get('lfs') else blob==row['blobId'])))
    nonweights.append(status)
report=dict(time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),host=os.uname().nodename,
 model_path=str(root),realpath=str(root.resolve()),expected_revision=api['sha'],weight_files_stat_checked=64,
 payload_content_sha256_verified=False,weight_hash_scope='whole_file_including_safetensors_header',
 runtime_assets_all_match=all(r.get('matches_fixed_revision') is True for r in nonweights if r['runtime_asset']),
 repository_nonweights_all_match=all(r.get('matches_fixed_revision') is True for r in nonweights),
 headers_match_previous=all(r['header_sha256']==previous_shards[r['file']]['header_sha256'] for r in shards),
 nonweight_files=nonweights,inventory=inventory,
 cpu_module_discovery_only={p:bool(importlib.util.find_spec(p)) for p in ('requests','tokenizers','transformers')},
 scope='Fresh small-file contents, index, exact prior header lengths and stat only; no requested tensor payload, model imports or GPU; does affect page cache')
(out/'current-local-metadata.json').write_text(json.dumps(report,indent=2)+'\\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('nonweight_files','inventory')}))
print('nonweight differences:',[r['file'] for r in nonweights if not r.get('matches_fixed_revision')])
'''
(work/'inspect_current_v4_metadata.py').write_bytes(s.encode())
