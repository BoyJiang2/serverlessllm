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
#include <string>
#include <unordered_set>
#include <vector>
#include <cuda_runtime.h>
#include <glog/logging.h>
#include HEADER
struct Event { std::string kind; void* pointer; std::string message; };
std::vector<Event> events;
std::unordered_set<void*> registered, unregistered, freed;
bool unregister_error = false;
void capture_log(const std::string& text) {
  events.push_back({"log", nullptr, text});
}
cudaError_t cudaHostRegister(void* pointer, size_t bytes, unsigned flags) {
  assert(pointer && bytes == 4096 && flags == cudaHostRegisterDefault);
  assert(registered.insert(pointer).second);
  events.push_back({"register", pointer, ""});
  return cudaSuccess;
}
cudaError_t cudaHostUnregister(void* pointer) {
  assert(registered.count(pointer) && unregistered.insert(pointer).second);
  events.push_back({"unregister", pointer, ""});
  return unregister_error ? 1 : cudaSuccess;
}
const char* cudaGetErrorString(cudaError_t) { return "CPU stub error"; }
void checked_free(void* pointer) {
  assert(registered.count(pointer) && unregistered.count(pointer));
  assert(freed.insert(pointer).second);
  assert(events.back().kind == "unregister" && events.back().pointer == pointer);
  events.push_back({"free", pointer, ""});
  std::free(pointer);
}
#define free checked_free
#include SOURCE
#undef free
int main(int argc, char** argv) {
  assert(argc == 3);
  size_t count = std::stoul(argv[1]);
  unregister_error = std::string(argv[2]) == "error";
  { PinnedMemoryPool pool(count * 4096, 4096); }
  assert(registered.size() == count && unregistered.size() == count);
  assert(freed.size() == count);
  size_t position = 0;
  auto log = [&](const std::string& expected) {
    assert(position < events.size());
    const auto& event = events[position++];
    assert(event.kind == "log" && event.message == expected);
    std::cout << "LOG\t" << event.message << "\n";
  };
  log("Creating PinnedMemoryPool with " + std::to_string(count)
      + " buffers of 4096 bytes");
  for (size_t index = 0; index < count; ++index) {
    assert(events[position++].kind == "register");
  }
  log("PinnedMemoryPool shutdown begin buffers=" + std::to_string(count)
      + " chunk_size=4096");
  for (size_t index = 0; index < count; ++index) {
    const auto& unregister = events[position++];
    const auto& release = events[position++];
    assert(unregister.kind == "unregister" && release.kind == "free");
    assert(unregister.pointer == release.pointer);
    if ((index + 1) % 1024 == 0) {
      log("PinnedMemoryPool shutdown progress completed="
          + std::to_string(index + 1) + " total=" + std::to_string(count));
    }
  }
  log("PinnedMemoryPool shutdown complete buffers=" + std::to_string(count));
  assert(position == events.size());
  std::cout << "COUNTS\t" << count << "\t" << registered.size() << "\t"
            << unregistered.size() << "\t" << freed.size() << "\n";
}
""".replace("#include HEADER", "#include " + header).replace(
            "#include SOURCE", "#include " + source
        )
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

    def run_case(self, count, error=False):
        result = subprocess.run(
            [str(self.executable), str(count), "error" if error else "ok"],
            check=True, capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        logs = [line.removeprefix("LOG\t") for line in lines if line.startswith("LOG\t")]
        expected = [
            f"Creating PinnedMemoryPool with {count} buffers of 4096 bytes",
            f"PinnedMemoryPool shutdown begin buffers={count} chunk_size=4096",
        ]
        expected.extend(
            f"PinnedMemoryPool shutdown progress completed={completed} total={count}"
            for completed in range(1024, count + 1, 1024)
        )
        expected.append(f"PinnedMemoryPool shutdown complete buffers={count}")
        self.assertEqual(logs, expected)
        self.assertEqual(lines[-1], "COUNTS\t" + "\t".join([str(count)] * 4))
        self.assertEqual(hashlib.sha256(SOURCE.read_bytes()).hexdigest(), self.source_sha256)

    def test_empty_pool_begin_and_complete(self):
        self.run_case(0)

    def test_one_buffer_unregister_then_free_exactly_once(self):
        self.run_case(1)

    def test_1025_buffers_progress_at_1024(self):
        self.run_case(1025)

    def test_unregister_error_keeps_original_free_behavior(self):
        self.run_case(1, error=True)


if __name__ == "__main__":
    unittest.main()
