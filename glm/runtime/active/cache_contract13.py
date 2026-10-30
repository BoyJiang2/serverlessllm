"""Frozen cache input closure; metadata identity reads precede eviction/warming."""
import hashlib,json,pathlib

def frozen_paths(base,manifest):
    data=json.loads(manifest.read_text())
    rows=data['files']; names=[r['path'] for r in rows]
    if len(names)!=148 or len(set(names))!=148: raise RuntimeError('cache input count differs')
    weights={name for name in names if name.endswith('.safetensors')}
    if len(weights)!=142 or weights!={p.name for p in base.glob('model-*.safetensors')}: raise RuntimeError('weight input closure differs')
    from workspace import workspace
    official_path=workspace()/'protocol/run-manifest.json'
    if hashlib.sha256(official_path.read_bytes()).hexdigest()!=data['source_manifest_sha256']: raise RuntimeError('official source manifest differs')
    official={r['rfilename']:r for r in json.loads(official_path.read_text())['weight_files']}
    verified_path=base/'VERIFIED.json'
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


def probe_cold_boundary(directory,metadata):
    """Linux metadata-sized control, never a model/cache performance sample."""
    import ctypes,os
    size=metadata.stat().st_size
    if not 0<size<1024**2:raise RuntimeError('cache probe requires small metadata only')
    payload=metadata.read_bytes()
    if len(payload)!=size:raise RuntimeError('cache probe metadata size changed')
    directory.mkdir()
    path=directory/'metadata-copy.json'
    page_size=os.sysconf('SC_PAGE_SIZE');pages=(len(payload)+page_size-1)//page_size
    vectors=[]
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mmap.restype=ctypes.c_void_p
    libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
    libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
    libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    def resident():
        with path.open('rb') as stream:
            address=libc.mmap(None,len(payload),0,2,stream.fileno(),0)
            if address==ctypes.c_void_p(-1).value:raise OSError(ctypes.get_errno(),'probe mmap failed')
            try:
                vector=(ctypes.c_ubyte*pages)()
                if libc.mincore(address,len(payload),vector):raise OSError(ctypes.get_errno(),'probe mincore failed')
                vectors.append(bytes(vector).hex())
                return sum(value&1 for value in vector)
            finally:
                if libc.munmap(address,len(payload)):raise OSError(ctypes.get_errno(),'probe munmap failed')
    def evict():
        with path.open('rb') as stream:os.posix_fadvise(stream.fileno(),0,len(payload),os.POSIX_FADV_DONTNEED)
    try:
        with path.open('xb') as stream:stream.write(payload);stream.flush();os.fsync(stream.fileno())
        if path.stat().st_dev!=metadata.stat().st_dev:raise RuntimeError('probe is not on input local filesystem')
        evict();initial=resident()
        if initial!=0:raise RuntimeError('probe initial eviction not cold')
        if path.read_bytes()!=payload:raise RuntimeError('probe metadata changed')
        stale=resident()
        if stale<=0:raise RuntimeError('metadata reread failed to invalidate old cold evidence')
        # Fixed order: other identity read first, then target eviction/final full mincore.
        if path.read_bytes()!=payload:raise RuntimeError('probe metadata changed')
        evict();fixed=resident()
        if fixed!=0:raise RuntimeError('fixed cache order did not restore cold boundary')
    finally:
        path.unlink(missing_ok=True);directory.rmdir()
    return dict(version=1,metadata_sha256=hashlib.sha256(payload).hexdigest(),metadata_bytes=len(payload),
        pages=pages,initial_resident_pages=initial,old_order_resident_pages=stale,
        fixed_order_resident_pages=fixed,mincore_vectors_hex=vectors,cleanup_complete=True)
