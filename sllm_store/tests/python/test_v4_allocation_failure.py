"""Compile the real allocation function against CPU CUDA-call stand-ins only."""

from pathlib import Path
import subprocess
import tempfile
import unittest


class V4AllocationFailureTests(unittest.TestCase):
    def test_partial_allocation_rolls_back_and_preserves_successful_map(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "csrc/checkpoint/checkpoint.cpp").read_text()
        begin = source.index("std::unordered_map<int, void*> AllocateCudaMemory(")
        end = source.index("std::unordered_map<int, std::string> GetCudaMemoryHandles(", begin)
        function = source[begin:end]
        stub = r'''
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
using cudaError_t = int;
constexpr int cudaSuccess = 0;
int alloc_calls=0, set_calls=0, fail_alloc=0, fail_set=0;
std::unordered_set<void*> live;
int cudaSetDevice(int) { return ++set_calls == fail_set ? 1 : 0; }
int cudaMalloc(void** ptr, size_t) {
  if (++alloc_calls == fail_alloc) return 1;
  *ptr = reinterpret_cast<void*>(uintptr_t(alloc_calls)); live.insert(*ptr); return 0;
}
int cudaFree(void* ptr) { live.erase(ptr); return 0; }
const char* cudaGetErrorString(int) { return "injected CUDA failure"; }
'''
        main = r'''
int main() {
  for (int failure : {1, 2}) {
    alloc_calls=0; set_calls=0; live.clear();
    fail_alloc = failure == 1 ? 2 : 0;
    fail_set = failure == 2 ? 2 : 0;
    bool threw=false;
    try { auto result=AllocateCudaMemory({{0,8},{1,8}}); }
    catch (const std::runtime_error&) { threw=true; }
    if (!threw || !live.empty()) {
      std::cerr << "partial allocation failure was not rolled back\n"; return 1;
    }
  }
  alloc_calls=0; set_calls=0; fail_alloc=0; fail_set=0; live.clear();
  auto result=AllocateCudaMemory({{0,8},{1,8}});
  if (result.size()!=2 || live.size()!=2) return 2;
  for (auto entry : result) cudaFree(entry.second);
  return live.empty() ? 0 : 3;
}
'''
        with tempfile.TemporaryDirectory() as folder:
            cpp, binary = Path(folder) / "allocation.cpp", Path(folder) / "allocation-test"
            cpp.write_text(stub + function + main)
            built = subprocess.run(["c++", "-std=c++17", str(cpp), "-o", str(binary)],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
