"""Compile the actual source tail with CPU CUDA stubs; never link CUDA."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).parent
BASE = ROOT / 'fixtures/native_model_base.cpp'
PATCH = ROOT / 'native_patch/model.cpp'


def source_tail(path):
    source = path.read_text(encoding='utf-8')
    marker = source.index('  lock.lock();\n  futures.clear();', source.index('int Model::ToGpu('))
    start = source.index('  if (error) {', marker)
    end = source.index('\nint Model::WaitInHost()', start)
    return source, source[start:end]


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


class IpcClosePatchTests(unittest.TestCase):
    def test_source_hashes_and_change_is_limited_to_close_tail(self):
        ledger = json.loads((ROOT / 'native_patch/ledger.json').read_bytes())
        self.assertEqual(hashlib.sha256(BASE.read_bytes()).hexdigest(), ledger['base_sha256'])
        self.assertEqual(hashlib.sha256(PATCH.read_bytes()).hexdigest(), ledger['candidate_sha256'])
        old, old_tail = source_tail(BASE)
        new, new_tail = source_tail(PATCH)
        self.assertEqual(old.replace(old_tail, '<TAIL>'), new.replace(new_tail, '<TAIL>'))
        self.assertFalse(ledger['compiled_native_store'])
        self.assertFalse(ledger['gpu_verified'])

    def test_actual_source_fragment_fails_before_patch_and_passes_fault_controls_after(self):
        compiler = shutil.which('g++')
        if compiler is None:
            self.skipTest('CPU C++ compiler unavailable; native patch not behavior-tested here')
        results = {}
        with tempfile.TemporaryDirectory() as temp:
            for label, path in (('baseline', BASE), ('candidate', PATCH)):
                source = Path(temp) / (label + '.cpp')
                binary = Path(temp) / (label + '.exe')
                _, tail = source_tail(path)
                source.write_text('constexpr bool CHECK_PATCH_ORDER=' + ('true' if label == 'candidate' else 'false')
                    + ';\n' + PREFIX + tail + MAIN, encoding='utf-8')
                compiled = subprocess.run([compiler, '-std=c++17', '-O0', str(source), '-o', str(binary)],
                    capture_output=True, text=True, timeout=60)
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
                results[label] = dict(exit=run.returncode, cases=json.loads(run.stdout))
        self.assertEqual(results['baseline']['exit'], 1)
        self.assertTrue(results['baseline']['cases']['success'])
        self.assertFalse(results['baseline']['cases']['close_failure'])
        self.assertFalse(results['baseline']['cases']['device_failure'])
        self.assertEqual(results['candidate']['exit'], 0)
        self.assertTrue(all(results['candidate']['cases'].values()))


if __name__ == '__main__':
    unittest.main()
