"""CPU lifetime regression for the actual RestoreTensors function.

Compile the extracted function with real Torch from_blob. Only the device and
cudaFree are replaced for this CPU test. This does not test CUDA, IPC or Store.
Run on Linux with existing Torch, compiler and Ninja; installs nothing.
"""
import hashlib
import os
import pathlib
import tempfile
import unittest

from torch.utils.cpp_extension import load_inline


class RestoreOwnershipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = (pathlib.Path(__file__).resolve().parents[2]
                  / "csrc/checkpoint/checkpoint.cpp").read_text()
        start = source.index(
            "std::unordered_map<std::string, torch::Tensor> RestoreTensors(")
        end = source.index(
            "\nstd::unordered_map<std::string, int> GetGpuUUID()", start)
        function = source[start:end]
        device = "torch::Device tensor_device(torch::kCUDA, device);"
        if function.count(device) != 1:
            raise AssertionError("RestoreTensors device contract changed")
        function = function.replace(
            device, "torch::Device tensor_device(torch::kCPU);")
        if function.count("cudaFree(") != 1:
            raise AssertionError("RestoreTensors release contract changed")
        function = function.replace("cudaFree(", "test_cuda_free(")
        code = r"""
#include <torch/extension.h>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>
#include <vector>
static std::unordered_set<void*> freed;
static int frees;
int test_cuda_free(void* pointer) {
  TORCH_CHECK(freed.insert(pointer).second, "duplicate allocation release");
  ++frees;
  std::free(pointer);
  return 0;
}
at::ScalarType stringToScalarType(const std::string& dtype) {
  TORCH_CHECK(dtype == "torch.int32", "unexpected fixture dtype");
  return torch::kInt32;
}
""" + function + r"""
void check_case(int mode) {
  frees = 0;
  freed.clear();
  void* base = std::malloc(8192);
  TORCH_CHECK(base, "fixture allocation failed");
  try {
    using Meta = std::tuple<std::vector<int64_t>,
                            std::vector<int64_t>, std::string>;
    std::unordered_map<std::string, Meta> meta{
        {"first", {{4}, {1}, "torch.int32"}},
        {"alias", {{4}, {1}, "torch.int32"}},
        {"tail", {{4}, {1}, "torch.int32"}}};
    std::unordered_map<std::string, uint64_t> offsets{{"tail", 4096}};
    if (mode == 0 || mode == 1) offsets["first"] = 0;
    if (mode == 1) offsets["alias"] = 0;
    if (mode == 3) offsets.clear();
    std::unordered_map<int, void*> pointers{{0, base}};
    std::unordered_map<int, std::unordered_map<std::string, uint64_t>>
        device_offsets{{0, offsets}};
    if (mode == 4) {
      meta.clear();
      bool threw = false;
      try {
        auto invalid = RestoreTensors(meta, pointers, device_offsets);
      } catch (const std::out_of_range&) {
        threw = true;
      }
      TORCH_CHECK(threw && frees == 1,
                  "metadata error did not release allocation exactly once");
      return;
    }
    auto state = RestoreTensors(meta, pointers, device_offsets);
    if (mode == 3) {
      TORCH_CHECK(state.empty() && frees == 1,
                  "unused allocation was not released");
      return;
    }
    state.at("tail").fill_(777);
    auto survivor = state.at("tail").view({4});
    if (mode == 1) {
      state.at("first").fill_(123);
      TORCH_CHECK(state.at("alias")[0].item<int>() == 123,
                  "offset-zero alias values differ");
    }
    state.erase("first");
    state.erase("alias");
    TORCH_CHECK(frees == 0, "base released while tail storage is live");
    state.clear();
    TORCH_CHECK(frees == 0, "base released while tail view is live");
    TORCH_CHECK(survivor[0].item<int>() == 777, "tail value changed");
    survivor = torch::Tensor();
    TORCH_CHECK(frees == 1, "allocation must be released exactly once");
  } catch (...) {
    if (freed.count(base) == 0) std::free(base);
    throw;
  }
}
"""
        cls.directory = tempfile.TemporaryDirectory(
            prefix="sllm-restore-ownership-")
        previous_jobs = os.environ.get("MAX_JOBS")
        os.environ["MAX_JOBS"] = "1"
        try:
            cls.extension = load_inline(
                name="sllm_ownership_" + hashlib.sha256(
                    code.encode()).hexdigest()[:12],
                cpp_sources=code, functions=["check_case"],
                with_cuda=False, extra_cflags=["-O0", "-g0"],
                build_directory=cls.directory.name, verbose=False)
        except Exception:
            cls.directory.cleanup()
            raise
        finally:
            if previous_jobs is None:
                os.environ.pop("MAX_JOBS", None)
            else:
                os.environ["MAX_JOBS"] = previous_jobs

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def test_lifetimes(self):
        for mode in range(5):
            with self.subTest(mode=mode):
                self.extension.check_case(mode)


if __name__ == "__main__":
    unittest.main()
