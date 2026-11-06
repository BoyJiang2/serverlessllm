"""Bounded Linux client-page-cache operations on an explicit file manifest.

Deadlines are checked around syscalls, not hard interruption of kernel IO.
Use the existing external guardian for a hard process budget. NFS server cache
and physical storage caches are outside this module's evidence scope.
"""
import ctypes
import errno
import math
import mmap
import os
from pathlib import Path
import stat
import sys
import time
from contextlib import ExitStack

WINDOW_BYTES = 8 << 20
_LIBC = ctypes.CDLL(None, use_errno=True)
_LIBC.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                       ctypes.c_int, ctypes.c_int, ctypes.c_long]
_LIBC.mmap.restype = ctypes.c_void_p
_LIBC.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t,
                          ctypes.POINTER(ctypes.c_ubyte)]
_LIBC.mincore.restype = ctypes.c_int
_LIBC.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
_LIBC.munmap.restype = ctypes.c_int


def _check_deadline(deadline):
    if not math.isfinite(deadline) or time.monotonic() >= deadline:
        raise TimeoutError("file cache deadline expired")


def _identity(info):
    return {"dev": info.st_dev, "ino": info.st_ino, "size": info.st_size,
            "mtime_ns": info.st_mtime_ns, "uid": info.st_uid}


def _trust(info):
    # Conservative: no ownership inference from overflow UID in a user namespace.
    mapping = [tuple(map(int, line.split())) for line in
               Path("/proc/self/uid_map").read_text().splitlines()]
    if mapping != [(0, 0, 4294967295)]:
        raise PermissionError("mincore trust requires full identity UID mapping")
    status = Path("/proc/self/status").read_text().splitlines()
    caps = next(line.split()[1] for line in status if line.startswith("CapEff:"))
    fsuid = int(next(line.split()[4] for line in status if line.startswith("Uid:")))
    cap_fowner = bool(int(caps, 16) & (1 << 3))
    euid = os.geteuid()
    if euid != fsuid:
        raise PermissionError("mincore credential trust requires matching euid/fsuid")
    if euid != info.st_uid and not (euid == 0 and cap_fowner):
        raise PermissionError("mincore ownership/CAP_FOWNER trust not established")
    return {"effective_uid": euid, "filesystem_uid": fsuid, "file_owner_uid": info.st_uid,
            "cap_fowner": cap_fowner, "full_identity_uid_map": True}


def _verify(fd, path, expected):
    current = os.fstat(fd)
    named = path.lstat()
    if (not stat.S_ISREG(named.st_mode) or _identity(current) != expected
            or _identity(named) != expected
            or Path(os.readlink(f"/proc/self/fd/{fd}")).resolve(strict=True) != path):
        raise RuntimeError(f"file identity changed: {path}")


def _residency(fd, size, deadline):
    page = os.sysconf("SC_PAGE_SIZE")
    if ctypes.sizeof(ctypes.c_long) != 8 or WINDOW_BYTES % page:
        raise RuntimeError("unsupported mmap offset/page geometry")
    resident = pages = 0
    for offset in range(0, size, WINDOW_BYTES):
        _check_deadline(deadline)
        length = min(WINDOW_BYTES, size - offset)
        count = (length + page - 1) // page
        address = _LIBC.mmap(None, length, mmap.PROT_READ, mmap.MAP_SHARED, fd, offset)
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), "cache mmap failed")
        try:
            vector = (ctypes.c_ubyte * count)()
            if _LIBC.mincore(address, length, vector) != 0:
                raise OSError(ctypes.get_errno(), "mincore failed")
            resident += sum(value & 1 for value in vector)
            pages += count
        finally:
            result = _LIBC.munmap(address, length)
            if result != 0 and sys.exc_info()[0] is None:
                raise OSError(ctypes.get_errno(), "cache munmap failed")
        _check_deadline(deadline)
    return pages, resident


def _run(paths, allowed_roots, deadline, action):
    _check_deadline(deadline)
    if not isinstance(paths, (list, tuple)) or not 0 < len(paths) <= 1024:
        raise ValueError("provide a nonempty bounded path list")
    if not isinstance(allowed_roots, (list, tuple)) or not allowed_roots:
        raise ValueError("provide explicit allowed roots")
    if not all(Path(root).is_absolute() for root in allowed_roots):
        raise ValueError("allowed roots must be absolute")
    roots = [Path(root).resolve(strict=True) for root in allowed_roots]
    if not all(root.is_dir() for root in roots):
        raise ValueError("allowed roots must be directories")
    rows, opened, seen = [], [], set()
    read_counts = {}
    with ExitStack() as stack:
        for raw in paths:
            _check_deadline(deadline)
            path = Path(raw)
            if not path.is_absolute() or path.is_symlink():
                raise ValueError("absolute nonsymlink files required")
            path = path.resolve(strict=True)
            if not any(path.is_relative_to(root) for root in roots):
                raise ValueError(f"file outside allowed roots: {path}")
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
            stack.callback(os.close, fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f"not a regular file: {path}")
            key = (info.st_dev, info.st_ino)
            if key in seen:
                raise ValueError("duplicate file inode")
            seen.add(key)
            identity = _identity(info)
            _verify(fd, path, identity)
            opened.append((fd, path, identity, _trust(info)))
        for fd, path, identity, trust in opened:
            _check_deadline(deadline)
            _verify(fd, path, identity)
            size = identity["size"]
            read_bytes = 0
            if action == "evict" and size:
                page = os.sysconf("SC_PAGE_SIZE")
                os.posix_fadvise(fd, 0, ((size + page - 1) // page) * page,
                                 os.POSIX_FADV_DONTNEED)
            elif action == "warm" and size:
                buffer = bytearray(min(WINDOW_BYTES, size))
                while read_bytes < size:
                    _check_deadline(deadline)
                    count = os.preadv(fd, [memoryview(buffer)[:min(len(buffer), size-read_bytes)]], read_bytes)
                    _check_deadline(deadline)
                    if count <= 0:
                        raise OSError(errno.EIO, f"short cache warm read: {path}")
                    read_bytes += count
            _verify(fd, path, identity)
            _check_deadline(deadline)
            read_counts[fd] = read_bytes
        # All manifest operations finish before any admission residency query.
        for fd, path, identity, trust in opened:
            _check_deadline(deadline)
            _verify(fd, path, identity)
            if _trust(os.fstat(fd)) != trust:
                raise PermissionError("mincore trust changed")
            pages, resident = _residency(fd, identity["size"], deadline)
            if _trust(os.fstat(fd)) != trust:
                raise PermissionError("mincore trust changed")
            _verify(fd, path, identity)
            _check_deadline(deadline)
            rows.append({"path": str(path), "identity": identity,
                         "pages": pages, "resident_pages": resident,
                         "read_bytes": read_counts[fd], "mincore_trust": trust})
        for fd, path, identity, _ in opened:
            _verify(fd, path, identity)
        _check_deadline(deadline)
    pages = sum(row["pages"] for row in rows)
    resident = sum(row["resident_pages"] for row in rows)
    cold, hot = resident == 0, pages > 0 and resident == pages
    return {"operation": action, "files": rows, "pages": pages,
            "resident_pages": resident, "all_cold": cold, "all_hot": hot,
            "admitted": cold if action == "evict" else hot if action == "warm" else None,
            "scope": "local Linux client page-cache snapshot; NFS server and physical disk uncontrolled",
            "deadline_semantics": "cooperative checks; external guard required for hard syscall/runtime bound"}


def query_cache_state(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "query")


def evict_file_cache(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "evict")


def warm_file_cache(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "warm")
