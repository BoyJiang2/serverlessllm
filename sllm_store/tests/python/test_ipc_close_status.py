"""CPU control of the actual IPC-close tail; no CUDA linked.

The C++ fixture is reused from the existing Kimi test_ipc_close_patch.py.
This verifies status/notification behavior, not actual IPC closure or cleanup.
"""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

PREFIX = r'''
#include <cstdint>
#include <iostream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>
enum class MemoryState { LOADING, LOADED, INTERRUPTED };
std::vector<std::string> events;
std::vector<int> closed;
int fail_device=-1, fail_close=-1;
using cudaError_t=int;
constexpr int cudaSuccess=0;
int cudaSetDevice(int d) { events.push_back("device"); return d==fail_device ? 7 : 0; }
int cudaIpcCloseMemHandle(void* p) {
  int id=static_cast<int>(reinterpret_cast<uintptr_t>(p));
  closed.push_back(id); events.push_back("close"); return id==fail_close ? 9 : 0;
}
const char* cudaGetErrorString(int) { return "CPU fixture error"; }
std::ostringstream log_stream;
#define LOG(level) log_stream
struct CV {
  int count=0;
  void notify_all() { ++count; events.push_back("notify"); }
};
struct Replica {
  MemoryState state_=MemoryState::LOADING;
  std::map<int,std::vector<void*>> device_ptrs_;
  CV cv_;
};
int complete(bool error, std::shared_ptr<Replica> gpu_replica) {
  std::string model_path_="fixture", replica_uuid="fixture";
'''

MAIN = r'''
int main() {
  struct Case { const char* name; bool copy_error; int device_error; int close_error; };
  std::vector<Case> cases={{"success",false,-1,-1},{"close_failure",false,-1,2},
    {"device_failure",false,0,-1},{"copy_failure",true,-1,-1}};
  std::cout << "{";
  bool all=true;
  for (size_t i=0;i<cases.size();++i) {
    auto c=cases[i]; events.clear(); closed.clear(); fail_device=c.device_error; fail_close=c.close_error;
    auto r=std::make_shared<Replica>();
    r->device_ptrs_={{0,{reinterpret_cast<void*>(1),reinterpret_cast<void*>(2)}},
                     {1,{reinterpret_cast<void*>(3)}}};
    int ret=complete(c.copy_error,r);
    bool failed=c.copy_error || c.device_error>=0 || c.close_error>=0;
    bool ok=ret==(failed ? -1 : 0) && r->state_==(failed ? MemoryState::INTERRUPTED : MemoryState::LOADED)
      && r->cv_.count==1;
    if (CHECK_PATCH_ORDER) ok=ok && !events.empty() && events.back()=="notify"
      && closed.size()==(c.device_error==0 ? 1U : 3U);
    all=all && ok;
    if(i) std::cout << ",";
    std::cout << "\"" << c.name << "\":" << (ok ? "true" : "false");
  }
  std::cout << "}" << std::endl;
  return all ? 0 : 1;
}
'''

class IpcCloseStatusTest(unittest.TestCase):
    def test_close_status_and_notification_order(self):
        compiler = shutil.which("g++")
        self.assertIsNotNone(compiler, "Existing g++ is required for CPU control")
        path = (Path(__file__).resolve().parents[2]
                / "csrc/sllm_store/model.cpp")
        source = path.read_text()
        marker = source.index("  lock.lock();\n  futures.clear();",
                              source.index("int Model::ToGpu("))
        start = source.index("  if (error) {", marker)
        end = source.index("\nbool Model::IsHostLoaded()", start)
        with tempfile.TemporaryDirectory(prefix="sllm-ipc-close-") as folder:
            root = Path(folder)
            fixture = root / "test.cpp"
            binary = root / "test"
            fixture.write_text("constexpr bool CHECK_PATCH_ORDER=true;\n"
                               + PREFIX + source[start:end] + MAIN)
            built = subprocess.run(
                [compiler, "-std=c++17", "-O0", str(fixture), "-o", str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True,
                                    text=True, timeout=5)
            cases = json.loads(result.stdout)
            self.assertEqual(result.returncode, 0, str(cases) + result.stderr)
            self.assertEqual(set(cases), {
                "success", "close_failure", "device_failure", "copy_failure"})
            self.assertTrue(all(cases.values()), str(cases))


if __name__ == "__main__":
    unittest.main()
