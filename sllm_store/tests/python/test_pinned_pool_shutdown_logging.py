"""Compile the actual pool CPP with CPU-only CUDA/glog stubs."""
import hashlib
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest


SOURCE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "csrc/sllm_store/pinned_memory_pool.cpp"
)


class PoolShutdownLoggingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = shutil.which("g++")
        if cls.compiler is None:
            raise unittest.SkipTest("host g++ is unavailable")
        cls.temporary = tempfile.TemporaryDirectory(prefix="sllm-pool-logs-")
        cls.addClassCleanup(cls.temporary.cleanup)
        folder = pathlib.Path(cls.temporary.name)
        (folder / "glog").mkdir()
        (folder / "cuda_runtime.h").write_text("""
#pragma once
#include <cstddef>
using cudaError_t = int;
constexpr cudaError_t cudaSuccess = 0;
constexpr unsigned cudaHostRegisterDefault = 0;
cudaError_t cudaHostRegister(void*, size_t, unsigned);
cudaError_t cudaHostUnregister(void*);
const char* cudaGetErrorString(cudaError_t);
""")
        (folder / "glog/logging.h").write_text("""
#pragma once
#include <cstdlib>
#include <sstream>
#include <string>
void capture_log(const std::string&);
struct CpuLog {
  explicit CpuLog(int level) : fatal(level == 2) {}
  ~CpuLog() { capture_log(stream.str()); if (fatal) std::abort(); }
  template <class T> CpuLog& operator<<(const T& value) {
    stream << value; return *this;
  }
  std::ostringstream stream;
  bool fatal;
};
#define INFO 0
#define ERROR 1
#define FATAL 2
#define LOG(level) CpuLog(level)
""")
        header = json.dumps(str(SOURCE.with_suffix(".h")))
        source = json.dumps(str(SOURCE))
        harness = r"""
#include <cassert>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>
#include <cuda_runtime.h>
#include <glog/logging.h>
#include HEADER
std::unordered_set<void*> registered, freed;
std::vector<std::string> logs;
size_t attempts = 0;
void* failing = nullptr;
bool fail_all = false;
void capture_log(const std::string& text) { logs.push_back(text); }
cudaError_t cudaHostRegister(void* pointer, size_t bytes, unsigned flags) {
  assert(pointer && bytes == 4096 && flags == cudaHostRegisterDefault);
  assert(registered.insert(pointer).second);
  return cudaSuccess;
}
cudaError_t cudaHostUnregister(void* pointer) {
  assert(registered.count(pointer) && !freed.count(pointer));
  ++attempts;
  if (fail_all || pointer == failing) return 1;
  registered.erase(pointer);
  return cudaSuccess;
}
const char* cudaGetErrorString(cudaError_t) { return "CPU stub error"; }
void checked_free(void* pointer) {
  assert(!registered.count(pointer));
  assert(freed.insert(pointer).second);
  std::free(pointer);
}
#define free checked_free
#include SOURCE
#undef free
int main(int argc, char** argv) {
  assert(argc == 3);
  size_t count = std::stoul(argv[1]);
  std::string mode = argv[2];
  if (mode == "drain") {
    CheckpointStore store;
    store.async_tasks_.push(std::async(std::launch::deferred, [] { return 7; }));
    store.async_tasks_.push(std::async(std::launch::deferred, []() -> int {
      throw std::runtime_error("injected task error");
    }));
    bool last_ran = false;
    store.async_tasks_.push(std::async(std::launch::deferred, [&] { last_ran = true; return 0; }));
    store.DrainAsyncTasks();
    assert(last_ran && store.async_tasks_.empty());
    bool nonzero = false, exception = false;
    for (const auto& text : logs) {
      if (text == "Async load task returned 7") nonzero = true;
      if (text == "Async load task failed: injected task error") exception = true;
    }
    assert(nonzero && exception);
  }
  {
    PinnedMemoryPool pool(count * 4096, 4096);
    if (mode == "borrowed") {
      std::vector<char*> borrowed;
      assert(pool.Allocate(4096, borrowed) == 0);
      bool rejected = false;
      try { pool.Release(); } catch (const std::runtime_error&) { rejected = true; }
      assert(rejected && attempts == 0 && freed.empty());
      assert(pool.Deallocate(borrowed) == 0);
    }
    if (mode == "error") {
      assert(count == 2);
      failing = *registered.begin();
      bool rejected = false;
      try { pool.Release(); } catch (const std::runtime_error&) { rejected = true; }
      assert(rejected && registered.size() == 1 && registered.count(failing));
      assert(freed.size() == 1 && !freed.count(failing));
      // The failed pointer remains owned and available; the successful one does not.
      std::vector<char*> retained;
      assert(pool.Allocate(4096, retained) == 0 && retained[0] == failing);
      assert(pool.Deallocate(retained) == 0);
      failing = nullptr;
    }
    if (mode == "destructor_error") {
      fail_all = true;
    } else {
      pool.Release();
      size_t previous = attempts;
      pool.Release();
      assert(attempts == previous && registered.empty() && freed.size() == count);
    }
  }
  if (mode == "destructor_error") {
    assert(registered.size() == count && freed.empty());
    bool logged = false;
    for (const auto& text : logs) if (text.find("destructor release failed") != std::string::npos) logged = true;
    assert(logged);
    // Test-only cleanup after proving production destructor retained failed memory.
    for (void* pointer : registered) std::free(pointer);
  } else {
    assert(registered.empty() && freed.size() == count);
  }
  if (count == 1025) {
    bool progress = false;
    for (const auto& text : logs) if (text.find("attempted=1024 failed=0 total=1025") != std::string::npos) progress = true;
    assert(progress);
  }
  std::cout << "POOL_RELEASE_CONTRACT_PASS " << mode << " " << count << "\n";
}
""".replace("#include HEADER", "#include " + header).replace(
            "#include SOURCE", "#include " + source
        )
        store_source = SOURCE.with_name("checkpoint_store.cpp").read_text()
        begin = store_source.index("void CheckpointStore::DrainAsyncTasks() {")
        end = store_source.index("\n}", begin) + 2
        drain = store_source[begin:end]
        harness = harness.replace("int main(int argc, char** argv) {", """
#include <future>
#include <queue>
struct CheckpointStore {
  std::queue<std::future<int>> async_tasks_;
  void DrainAsyncTasks();
};
""" + drain + "\nint main(int argc, char** argv) {")
        cls.source_sha256 = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
        fixture = folder / "pool_shutdown.cpp"
        fixture.write_text(harness)
        cls.executable = folder / "pool_shutdown"
        subprocess.run(
            [
                cls.compiler, "-std=c++17", "-pthread", "-O0",
                "-I", str(folder), str(fixture), "-o", str(cls.executable),
            ],
            check=True, capture_output=True, text=True, timeout=30,
        )

    def run_case(self, count, mode="ok"):
        result = subprocess.run(
            [str(self.executable), str(count), mode],
            check=True, capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.stdout.strip(), f"POOL_RELEASE_CONTRACT_PASS {mode} {count}")
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.source_sha256)

    def test_empty_pool_release_is_idempotent(self):
        self.run_case(0)

    def test_one_buffer_unregister_then_free_exactly_once(self):
        self.run_case(1)

    def test_1025_buffers_progress_at_1024(self):
        self.run_case(1025)

    def test_unregister_error_retains_failed_owner_and_successes_are_not_repeated(self):
        self.run_case(2, "error")

    def test_borrowed_buffer_rejects_release_before_any_unregister(self):
        self.run_case(1, "borrowed")

    def test_destructor_contains_unregister_error_without_freeing_failed_memory(self):
        self.run_case(1, "destructor_error")

    def test_actual_task_drain_observes_results_and_continues_after_exception(self):
        self.run_case(0, "drain")


if __name__ == "__main__":
    unittest.main()
