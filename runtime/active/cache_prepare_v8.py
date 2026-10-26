"""Place only this hot-reader process on a node with room for the full input."""
import os
import pathlib
import ctypes
import gzip
import hashlib
import json

from cache_prepare_v6 import numa_receipt as inherited_receipt

MARGIN_BYTES = 64*1024**3
LSB = bytes(value & 1 for value in range(256))


def cpu_list(text):
    cpus=set()
    for item in text.strip().split(','):
        ends=list(map(int,item.split('-')))
        cpus.update(range(ends[0],ends[-1]+1))
    return cpus


def choose_node(node_meminfo, required):
    candidates=[]
    for name, text in node_meminfo.items():
        fields={line.split()[2].rstrip(':'):int(line.split()[3])*1024
                for line in text.splitlines() if len(line.split())>=5}
        capacity=fields['MemFree']+fields['FilePages']
        if capacity >= required+MARGIN_BYTES:
            candidates.append((capacity,name))
    assert candidates, 'no NUMA node has the required reclaimable capacity and margin'
    capacity,name=max(candidates)
    return name,capacity


def numa_receipt():
    result=inherited_receipt()
    result['policy']='inherited memory policy unchanged; only hot reader CPU affinity may be restricted; no per-file physical-node guarantee'
    result['vmstat']=pathlib.Path('/proc/vmstat').read_text()
    result['node_vmstat']={p.parent.name:p.read_text() for p in pathlib.Path('/sys/devices/system/node').glob('node[0-9]*/vmstat')}
    return result


def residency(path):
    libc=ctypes.CDLL(None,use_errno=True)
    libc.mmap.restype=ctypes.c_void_p
    libc.mmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_long]
    libc.mincore.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_void_p]
    libc.munmap.argtypes=[ctypes.c_void_p,ctypes.c_size_t]
    size=path.stat().st_size
    count=(size+os.sysconf('SC_PAGE_SIZE')-1)//os.sysconf('SC_PAGE_SIZE')
    with path.open('rb') as stream:
        address=libc.mmap(None,size,0,2,stream.fileno(),0)
        assert address != ctypes.c_void_p(-1).value
        try:
            vector=(ctypes.c_ubyte*count)()
            assert libc.mincore(address,size,vector)==0
            return bytes(vector)
        finally:
            assert libc.munmap(address,size)==0


def prepare_hot_placement(paths, root, node_meminfo, dest):
    required=sum(path.stat().st_size for path in paths)
    name,capacity=choose_node(node_meminfo,required)
    prior=set(os.sched_getaffinity(0))
    actual_cpu_list=(pathlib.Path('/sys/devices/system/node')/name/'cpulist').read_text()
    cpus=cpu_list(actual_cpu_list) & prior
    assert cpus
    for path in paths:
        assert path.resolve().is_relative_to(root/'checkpoint')
        assert path.stat().st_uid == os.getuid()
    os.sched_setaffinity(0,cpus)
    assert set(os.sched_getaffinity(0))==cpus
    record={'node':name,'capacity_proxy_bytes':capacity,'required_bytes':required,
            'margin_bytes':MARGIN_BYTES,'reader_cpu_affinity':sorted(cpus),
            'prior_cpu_affinity':sorted(prior),'sysfs_cpu_list':actual_cpu_list,
            'source_files_advised_dontneed':len(paths),
            'scope':'per-process CPU affinity and own checkpoint cache preparation; capacity proxy is not a reservation or proof of residency; full mincore still required'}
    (dest/'hot-placement.json').write_text(json.dumps(record,indent=2))
    for path in paths:
        with path.open('rb') as stream:
            os.posix_fadvise(stream.fileno(),0,0,os.POSIX_FADV_DONTNEED)
    vectors=dest/'eviction-mincore'
    vectors.mkdir()
    rows=[]
    with (dest/'eviction-mincore.jsonl').open('x') as stream:
        for number,path in enumerate(paths):
            flags=residency(path)
            resident=flags.translate(LSB).count(b'\x01')
            (vectors/f'{number}.gz').write_bytes(gzip.compress(flags,compresslevel=1,mtime=0))
            row={'source_path':str(path),'pages':len(flags),'resident_pages':resident,
                 'vector':f'eviction-mincore/{number}.gz','vector_sha256':hashlib.sha256(flags).hexdigest()}
            stream.write(json.dumps(row)+'\n')
            stream.flush()
            rows.append(row)
    assert all(row['resident_pages']==0 for row in rows), 'hot placement eviction was incomplete'
    record['eviction_resident_pages']=sum(row['resident_pages'] for row in rows)
    return record
