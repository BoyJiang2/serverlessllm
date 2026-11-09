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


def _run(paths, allowed_roots, deadline, action, *, observation=None, telemetry=None):
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
    read_times = {}
    read_deadline = deadline
    if observation is not None:
        observation["read_phase_begin_ns"] = time.monotonic_ns()
        read_deadline = min(deadline, observation["read_phase_begin_ns"] / 1e9 + 1800)
        if telemetry:
            telemetry("baseline")
            _check_deadline(read_deadline)
            telemetry("read_begin")
            _check_deadline(read_deadline)
    next_telemetry = time.monotonic() + 30
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
            timing = {"path": str(path), "identity": identity,
                      "read_begin_ns": None, "read_end_ns": None, "read_bytes": 0}
            read_times[fd] = timing
            if observation is not None:
                observation["read_files"].append(timing)
            if action == "evict" and size:
                page = os.sysconf("SC_PAGE_SIZE")
                os.posix_fadvise(fd, 0, ((size + page - 1) // page) * page,
                                 os.POSIX_FADV_DONTNEED)
            elif action == "warm" and size:
                buffer = bytearray(min(WINDOW_BYTES, size))
                while read_bytes < size:
                    _check_deadline(read_deadline)
                    if timing["read_begin_ns"] is None:
                        timing["read_begin_ns"] = time.monotonic_ns()
                        if observation is not None and observation["read_begin_ns"] is None:
                            observation["read_begin_ns"] = timing["read_begin_ns"]
                    count = os.preadv(fd, [memoryview(buffer)[:min(len(buffer), size-read_bytes)]], read_bytes)
                    returned_ns = time.monotonic_ns()
                    if count <= 0:
                        _check_deadline(read_deadline)
                        raise OSError(errno.EIO, f"short cache warm read: {path}")
                    read_bytes += count
                    timing.update(read_bytes=read_bytes, read_end_ns=returned_ns)
                    if observation is not None:
                        observation["read_bytes"] += count
                        observation["read_end_ns"] = returned_ns
                    _check_deadline(read_deadline)
                    if observation is not None and telemetry and time.monotonic() >= next_telemetry:
                        telemetry("read_progress")
                        _check_deadline(read_deadline)
                        next_telemetry = time.monotonic() + 30
            _verify(fd, path, identity)
            _check_deadline(deadline)
            read_counts[fd] = read_bytes
        # All manifest operations finish before any admission residency query.
        if observation is not None:
            if observation["read_end_ns"] is None:
                raise ValueError("observed warm requires nonempty payload")
            observation["read_complete"] = True
            observation["sweeps"] = [
                {"offset_s": offset, "scheduled_ns": observation["read_end_ns"] + offset * 10**9,
                 "query_begin_ns": None, "query_end_ns": None, "lateness_ns": None,
                 "status": "NOT_EXECUTED", "all_hot": None, "files": []}
                for offset in (0, 30, 60, 90, 120)]
            if telemetry:
                telemetry("read_end")
            _check_deadline(read_deadline)
        rounds = observation["sweeps"] if observation is not None else [None]
        for sweep in rounds:
            query_deadline = deadline
            rows = [] if sweep is None else sweep["files"]
            if sweep is not None:
                scheduled = sweep["scheduled_ns"] / 1e9
                while time.monotonic() < scheduled:
                    _check_deadline(deadline)
                    time.sleep(max(0, min(0.25, scheduled - time.monotonic())))
                query_deadline = min(deadline, scheduled + 30)
                if telemetry:
                    telemetry(f"query_{sweep['offset_s']}_begin")
                sweep["attempted_ns"] = time.monotonic_ns()
                if sweep["attempted_ns"] > sweep["scheduled_ns"] + 2 * 10**9:
                    sweep["lateness_ns"] = sweep["attempted_ns"] - sweep["scheduled_ns"]
                    sweep["status"] = "MISSED" if sweep["attempted_ns"] >= sweep["scheduled_ns"] + 30 * 10**9 else "LATE"
                    raise TimeoutError("fixed cache query start missed its two-second tolerance")
            for fd, path, identity, trust in opened:
                _check_deadline(query_deadline)
                _verify(fd, path, identity)
                if _trust(os.fstat(fd)) != trust:
                    raise PermissionError("mincore trust changed")
                query_begin_ns = time.monotonic_ns()
                if sweep is not None and sweep["query_begin_ns"] is None:
                    sweep["query_begin_ns"] = query_begin_ns
                    sweep["lateness_ns"] = query_begin_ns - sweep["scheduled_ns"]
                    if sweep["lateness_ns"] > 2 * 10**9:
                        sweep["status"] = "MISSED" if query_begin_ns >= sweep["scheduled_ns"] + 30 * 10**9 else "LATE"
                        raise TimeoutError("fixed cache query start missed its two-second tolerance")
                    sweep["status"] = "QUERYING"
                row = {"path": str(path), "identity": identity,
                       "query_begin_ns": query_begin_ns, "query_end_ns": None,
                       "pages": None, "resident_pages": None,
                       "read_bytes": read_counts[fd], "mincore_trust": trust,
                       "read_begin_ns": read_times[fd]["read_begin_ns"],
                       "read_end_ns": read_times[fd]["read_end_ns"]}
                rows.append(row)
                pages, resident = _residency(fd, identity["size"], query_deadline)
                row.update(query_end_ns=time.monotonic_ns(), pages=pages, resident_pages=resident)
                if _trust(os.fstat(fd)) != trust:
                    raise PermissionError("mincore trust changed")
                _verify(fd, path, identity)
                _check_deadline(query_deadline)
            for fd, path, identity, _ in opened:
                _verify(fd, path, identity)
            _check_deadline(query_deadline)
            if sweep is not None:
                sweep["query_end_ns"] = time.monotonic_ns()
                if sweep["query_end_ns"] >= min(deadline * 1e9, sweep["scheduled_ns"] + 30 * 10**9):
                    raise TimeoutError("fixed cache query completion deadline expired")
                sweep["all_hot"] = (sum(row["pages"] for row in rows) > 0 and
                                    all(row["pages"] == row["resident_pages"] for row in rows))
                sweep["status"] = "OBSERVED"
                observation["admitted"] = observation["admitted"] and sweep["all_hot"]
                if telemetry:
                    telemetry(f"query_{sweep['offset_s']}_end")
                _check_deadline(deadline)
        if observation is not None:
            observation["observation_complete"] = True
            observation["technical_status"] = "COMPLETED"
            return observation

    pages = sum(row["pages"] for row in rows)
    resident = sum(row["resident_pages"] for row in rows)
    cold, hot = resident == 0, pages > 0 and resident == pages
    return {"operation": action, "files": rows, "pages": pages,
            "resident_pages": resident, "all_cold": cold, "all_hot": hot,
            "read_bytes": sum(row["read_bytes"] for row in rows),
            "read_begin_ns": min((row["read_begin_ns"] for row in rows if row["read_begin_ns"] is not None), default=None),
            "read_end_ns": max((row["read_end_ns"] for row in rows if row["read_end_ns"] is not None), default=None),
            "query_begin_ns": min(row["query_begin_ns"] for row in rows),
            "query_end_ns": max(row["query_end_ns"] for row in rows),
            "admitted": cold if action == "evict" else hot if action == "warm" else None,
            "scope": "local Linux client page-cache snapshot; NFS server and physical disk uncontrolled",
            "deadline_semantics": "cooperative checks; external guard required for hard syscall/runtime bound"}


def query_cache_state(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "query")


def evict_file_cache(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "evict")


def warm_file_cache(paths, allowed_roots, deadline):
    return _run(paths, allowed_roots, deadline, "warm")


def warm_file_cache_observed(paths, allowed_roots, deadline, *, telemetry=None):
    """One full read, then five fixed client-cache snapshots; never rewarm.

    Technical errors (including late/missed queries) raise with partial evidence
    in ``error.cache_observation``. Partial residency is sticky, not an exception.
    Callback and syscall limits are cooperative; an external guard is required.
    """
    result = {"schema": "file-cache-warm-observation-v1", "pid": os.getpid(),
              "clock_identity": None, "read_phase_begin_ns": None,
              "read_begin_ns": None, "read_end_ns": None, "read_bytes": 0,
              "read_complete": False, "read_files": [], "sweeps": [], "admitted": True,
              "observation_complete": False, "technical_status": "INCOMPLETE",
              "read_cap_s": 1800, "query_cap_s": 30, "start_lateness_tolerance_s": 2,
              "scope": "local Linux client page cache; not continuous residency or NFS server cache",
              "deadline_semantics": "cooperative; external guard required"}
    try:
        _check_deadline(deadline)
        if telemetry is not None and not callable(telemetry):
            raise TypeError("telemetry must be callable")
        proc = Path("/proc/self")
        result["clock_identity"] = {
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
            "container_hostname": os.uname().nodename,
            "process_startticks": int((proc / "stat").read_text().rsplit(")", 1)[1].split()[19]),
            "time_namespace": os.readlink(proc / "ns/time"),
            "monotonic_clock_implementation": time.get_clock_info("monotonic").implementation}
        return _run(paths, allowed_roots, deadline, "warm", observation=result, telemetry=telemetry)
    except BaseException as error:
        result["admitted"] = False
        result["technical_status"] = "ERROR"
        result["error"] = {"type": type(error).__name__, "message": str(error)}
        error.cache_observation = result
        raise
