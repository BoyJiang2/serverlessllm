"""CPU regression for the official writer; no Torch/CUDA or downloads.

Run: python3 sllm_store/tests/python/test_tensor_writer.py
The partition test copies only the writer sources into a temporary directory
and reduces kPartitionMaxSize and kBufferSize to 8192 bytes. It is a boundary fixture,
not a real 10 GiB partition or model conversion test.
"""
import pathlib
import subprocess
import tempfile
import unittest


class TensorWriterTest(unittest.TestCase):
    def test_tail_and_partition_offsets(self):
        root = pathlib.Path(__file__).resolve().parents[2] / "csrc/checkpoint"
        with tempfile.TemporaryDirectory(prefix="sllm-writer-test-") as directory:
            work = pathlib.Path(directory)
            for name in ("aligned_buffer.cpp", "aligned_buffer.h",
                         "tensor_writer.cpp", "tensor_writer.h"):
                (work / name).write_bytes((root / name).read_bytes())
            header = work / "tensor_writer.h"
            text = header.read_text()
            self.assertIn("10L << 30", text)
            header.write_text(text.replace("10L << 30", "8192"))
            buffer_header = work / "aligned_buffer.h"
            buffer_text = buffer_header.read_text()
            self.assertIn("1L << 30", buffer_text)
            buffer_header.write_text(buffer_text.replace("1L << 30", "8192"))
            (work / "test.cpp").write_text(r"""
#include "tensor_writer.h"
#include <cassert>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <vector>

std::vector<char> read(const std::string& path) {
  std::ifstream file(path, std::ios::binary);
  return std::vector<char>(std::istreambuf_iterator<char>(file), {});
}
int main(int argc, char** argv) {
  std::string path = argv[1];
  for (size_t size : {3, 4096, 4101, 8192, 0}) {
    { std::ofstream old(path); old << std::string(16384, 'x'); }
    std::vector<char> data(size, 'a');
    { AlignedBuffer buffer(path);
      assert(buffer.writeData(data.data(), size) == size); }
    auto actual = read(path);
    size_t padded = (size + 4095) / 4096 * 4096;
    assert(actual.size() == padded);
    assert(std::equal(data.begin(), data.end(), actual.begin()));
    for (size_t i = size; i < padded; ++i) assert(actual[i] == 0);
  }
  std::vector<char> first(4097, 'b'), second(4097, 'c');
  { TensorWriter writer(path + ".tensor");
    assert(writer.writeRecord(first.data(), first.size()) == 0);
    assert(writer.writeRecord(second.data(), second.size()) == 8192); }
  auto a = read(path + ".tensor_0"), b = read(path + ".tensor_1");
  assert(a.size() == 8192 && b.size() == 8192);
  assert(std::equal(first.begin(), first.end(), a.begin()));
  assert(std::equal(second.begin(), second.end(), b.begin()));
}
""")
            subprocess.run(["g++", "-std=c++17", str(work / "test.cpp"),
                            str(work / "aligned_buffer.cpp"),
                            str(work / "tensor_writer.cpp"), "-o",
                            str(work / "test")], check=True, timeout=30)
            subprocess.run([str(work / "test"), str(work / "payload")],
                           check=True, timeout=10)


if __name__ == "__main__":
    unittest.main()
