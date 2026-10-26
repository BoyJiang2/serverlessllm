"""Frozen cache input closure; metadata identity reads precede eviction/warming."""
import hashlib,json,pathlib

def frozen_paths(base,manifest):
    data=json.loads(manifest.read_text())
    rows=data['files']; names=[r['path'] for r in rows]
    if len(names)!=148 or len(set(names))!=148: raise RuntimeError('cache input count differs')
    weights={name for name in names if name.endswith('.safetensors')}
    if len(weights)!=142 or weights!={p.name for p in base.glob('model-*.safetensors')}: raise RuntimeError('weight input closure differs')
    official_path=manifest.parent/'evidence/run-manifest.json'
    if hashlib.sha256(official_path.read_bytes()).hexdigest()!=data['source_manifest_sha256']: raise RuntimeError('official source manifest differs')
    official={r['rfilename']:r for r in json.loads(official_path.read_text())['weight_files']}
    verified_path=manifest.parent/'evidence/checkpoint-VERIFIED.json'
    if hashlib.sha256(verified_path.read_bytes()).hexdigest()!=data['verified_manifest_sha256']: raise RuntimeError('historical verification receipt differs')
    verified={r[0]:r for r in json.loads(verified_path.read_text())['hashes']}
    for row in rows:
        if row['path'] in weights:
            reference=official[row['path']]; historical=verified[row['path']]
            if reference['size']!=row['bytes'] or reference['lfs']['sha256']!=row['expected_sha256'] or historical[1:4]!=[row['bytes'],row['expected_sha256'],True]: raise RuntimeError('LFS or historical weight identity differs')
    paths=[]
    for row in rows:
        name=row['path']; path=base/name
        if pathlib.PurePosixPath(name).name!=name or path.is_symlink() or not path.is_file() or path.stat().st_size!=row['bytes']: raise RuntimeError('input identity/size differs: '+name)
        if 'hash_kind' in row:
            if row['hash_kind'] not in ('sha256','git_blob_sha1'): raise RuntimeError('unknown metadata hash kind')
            payload=path.read_bytes()
            actual=hashlib.sha256(payload).hexdigest() if row['hash_kind']=='sha256' else hashlib.sha1(b'blob '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
            if actual!=row['expected_hash']: raise RuntimeError('metadata hash differs: '+name)
        paths.append(path)
    return paths
