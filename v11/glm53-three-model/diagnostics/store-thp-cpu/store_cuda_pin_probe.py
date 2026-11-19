"""Isolated Store CUDA registration probe. The default is a CPU no-touch mock."""

import argparse
import ctypes as c
import hashlib
import json
from pathlib import Path
import re
import socket
import time


PAGE = 4096
CHUNK = 32 * (1 << 20)
CHUNKS = 4
MADV_HUGEPAGE = 14
SMAPS_HEADER = re.compile(r"^([0-9a-f]+)-([0-9a-f]+) ")
SMAPS_FIELDS = ("Rss", "Anonymous", "AnonHugePages", "Locked")
LIBC = c.CDLL(None, use_errno=True)
LIBC.aligned_alloc.argtypes = (c.c_size_t, c.c_size_t)
LIBC.aligned_alloc.restype = c.c_void_p
LIBC.free.argtypes = (c.c_void_p,)
LIBC.free.restype = None
LIBC.madvise.argtypes = (c.c_void_p, c.c_size_t, c.c_int)
LIBC.madvise.restype = c.c_int


def load_cuda(path):
    lib = c.CDLL(path)
    signatures = {
        "cudaGetDeviceCount": (c.POINTER(c.c_int),),
        "cudaSetDevice": (c.c_int,),
        "cudaMalloc": (c.POINTER(c.c_void_p), c.c_size_t),
        "cudaFree": (c.c_void_p,),
        "cudaHostRegister": (c.c_void_p, c.c_size_t, c.c_uint),
        "cudaHostUnregister": (c.c_void_p,),
        "cudaMemcpy": (c.c_void_p, c.c_void_p, c.c_size_t, c.c_int),
        "cudaDeviceSynchronize": (),
        "cudaGetErrorString": (c.c_int,),
    }
    for name, params in signatures.items():
        function = getattr(lib, name)
        function.argtypes = params
        function.restype = c.c_char_p if name == "cudaGetErrorString" else c.c_int
    return lib


def emit(**event):
    print(json.dumps(dict(monotonic_ns=time.monotonic_ns(), wall_ns=time.time_ns(), **event)), flush=True)


def cuda_call(cuda, name, *args, chunk=None):
    start = time.monotonic_ns()
    code = getattr(cuda, name)(*args) if cuda is not None else None
    duration = time.monotonic_ns() - start
    message = cuda.cudaGetErrorString(code) if code else None
    emit(event="cuda_call", operation=name, duration_ns=duration, chunk=chunk,
         code=code, error=message.decode(errors="replace") if message else None,
         mock=cuda is None)
    return code


def require_cuda(cuda, name, *args, chunk=None):
    code = cuda_call(cuda, name, *args, chunk=chunk)
    if code:
        raise RuntimeError(f"{name} failed with CUDA status {code}")


def selected_smaps(buffers):
    values = dict.fromkeys(SMAPS_FIELDS, 0)
    covered = 0
    vma_bytes = 0
    selected = False
    for line in Path("/proc/self/smaps").read_text().splitlines():
        header = SMAPS_HEADER.match(line)
        if header:
            first, last = (int(address, 16) for address in header.groups())
            overlap = sum(max(0, min(last, ptr + CHUNK) - max(first, ptr)) for ptr in buffers)
            selected = bool(overlap)
            if selected:
                covered += overlap
                vma_bytes += last - first
        elif selected:
            field, _, value = line.partition(":")
            if field in values:
                values[field] += int(value.split()[0])
    if covered != len(buffers) * CHUNK:
        raise RuntimeError(f"smaps covered {covered} bytes for {len(buffers)} live chunks")
    return dict(covered_bytes=covered, vma_bytes=vma_bytes, values_kib=values)


def snapshot(event, buffers, cgroup, chunk=None):
    sample_start_ns = time.monotonic_ns()
    meminfo = Path("/proc/meminfo").read_text()
    memory = {}
    for name in ("memory.current", "memory.stat"):
        try:
            memory[name] = (cgroup / name).read_text()
        except OSError as exc:
            memory[name] = {"errno": exc.errno}
    emit(event=event, chunk=chunk, sample_start_ns=sample_start_ns,
         s_unreclaim_kib=next(int(line.split()[1]) for line in meminfo.splitlines()
                              if line.startswith("SUnreclaim:")),
         smaps=selected_smaps(buffers), meminfo=meminfo,
         vmstat=Path("/proc/vmstat").read_text(), cgroup_memory=memory)


def prefault(ptr, advise):
    if advise and LIBC.madvise(ptr, CHUNK, MADV_HUGEPAGE) != 0:
        code = c.get_errno()
        raise OSError(code, "MADV_HUGEPAGE failed")
    data = (c.c_ubyte * CHUNK).from_address(ptr)
    for offset in range(0, CHUNK, PAGE):
        data[offset] = 0x5A


def run(mode, cuda, cudart):
    cgroup_text = Path("/proc/self/cgroup").read_text()
    relative = next((line[3:] for line in cgroup_text.splitlines() if line.startswith("0::")), "/")
    cgroup = Path("/sys/fs/cgroup") / relative.lstrip("/")
    emit(event="configuration", node=socket.gethostname(), mode=mode, mock=cuda is None,
         cudart=cudart if cuda is not None else None, chunk_bytes=CHUNK, chunks=CHUNKS,
         cgroup_path=str(cgroup), proc_cgroup=cgroup_text)
    buffers = []
    registered = set()
    scratch = c.c_void_p()
    cleanup_failed = False
    try:
        if cuda is not None:
            count = c.c_int()
            require_cuda(cuda, "cudaGetDeviceCount", c.byref(count))
            if count.value != 1:
                raise RuntimeError(f"expected exactly one visible CUDA device, got {count.value}")
            require_cuda(cuda, "cudaSetDevice", 0)
            require_cuda(cuda, "cudaMalloc", c.byref(scratch), CHUNK)
            readback = (c.c_ubyte * CHUNK)()
            c.memset(readback, 0, CHUNK)
        snapshot("baseline", buffers, cgroup)
        for index in range(CHUNKS):
            ptr = LIBC.aligned_alloc(PAGE, CHUNK)
            if ptr is None:
                raise MemoryError("aligned_alloc failed")
            buffers.append(ptr)
            snapshot("allocated", buffers, cgroup, chunk=index)
            if cuda is not None and mode != "current":
                prefault(ptr, advise=mode == "prefault-thp")
                snapshot("prefaulted", buffers, cgroup, chunk=index)
            require_cuda(cuda, "cudaHostRegister", ptr, CHUNK, 0, chunk=index)
            registered.add(index)
            snapshot("registered", buffers, cgroup, chunk=index)
        if cuda is not None:
            payload = (c.c_ubyte * CHUNK).from_address(buffers[0])
            c.memset(payload, 0x5A, CHUNK)
            snapshot("h2d-ready", buffers, cgroup)
            require_cuda(cuda, "cudaMemcpy", scratch, buffers[0], CHUNK, 1)
            require_cuda(cuda, "cudaDeviceSynchronize")
            snapshot("h2d-complete", buffers, cgroup)
            require_cuda(cuda, "cudaMemcpy", readback, scratch, CHUNK, 2)
            require_cuda(cuda, "cudaDeviceSynchronize")
            digest = hashlib.sha256(readback).hexdigest()
            if digest != hashlib.sha256(payload).hexdigest():
                raise RuntimeError("CUDA readback differs from the complete source chunk")
            emit(event="readback", bytes=CHUNK, sha256=digest)
    finally:
        try:
            for index in sorted(registered, reverse=True):
                code = cuda_call(cuda, "cudaHostUnregister", buffers[index], chunk=index)
                if code:
                    cleanup_failed = True
                else:
                    registered.remove(index)
            if buffers:
                snapshot("unregistered", buffers, cgroup)
        finally:
            for index, ptr in enumerate(buffers):
                if index not in registered:
                    LIBC.free(ptr)
            if scratch.value:
                cleanup_failed |= bool(cuda_call(cuda, "cudaFree", scratch))
        if cleanup_failed:
            raise RuntimeError("CUDA cleanup failed; unsuccessful unregisters remain mapped until process exit")
        snapshot("released", [], cgroup)


if __name__ == "__main__":
    options = argparse.ArgumentParser(description=__doc__)
    options.add_argument("mode", choices=("current", "prefault", "prefault-thp"))
    options.add_argument("--execute-cuda", action="store_true")
    options.add_argument("--cudart", default="libcudart.so.13")
    args = options.parse_args()
    if not Path("/proc/self/smaps").exists() or c.CDLL(None).getpagesize() != PAGE:
        options.error("this fixture requires Linux with 4 KiB base pages")
    run(args.mode, load_cuda(args.cudart) if args.execute_cuda else None, args.cudart)
