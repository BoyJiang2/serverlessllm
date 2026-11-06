from pathlib import Path
import collections,datetime,hashlib,json,math,struct
root=Path('/mnt/raid/models/DeepSeek-V4-Pro')
out=Path('/home/boyjiang/sllm-v4-pro/evidence/v4-weight-provenance-20261106')
expected=json.loads((out/'expected-local-v4-assets.json').read_text())
previous=json.loads((out/'previous-local-model-assets-receipt.json').read_text())
previous_shards={r['file']:r for r in previous['shards']}
assert sum(r['bytes'] for r in expected['assets'])+expected['index']['size']+previous['header_byte_count']<64*1024*1024
assets=[]
for row in expected['assets']:
    path=root/row['source_path']
    if not path.is_file():
        assets.append(dict(file=row['source_path'],matches=False,missing=True));continue
    with path.open('rb') as stream:data=stream.read(row['bytes']+1)
    sha=hashlib.sha256(data).hexdigest()
    assets.append(dict(file=row['source_path'],bytes=len(data),sha256=sha,git_blob_content=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),matches=sha==row['sha256'] and len(data)==row['bytes']))
index_path=root/'model.safetensors.index.json';
with index_path.open('rb') as stream:data=stream.read(expected['index']['size']+1)
index_sha=hashlib.sha256(data).hexdigest();index=json.loads(data)
weight_map=index['weight_map'];declared={r['file']:r for r in expected['weights']}
assert len(declared)==64 and set(weight_map.values())==set(declared)
assert {p.name for p in root.glob('*.safetensors')}==set(declared)
assert index_sha==expected['index']['lfs']['sha256']
by_shard=collections.defaultdict(set)
for name,shard in weight_map.items():by_shard[shard].add(name)
dtypes=collections.Counter();shards=[];payload=0;header_total=0
bits={'BOOL':8,'U8':8,'I8':8,'I16':16,'U16':16,'I32':32,'U32':32,'I64':64,'U64':64,'BF16':16,'F16':16,'F32':32,'F64':64,'F8_E4M3':8,'F8_E5M2':8,'F8_E8M0':8}
unknown=set()
for name,row in declared.items():
    path=root/name;before=path.stat();assert before.st_size==row['bytes']
    with path.open('rb') as f:
        n=struct.unpack('<Q',f.read(8))[0]
        assert n+8==previous_shards[name]['header_bytes'] and 0<n<=16*1024*1024 and n+8<before.st_size
        body=f.read(n);assert len(body)==n
    header=json.loads(body);keys=set(header)-{'__metadata__'}
    assert keys==by_shard[name]
    ranges=[]
    for key in keys:
        entry=header[key];dtype=entry['dtype'];a,b=entry['data_offsets'];shape=entry['shape']
        assert type(a)is int and type(b)is int and 0<=a<=b<=before.st_size-8-n
        assert all(type(d)is int and d>=0 for d in shape)
        if dtype in bits:assert (math.prod(shape)*bits[dtype]+7)//8==b-a
        else:unknown.add(dtype)
        ranges.append((a,b));dtypes[dtype]+=1
    ranges.sort();assert ranges[0][0]==0 and ranges[-1][1]==before.st_size-8-n
    assert all(a[1]==b[0] for a,b in zip(ranges,ranges[1:]))
    after=path.stat();assert (before.st_size,before.st_mtime_ns,before.st_ino)==(after.st_size,after.st_mtime_ns,after.st_ino)
    size=before.st_size-8-n;payload+=size;header_total+=8+n
    shards.append(dict(file=name,bytes=before.st_size,header_bytes=8+n,header_sha256=hashlib.sha256(body).hexdigest(),tensor_count=len(keys),payload_bytes=size,expected_payload_sha256_not_verified=row['lfs_sha256_declared_not_payload_verified']))
assert payload==index['metadata']['total_size']
result=dict(time_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),host='qjhs002',model_path=str(root),expected_hf_revision=expected['revision'],assets=assets,all_frozen_small_assets_match=all(r['matches'] for r in assets),index_sha256=index_sha,index_matches_fixed_revision=True,all_64_shard_sizes_match=True,header_index_tensor_coverage=True,header_byte_count=header_total,tensor_count=len(weight_map),dtype_tensor_counts=dict(dtypes),unknown_dtypes=sorted(unknown),payload_bytes=payload,serialized_file_bytes=sum(r['bytes'] for r in shards),shards=shards,payload_sha256_verified=False,provenance_note=(root/'.mv').read_text() if (root/'.mv').is_file() else None,scope='Read-only existing assets, index and safetensors headers only; no tensor payload hash, model import or GPU execution; header reads affect future cold-cache preparation')
(out/'local-model-assets-receipt.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ('assets','shards')}))
print('small asset mismatches:',[r['file'] for r in assets if not r['matches']])

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
        sha=hashlib.sha256(content).hexdigest();blob=hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest();size=len(content)
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
(out/'current-local-metadata.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('nonweight_files','inventory')}))
print('nonweight differences:',[r['file'] for r in nonweights if not r.get('matches_fixed_revision')])
