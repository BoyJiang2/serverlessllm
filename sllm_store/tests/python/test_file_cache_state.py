"""Real Linux cache operations on tiny owned files only; no model or GPU work."""
import ctypes
import errno
import fcntl
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location(
    "cache_state", Path(__file__).resolve().parents[1] / "file_cache_state.py")
cache = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cache)


class FileCacheStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sllm-cache-cpu-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.page = os.sysconf("SC_PAGE_SIZE")
        self.path = self.root / "owned.bin"
        self.path.write_bytes(b"x" * (self.page + 7))

    def invoke(self, function=cache.query_cache_state, paths=None, deadline=None):
        return function(paths or [self.path], [self.root],
                        time.monotonic() + 5 if deadline is None else deadline)

    def test_real_readonly_partial_page_warm_and_residency(self):
        self.path.chmod(0o400)
        result = self.invoke(cache.warm_file_cache)
        self.assertTrue(result["admitted"])
        self.assertEqual(result["pages"], 2)
        self.assertEqual(result["resident_pages"], 2)
        self.assertEqual(result["files"][0]["read_bytes"], self.page + 7)
        query = self.invoke()
        self.assertEqual(query["resident_pages"], 2)
        self.assertIsNone(query["admitted"])
        self.assertNotIn("content", result["files"][0])

    def test_zero_file_has_no_mapping_and_not_fake_hot(self):
        self.path.write_bytes(b"")
        with mock.patch.object(cache._LIBC, "mmap", side_effect=AssertionError("must not map empty")):
            row = self.invoke(cache.warm_file_cache)
        self.assertEqual((row["pages"], row["resident_pages"]), (0, 0))
        self.assertTrue(row["all_cold"])
        self.assertFalse(row["all_hot"])
        self.assertFalse(row["admitted"])

    def test_real_mincore_uses_bounded_nonpopulating_readonly_maps(self):
        actual = cache._LIBC.mmap
        calls = []
        def mapping(address, length, protection, flags, fd, offset):
            calls.append((length, protection, flags, offset))
            return actual(address, length, protection, flags, fd, offset)
        with mock.patch.object(cache, "WINDOW_BYTES", self.page), mock.patch.object(cache._LIBC, "mmap", side_effect=mapping):
            row = self.invoke()
        self.assertEqual(row["pages"], 2)
        self.assertEqual([x[0] for x in calls], [self.page, 7])
        self.assertTrue(all(x[1] == cache.mmap.PROT_READ and x[2] == cache.mmap.MAP_SHARED for x in calls))
        self.assertEqual([x[3] for x in calls], [0, self.page])

    def test_fadvise_only_readonly_target_and_query_controls_admission(self):
        actual = os.posix_fadvise
        def advice(fd, offset, size, operation):
            self.assertEqual(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE, os.O_RDONLY)
            self.assertEqual((offset, size, operation), (0, self.page * 2, os.POSIX_FADV_DONTNEED))
            return actual(fd, offset, size, operation)
        with mock.patch.object(cache.os, "posix_fadvise", side_effect=advice):
            row = self.invoke(cache.evict_file_cache)
        self.assertEqual(row["admitted"], row["resident_pages"] == 0)
        with mock.patch.object(cache, "_residency", return_value=(2, 2)):
            self.assertFalse(self.invoke(cache.evict_file_cache)["admitted"])
        with mock.patch.object(cache, "_residency", return_value=(2, 1)):
            self.assertFalse(self.invoke(cache.warm_file_cache)["admitted"])

    def test_later_warm_read_eviction_is_seen_by_final_whole_manifest_query(self):
        second = self.root / "second.bin";second.write_bytes(b"y" * (self.page + 7))
        first_inode, second_inode = self.path.stat().st_ino, second.stat().st_ino
        resident = {first_inode: True, second_inode: True}
        mapping, events = {}, []
        real_read, real_map = os.preadv, cache._LIBC.mmap
        def read(fd, buffers, offset):
            inode = os.fstat(fd).st_ino
            events.append(("read", inode))
            if inode == second_inode:resident[first_inode] = False
            return real_read(fd, buffers, offset)
        def mapped(address, length, protection, flags, fd, offset):
            result = real_map(address, length, protection, flags, fd, offset)
            mapping[result] = os.fstat(fd).st_ino
            return result
        def query(address, length, vector):
            inode = mapping[address];events.append(("query", inode))
            for index in range((length + self.page - 1) // self.page):
                vector[index] = int(resident[inode])
            return 0
        with mock.patch.object(cache.os, "preadv", side_effect=read), mock.patch.object(cache._LIBC, "mmap", side_effect=mapped), mock.patch.object(cache._LIBC, "mincore", side_effect=query):
            result = self.invoke(cache.warm_file_cache, paths=[self.path, second])
        self.assertEqual([kind for kind, _ in events], ["read", "read", "query", "query"])
        self.assertEqual([row["resident_pages"] for row in result["files"]], [0, 2])
        self.assertEqual([row["read_bytes"] for row in result["files"]], [self.page + 7] * 2)
        self.assertFalse(result["all_hot"])
        self.assertFalse(result["admitted"])

    def test_malformed_missing_outside_symlink_directory_and_duplicate_inode(self):
        link = self.root / "hardlink";os.link(self.path, link)
        symlink = self.root / "symlink";symlink.symlink_to(self.path)
        for paths in [[Path("relative")], [self.root / "missing"], [self.root], [symlink], [self.path, link]]:
            with self.subTest(paths=paths), self.assertRaises((ValueError, OSError)):
                self.invoke(paths=paths)
        with tempfile.TemporaryDirectory() as outside:
            file = Path(outside) / "other";file.write_bytes(b"a")
            with self.assertRaisesRegex(ValueError, "outside"):
                self.invoke(paths=[file])
            symlink.unlink();symlink.symlink_to(file)
            with self.assertRaisesRegex(ValueError, "nonsymlink"):
                self.invoke(paths=[symlink])
        with self.assertRaises(ValueError):
            cache.query_cache_state([], [self.root], time.monotonic() + 5)
        with self.assertRaises(ValueError):
            cache.query_cache_state([self.path], ["relative"], time.monotonic() + 5)

    def test_fifo_and_open_identity_redirection_rejected_before_cache_operation(self):
        fifo = self.root / "fifo";os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.invoke(paths=[fifo])
        actual = os.open
        with tempfile.TemporaryDirectory() as outside:
            other = Path(outside) / "foreign";other.write_bytes(b"a")
            def redirect(path, flags):
                return actual(other, flags)
            with mock.patch.object(cache.os, "open", side_effect=redirect), mock.patch.object(cache.os, "posix_fadvise") as advice:
                with self.assertRaisesRegex(RuntimeError, "identity changed"):
                    self.invoke(cache.evict_file_cache)
                advice.assert_not_called()

    def test_unreadable_and_untrusted_mincore_are_errors(self):
        with mock.patch.object(cache.os, "open", side_effect=PermissionError("unreadable")):
            with self.assertRaises(PermissionError):self.invoke()
        with mock.patch.object(cache.os, "geteuid", return_value=self.path.stat().st_uid + 1):
            with self.assertRaisesRegex(PermissionError, "trust"):
                self.invoke()
        actual = Path.read_text
        def read(path, *args, **kwargs):
            return "0 100000 65536\n" if str(path) == "/proc/self/uid_map" else actual(path, *args, **kwargs)
        with mock.patch.object(Path, "read_text", read):
            with self.assertRaisesRegex(PermissionError, "UID mapping"):
                self.invoke()

    def test_root_nonowner_requires_actual_fowner_capability_proof(self):
        actual = Path.read_text
        owner = self.path.stat()
        info = type("Info", (), {"st_uid": owner.st_uid + 1})()
        caps = [8]
        def read(path, *args, **kwargs):
            if str(path) == "/proc/self/status":return f"Uid:\t0\t0\t0\t0\nCapEff:\t{caps[0]:016x}\n"
            return actual(path, *args, **kwargs)
        with mock.patch.object(cache.os, "geteuid", return_value=0), mock.patch.object(Path, "read_text", read):
            proof = cache._trust(info)
            self.assertTrue(proof["cap_fowner"])
            caps[0] = 0
            with self.assertRaises(PermissionError):cache._trust(info)

    def test_identity_race_after_operation_rejected(self):
        actual = cache._residency
        def mutate(fd, size, deadline):
            result = actual(fd, size, deadline)
            info = self.path.stat()
            os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            return result
        with mock.patch.object(cache, "_residency", side_effect=mutate):
            with self.assertRaisesRegex(RuntimeError, "identity changed"):
                self.invoke()

    def test_expired_deadline_and_post_syscall_overrun_never_admit(self):
        with self.assertRaises(TimeoutError):self.invoke(deadline=time.monotonic()-1)
        with self.assertRaises(TimeoutError):self.invoke(deadline=float("inf"))
        clock = [0.0]
        def overrun(fd, buffers, offset):
            clock[0] = 2.0
            return len(buffers[0])
        with mock.patch.object(cache.time, "monotonic", side_effect=lambda: clock[0]), mock.patch.object(cache.os, "preadv", side_effect=overrun):
            with self.assertRaises(TimeoutError):self.invoke(cache.warm_file_cache, deadline=1.0)

    def test_real_errors_short_read_and_mincore_failure_propagate(self):
        with mock.patch.object(cache.os, "preadv", return_value=0):
            with self.assertRaises(OSError):self.invoke(cache.warm_file_cache)
        with mock.patch.object(cache.os, "posix_fadvise", side_effect=OSError(errno.EIO, "advice failed")):
            with self.assertRaises(OSError):self.invoke(cache.evict_file_cache)
        def fail(*args):
            ctypes.set_errno(errno.EACCES)
            return -1
        with mock.patch.object(cache._LIBC, "mincore", side_effect=fail):
            with self.assertRaises(OSError) as caught:self.invoke()
        self.assertEqual(caught.exception.errno, errno.EACCES)
        unmap = cache._LIBC.munmap
        def failed_cleanup(address, length):
            unmap(address, length)
            ctypes.set_errno(errno.EIO)
            return -1
        with mock.patch.object(cache._LIBC, "mincore", side_effect=fail), mock.patch.object(cache._LIBC, "munmap", side_effect=failed_cleanup):
            with self.assertRaises(OSError) as original:self.invoke()
        self.assertEqual(original.exception.errno, errno.EACCES)


class ObservedCacheTest(unittest.TestCase):
    setUp = FileCacheStateTest.setUp
    # Reuse the same tiny real owned files; mocked time avoids a two-minute test.
    def observed(self, residency=None, telemetry=None, read=None, deadline=2000):
        clock = [100.0]
        self.clock = clock
        def sleep(seconds):
            clock[0] += max(0, seconds)
        with mock.patch.object(cache.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(cache.time, "monotonic_ns", side_effect=lambda: round(clock[0] * 1e9)), \
             mock.patch.object(cache.time, "sleep", side_effect=sleep), \
             mock.patch.object(cache, "_residency", side_effect=residency or cache._residency):
            if read is None:
                return cache.warm_file_cache_observed([self.path], [self.root], deadline, telemetry=telemetry)
            with mock.patch.object(cache.os, "preadv", side_effect=read):
                return cache.warm_file_cache_observed([self.path], [self.root], deadline, telemetry=telemetry)

    def test_observed_five_fixed_sweeps_real_tiny_read_and_mincore(self):
        events = []
        result = self.observed(telemetry=events.append)
        self.assertTrue(result["admitted"])
        self.assertTrue(result["observation_complete"])
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["technical_status"], "COMPLETED")
        self.assertEqual(result["read_bytes"], self.path.stat().st_size)
        self.assertEqual([s["offset_s"] for s in result["sweeps"]], [0, 30, 60, 90, 120])
        for sweep in result["sweeps"]:
            self.assertEqual(sweep["scheduled_ns"], result["read_end_ns"] + sweep["offset_s"] * 10**9)
            self.assertGreaterEqual(sweep["query_begin_ns"], sweep["scheduled_ns"])
            self.assertLessEqual(sweep["query_end_ns"], sweep["scheduled_ns"] + 30 * 10**9)
            self.assertEqual(sweep["status"], "OBSERVED")
            row = sweep["files"][0]
            self.assertLessEqual(row["read_end_ns"], row["query_begin_ns"])
            self.assertLessEqual(row["query_begin_ns"], row["query_end_ns"])
        self.assertEqual(events[:3], ["baseline", "read_begin", "read_end"])
        self.assertEqual(result["clock_identity"]["process_startticks"], int(Path("/proc/self/stat").read_text().rsplit(")",1)[1].split()[19]))

    def test_partial_first_snapshot_is_sticky_without_rewarm(self):
        calls = []
        actual = os.preadv
        def read(*args):
            calls.append(args[2]);return actual(*args)
        values = iter([(2, 1), (2, 2), (2, 2), (2, 2), (2, 2)])
        result = self.observed(residency=lambda *args: next(values), read=read)
        self.assertFalse(result["admitted"])
        self.assertTrue(result["observation_complete"])
        self.assertEqual(len(calls), 1)
        self.assertEqual([s["all_hot"] for s in result["sweeps"]], [False, True, True, True, True])

    def test_read_end_is_last_read_return_before_telemetry_not_reanchored(self):
        def telemetry(label):
            if label == "read_end":self.clock[0] += 1
        result = self.observed(telemetry=telemetry)
        self.assertEqual(result["read_end_ns"], 100 * 10**9)
        self.assertEqual(result["sweeps"][0]["query_begin_ns"], 101 * 10**9)
        self.assertEqual(result["sweeps"][1]["query_begin_ns"], 130 * 10**9)

    def test_late_and_missed_queries_abort_with_unexecuted_slots(self):
        for delay, status in [(2.001, "LATE"), (31, "MISSED")]:
            def telemetry(label):
                if label == "read_end":self.clock[0] += delay
            with self.subTest(delay=delay), self.assertRaises(TimeoutError) as caught:
                self.observed(telemetry=telemetry)
            result = caught.exception.cache_observation
            self.assertFalse(result["admitted"])
            self.assertEqual(result["sweeps"][0]["status"], status)
            self.assertEqual([s["status"] for s in result["sweeps"]][1:], ["NOT_EXECUTED"] * 4)
            self.assertIsNone(result["sweeps"][0]["query_begin_ns"])

    def test_round_deadline_is_fixed_not_start_plus_thirty(self):
        deadlines = []
        def telemetry(label):
            if label.endswith("_begin") and label.startswith("query_"):self.clock[0] += 1
        def residency(fd, size, deadline):
            deadlines.append(deadline)
            return (2, 2)
        result = self.observed(residency=residency, telemetry=telemetry)
        self.assertEqual(deadlines, [130, 160, 190, 220, 250])
        self.assertTrue(result["admitted"])

    def test_query_overrun_and_telemetry_failure_propagate_original(self):
        primary = RuntimeError("telemetry failed")
        def telemetry(label):
            if label == "query_30_end":raise primary
        with self.assertRaises(RuntimeError) as caught:self.observed(telemetry=telemetry)
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.cache_observation["technical_status"], "ERROR")
        def overrun(fd, size, deadline):
            self.clock[0] = deadline + 0.001
            return (2, 2)
        with self.assertRaises(TimeoutError) as caught:self.observed(residency=overrun)
        self.assertFalse(caught.exception.cache_observation["admitted"])
        self.assertFalse(caught.exception.cache_observation["observation_complete"])

    def test_full_read_cap_and_partial_read_evidence(self):
        actual = os.preadv
        def slow_read(*args):
            result = actual(*args);self.clock[0] += 1801;return result
        with self.assertRaises(TimeoutError) as caught:self.observed(read=slow_read, deadline=5000)
        result = caught.exception.cache_observation
        self.assertEqual(result["read_bytes"], self.path.stat().st_size)
        self.assertEqual(result["read_end_ns"], 1901 * 10**9)
        self.assertFalse(result["read_complete"])
        self.assertFalse(result["admitted"])
        self.assertEqual(result["sweeps"], [])

    def test_read_progress_callback_is_time_limited_not_per_chunk(self):
        self.path.write_bytes(b"x" * 17)
        events = []
        actual = os.preadv
        def read(*args):
            result = actual(*args);self.clock[0] += 10;return result
        with mock.patch.object(cache, "WINDOW_BYTES", 1):
            result = self.observed(residency=lambda *args: (1, 1), telemetry=events.append, read=read)
        self.assertLessEqual(events.count("read_progress"), 6)
        self.assertEqual(result["read_bytes"], 17)

    def test_read_cap_includes_baseline_and_progress_callback_errors_are_not_swallowed(self):
        def slow_baseline(label):
            if label == "baseline":self.clock[0] += 1801
        with self.assertRaises(TimeoutError) as caught:
            self.observed(telemetry=slow_baseline, deadline=5000)
        self.assertIsNone(caught.exception.cache_observation["read_begin_ns"])
        self.assertEqual(caught.exception.cache_observation["read_bytes"], 0)
        primary = OSError("metadata read failed")
        def failed(label):
            if label == "read_end":raise primary
        with self.assertRaises(OSError) as caught:self.observed(telemetry=failed)
        self.assertIs(caught.exception, primary)
        self.assertEqual(len(primary.cache_observation["sweeps"]), 5)

    def test_observed_identity_drift_and_global_deadline_abort(self):
        def mutate(label):
            if label == "query_30_begin":
                info = self.path.stat()
                os.utime(self.path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
        with self.assertRaisesRegex(RuntimeError, "identity changed") as caught:
            self.observed(telemetry=mutate)
        self.assertEqual(caught.exception.cache_observation["sweeps"][0]["status"], "OBSERVED")
        with self.assertRaises(TimeoutError) as caught:self.observed(deadline=145)
        self.assertFalse(caught.exception.cache_observation["observation_complete"])
        self.assertFalse(caught.exception.cache_observation["admitted"])

    def test_start_tolerance_checks_actual_query_after_identity_verification(self):
        verify = cache._verify
        def slow_verify(*args):
            result = verify(*args)
            if hasattr(self, "clock") and getattr(self, "reading_done", False):self.clock[0] += 3
            return result
        def telemetry(label):
            if label == "read_end":self.reading_done = True
        with mock.patch.object(cache, "_verify", side_effect=slow_verify):
            with self.assertRaises(TimeoutError) as caught:self.observed(telemetry=telemetry)
        self.assertEqual(caught.exception.cache_observation["sweeps"][0]["status"], "LATE")

    def test_observed_all_files_read_before_each_whole_manifest_sweep(self):
        second = self.root / "second.bin";second.write_bytes(b"y")
        original = cache._run
        def both(paths, roots, deadline, action, **kwargs):
            return original([self.path, second], roots, deadline, action, **kwargs)
        read = os.preadv
        reads, queries = [], []
        def observed_read(fd, buffers, offset):
            reads.append(os.fstat(fd).st_ino)
            return read(fd, buffers, offset)
        def residency(fd, size, deadline):
            self.assertEqual(len(reads), 2)
            queries.append(os.fstat(fd).st_ino)
            pages = (size + self.page - 1) // self.page
            return pages, pages
        with mock.patch.object(cache, "_run", side_effect=both):
            result = self.observed(residency=residency, read=observed_read)
        self.assertEqual(len(reads), 2)
        self.assertEqual(len(queries), 10)
        self.assertEqual(result["read_bytes"], self.path.stat().st_size + 1)
        self.assertTrue(all(len(sweep["files"]) == 2 for sweep in result["sweeps"]))
        self.assertEqual(len(result["read_files"]), 2)

    def test_query_end_clock_crossing_after_last_deadline_check_is_rejected(self):
        clock = [100.0]
        armed = [False]
        ns_calls = [0]
        def monotonic_ns():
            if armed[0]:
                ns_calls[0] += 1
                # First stamp is the per-file end; second is the whole-sweep end.
                if ns_calls[0] == 2:return 130 * 10**9
            return round(clock[0] * 1e9)
        def residency(*args):
            armed[0] = True
            return (2, 2)
        with mock.patch.object(cache.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(cache.time, "monotonic_ns", side_effect=monotonic_ns), \
             mock.patch.object(cache.time, "sleep", side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay)), \
             mock.patch.object(cache, "_residency", side_effect=residency):
            with self.assertRaisesRegex(TimeoutError, "completion") as caught:
                cache.warm_file_cache_observed([self.path], [self.root], 2000)
        result = caught.exception.cache_observation
        self.assertFalse(result["admitted"])
        self.assertFalse(result["observation_complete"])
        self.assertEqual(result["sweeps"][0]["query_end_ns"], 130 * 10**9)
        self.assertEqual(result["sweeps"][0]["status"], "QUERYING")
        self.assertTrue(result["read_complete"])

    def test_last_read_skips_heavy_progress_that_would_delay_query_zero(self):
        actual = os.preadv
        events = []
        def read(*args):
            count = actual(*args)
            self.clock[0] += 30
            return count
        def telemetry(label):
            events.append(label)
            if label == "read_progress":self.clock[0] += 5
        result = self.observed(read=read, telemetry=telemetry)
        self.assertNotIn("read_progress", events)
        self.assertEqual(result["read_end_ns"], 130 * 10**9)
        self.assertEqual(result["sweeps"][0]["query_begin_ns"], result["read_end_ns"])
        self.assertTrue(result["admitted"])

    def test_nonfinal_file_read_still_reports_due_progress(self):
        second = self.root / "second.bin";second.write_bytes(b"y")
        run, read = cache._run, os.preadv
        events = []
        def both(paths, roots, deadline, action, **kwargs):
            return run([self.path, second], roots, deadline, action, **kwargs)
        def slow_read(*args):
            count = read(*args);self.clock[0] += 30;return count
        def telemetry(label):
            events.append(label)
            if label == "read_progress":self.clock[0] += 3
        with mock.patch.object(cache, "_run", side_effect=both):
            result = self.observed(read=slow_read, telemetry=telemetry)
        self.assertEqual(events.count("read_progress"), 1)
        self.assertEqual(result["read_end_ns"], 163 * 10**9)
        self.assertTrue(result["admitted"])

    def test_zero_payload_is_not_invented_read_endpoint(self):
        self.path.write_bytes(b"")
        with self.assertRaises(ValueError) as caught:self.observed()
        self.assertIsNone(caught.exception.cache_observation["read_end_ns"])


if __name__ == "__main__":
    unittest.main()
