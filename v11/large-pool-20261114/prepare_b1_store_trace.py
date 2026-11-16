"""Prepare isolated Store tracing sources for the pinned V11 B1 image.

Pass the original image's csrc/sllm_store directory, or give --source a
directory containing the pinned model.cpp and observed checkpoint_store.cpp.
The output contains sources, a local CPU fixture, and a build command to run
later with no GPU devices. This preparation never starts a container.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


IMAGE = "sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d"
MODEL = "2668e2548f8a49539e6ec2915b870a61c00899fcfd0226c5db4db481afb02f58"
CHECKPOINT = "f8334640d50dc1453e443a2dec87a49f934e79e1876164753167e924b9e0c174"
OBSERVER = "a4b72bac84ba52a1e8e467500f1744609888a9a5d847df4ba1574c5898e0ab1e"
CPU_BUILD = [
    "docker", "run", "--rm", "--runtime", "runc", "--network", "none",
    "--cpus", "4", "--memory", "32g", "--pids-limit", "2048",
    "--tmpfs", "/tmp:rw,exec,nosuid,size=16g,mode=1777",
    "-e", "NVIDIA_VISIBLE_DEVICES=void", "-e", "CUDA_VISIBLE_DEVICES=",
    "-e", "LD_LIBRARY_PATH=/usr/local/cuda/compat:/opt/sllm-v4/lib/python3.12/site-packages/torch/lib",
    "--mount", "type=bind,src=<PREPARED>,dst=/b1-store-trace,readonly",
    "--mount", "type=bind,src=<EMPTY_OUTPUT>,dst=/out",
    "--entrypoint", "/opt/sllm-v4/bin/python", IMAGE, "/b1-store-trace/build_inside.py",
]
HEADER = """#pragma once

#include <algorithm>
#include <cerrno>
#include <cstddef>
#include <cstdint>
#include <time.h>
#include <unistd.h>

namespace b1_store_trace {
inline std::uint64_t Now() noexcept {
  const int saved_errno = errno;
  timespec stamp{};
  const int rc = clock_gettime(CLOCK_MONOTONIC, &stamp);
  errno = saved_errno;
  if (rc != 0) return 0;
  return static_cast<std::uint64_t>(stamp.tv_sec) * 1000000000ULL + stamp.tv_nsec;
}

struct Span {
  std::uint64_t begin_ns = 0, end_ns = 0, call_sum_ns = 0;
  std::uint64_t calls = 0, short_calls = 0, error_calls = 0, clock_errors = 0;
  std::uint64_t requested_bytes = 0, transferred_bytes = 0;

  void Add(std::uint64_t begin, std::uint64_t end, std::size_t requested,
           std::size_t completed, bool ok) noexcept {
    ++calls;
    requested_bytes += requested;
    transferred_bytes += completed;
    error_calls += !ok;
    short_calls += ok && completed < requested;
    if (!begin || end < begin) {
      ++clock_errors;
      return;
    }
    if (!begin_ns || begin < begin_ns) begin_ns = begin;
    end_ns = std::max(end_ns, end);
    call_sum_ns += end - begin;
  }

  void Merge(const Span& other) noexcept {
    if (other.begin_ns && (!begin_ns || other.begin_ns < begin_ns)) begin_ns = other.begin_ns;
    end_ns = std::max(end_ns, other.end_ns);
    call_sum_ns += other.call_sum_ns;
    calls += other.calls;
    short_calls += other.short_calls;
    error_calls += other.error_calls;
    clock_errors += other.clock_errors;
    requested_bytes += other.requested_bytes;
    transferred_bytes += other.transferred_bytes;
  }
};

inline ssize_t TimedPread(Span& span, int fd, void* buffer,
                          std::size_t size, off_t offset) noexcept {
  const auto begin = Now();
  const ssize_t result = pread(fd, buffer, size, offset);
  const int saved_errno = errno;
  const auto end = Now();
  span.Add(begin, end, size, result >= 0 ? result : 0, result >= 0);
  errno = saved_errno;
  return result;
}
}  // namespace b1_store_trace
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace(source, anchor, replacement):
    if source.count(anchor) != 1:
        raise ValueError(f"Expected one source anchor: {anchor!r}")
    return source.replace(anchor, replacement)


def observe_checkpoint(source):
    anchor = "  lock_info.unlock();\n\n  // Allocate memory"
    source = replace(source, anchor, """  LOG(INFO) << "SLLM_CACHE_LOOKUP path=" << model_path
            << " resident=" << model->IsHostLoaded()
            << " bytes=" << model->GetModelSize();
""" + anchor)
    anchor = "  int ret = model->ToHost(num_thread_);"
    source = replace(source, anchor, anchor + """
  LOG(INFO) << "SLLM_CACHE_COMPLETE path=" << model_path
            << " rc=" << ret << " resident=" << model->IsHostLoaded();""")
    if sha(source.encode()) != OBSERVER:
        raise ValueError("Original sources do not reproduce the pinned Store observer")
    return source


def instrument_model(source):
    source = replace(source, '#include "model.h"', '#include "model.h"\n#include "b1_store_timing.h"')
    anchor = "  std::vector<int> file_descriptors;"
    source = replace(source, anchor, anchor + """
  std::vector<bool> direct_open(partition_sizes_.size(), true);""")
    anchor = "        fd = open(tensor_path.c_str(), O_RDONLY);"
    source = replace(source, anchor, anchor + """
        direct_open[partition_id] = false;""")
    anchor = "  size_t chunk_per_thread = (num_chunks + num_threads - 1) / num_threads;"
    source = replace(source, anchor, anchor + """
  // Each reader writes its own per-file counters. Do not log per chunk.
  std::vector<std::vector<b1_store_trace::Span>> reader_io(
      num_threads, std::vector<b1_store_trace::Span>(partition_sizes_.size()));
  std::vector<int> reader_cancelled(num_threads, 0);""")
    anchor = """        if (state_ == MemoryState::CANCELLED) {
          LOG(INFO) << "Loading from disk for model " << model_path_"""
    source = replace(source, anchor, """        if (state_ == MemoryState::CANCELLED) {
          reader_cancelled[thread_idx] = 1;
          LOG(INFO) << "Loading from disk for model " << model_path_""")
    source = replace(source, "            pread(fd, (void*)host_buffers[chunk_idx], size, file_offset);",
                     """            b1_store_trace::TimedPread(reader_io[thread_idx][partition_id],
                fd, (void*)host_buffers[chunk_idx], size, file_offset);""")
    source = replace(source, """            ret = pread(fd, (void*)(host_buffers[chunk_idx] + ret),
                        remaining_size, file_offset);""",
                     """            ret = b1_store_trace::TimedPread(reader_io[thread_idx][partition_id],
                fd, (void*)(host_buffers[chunk_idx] + ret), remaining_size, file_offset);""")
    source = replace(source, "  // close file\n  for (int fd : file_descriptors)",
                     """  const bool cancelled = std::any_of(reader_cancelled.begin(), reader_cancelled.end(),
      [](int value) { return value != 0; });
  for (size_t partition = 0; partition < partition_sizes_.size(); ++partition) {
    b1_store_trace::Span span;
    for (const auto& reader : reader_io) span.Merge(reader[partition]);
    LOG(INFO) << "SLLM_B1_PREAD path=" << model_path_
              << " partition=" << partition << " file=" << partition_paths_[partition]
              << " direct_open=" << direct_open[partition]
              << " expected_file_bytes=" << partition_sizes_[partition]
              << " begin_ns=" << span.begin_ns << " end_ns=" << span.end_ns
              << " call_sum_ns=" << span.call_sum_ns << " calls=" << span.calls
              << " short_calls=" << span.short_calls << " error_calls=" << span.error_calls
              << " clock_errors=" << span.clock_errors << " requested_bytes=" << span.requested_bytes
              << " transferred_bytes=" << span.transferred_bytes
              << " read_error=" << error << " cancelled=" << cancelled;
  }

  // close file
  for (int fd : file_descriptors)""")
    source = replace(source, """            [gpu_replica, device_id, device_ptr_list, host_memory]() {
          auto gpu_loading_queue = gpu_replica->gpu_loading_queue_.at(device_id);
          if (cudaSetDevice(device_id) != cudaSuccess) return 1;""",
                     """            [this, &replica_uuid, gpu_replica, device_id, device_ptr_list, host_memory]() {
          b1_store_trace::Span span;
          const auto log_copy = [&](int rc) {
            LOG(INFO) << "SLLM_B1_H2D path=" << model_path_ << " replica=" << replica_uuid
                      << " device=" << device_id << " begin_ns=" << span.begin_ns
                      << " end_ns=" << span.end_ns << " call_sum_ns=" << span.call_sum_ns
                      << " calls=" << span.calls << " error_calls=" << span.error_calls
                      << " clock_errors=" << span.clock_errors
                      << " requested_bytes=" << span.requested_bytes
                      << " transferred_bytes=" << span.transferred_bytes << " rc=" << rc;
          };
          auto gpu_loading_queue = gpu_replica->gpu_loading_queue_.at(device_id);
          if (cudaSetDevice(device_id) != cudaSuccess) { log_copy(1); return 1; }""")
    source = replace(source, """            if (gpu_replica->state_ == MemoryState::CANCELLED) return 1;
            CUDA_CHECK(cudaMemcpy(
                static_cast<char*>(device_ptr_list.at(handle_idx)) + gpu_offset,
                host_buffers.at(chunk_id) + chunk_offset, size,
                cudaMemcpyHostToDevice), "cudaMemcpy Error");""",
                     """            if (gpu_replica->state_ == MemoryState::CANCELLED) { log_copy(1); return 1; }
            auto* dst = static_cast<char*>(device_ptr_list.at(handle_idx)) + gpu_offset;
            auto* src = host_buffers.at(chunk_id) + chunk_offset;
            const auto copy_begin = b1_store_trace::Now();
            const auto copy_rc = cudaMemcpy(dst, src, size, cudaMemcpyHostToDevice);
            const auto copy_end = b1_store_trace::Now();
            span.Add(copy_begin, copy_end, size, copy_rc == cudaSuccess ? size : 0,
                     copy_rc == cudaSuccess);
            if (copy_rc != cudaSuccess) log_copy(-1);
            CUDA_CHECK(copy_rc, "cudaMemcpy Error");""")
    anchor = '          LOG(INFO) << "Finished loading tensor from memory to device " << device_id;'
    return replace(source, anchor, "          log_copy(0);\n" + anchor)


def instrument_checkpoint(source):
    source = replace(source, '#include "checkpoint_store.h"',
                     '#include "checkpoint_store.h"\n#include "b1_store_timing.h"')
    start = '  LOG(INFO) << "SLLM_CACHE_LOOKUP path=" << model_path'
    source = replace(source, start, """  LOG(INFO) << "SLLM_B1_STORE_LOOKUP path=" << model_path
            << " monotonic_ns=" << b1_store_trace::Now();
""" + start)
    anchor = """      int freed_chunks =
          model->TryFreeHost();  // try to free memory, non-blocking"""
    source = replace(source, anchor, "      const auto eviction_begin = b1_store_trace::Now();\n" + anchor +
                     "\n      const auto eviction_end = b1_store_trace::Now();")
    anchor = '        LOG(INFO) << "Model " << model_path << " is freed from memory";'
    source = replace(source, anchor, anchor + """
        LOG(INFO) << "SLLM_B1_EVICT path=" << model_path
                  << " begin_ns=" << eviction_begin << " end_ns=" << eviction_end
                  << " freed_chunks=" << freed_chunks
                  << " freed_bytes=" << static_cast<size_t>(freed_chunks) * chunk_size_;""")
    anchor = '  LOG(INFO) << "Loading model " << model_path;'
    source = replace(source, anchor, "  const auto recovery_begin = b1_store_trace::Now();\n" + anchor)
    anchor = """  auto ret = model->ToGpu(replica_uuid, device_ptrs, converted_mem_copy_chunks,
                          converted_mem_copy_handles);"""
    return replace(source, anchor, anchor + """
  const auto recovery_end = b1_store_trace::Now();
  LOG(INFO) << "SLLM_B1_RECOVERY path=" << model_path << " replica=" << replica_uuid
            << " begin_ns=" << recovery_begin << " end_ns=" << recovery_end
            << " rc=" << ret;""")


FIXTURE = r"""#include "b1_store_timing.h"
#include <cassert>
#include <cstdio>
#include <cstring>

int main() {
  using b1_store_trace::Span;
  using b1_store_trace::TimedPread;
  auto* a = tmpfile();
  auto* b = tmpfile();
  assert(a && b);
  assert(write(fileno(a), "abc", 3) == 3 && write(fileno(b), "de", 2) == 2);
  char buffer[5]{};
  Span first, second, failed, copy;
  auto got = TimedPread(first, fileno(a), buffer, 5, 0);
  assert(got == 3 && TimedPread(second, fileno(b), buffer + got, 5 - got, 0) == 2);
  assert(!memcmp(buffer, "abcde", 5));
  assert(first.calls == 1 && first.short_calls == 1 && first.transferred_bytes == 3);
  assert(second.calls == 1 && second.short_calls == 0 && second.transferred_bytes == 2);
  assert(first.begin_ns > 0 && first.end_ns >= first.begin_ns);
  assert(second.begin_ns >= first.end_ns && second.end_ns >= second.begin_ns);
  assert(TimedPread(failed, -1, buffer, 5, 0) == -1 && errno == EBADF);
  assert(failed.error_calls == 1 && failed.calls == 1 && failed.transferred_bytes == 0);
  Span total;
  total.Merge(second);
  total.Merge(first);
  assert(total.begin_ns == first.begin_ns && total.end_ns == second.end_ns);
  assert(total.calls == 2 && total.requested_bytes == 7 && total.transferred_bytes == 5);
  assert(total.call_sum_ns == first.call_sum_ns + second.call_sum_ns);
  copy.Add(10, 30, 4096, 4096, true);
  copy.Add(40, 43, 2048, 0, false);
  assert(copy.calls == 2 && copy.error_calls == 1 && copy.transferred_bytes == 4096);
  assert(copy.begin_ns == 10 && copy.end_ns == 43 && copy.call_sum_ns == 23);
  copy.Add(0, 44, 1, 1, true);
  assert(copy.clock_errors == 1 && copy.end_ns == 43);
  fclose(a);
  fclose(b);
}
"""

BUILD_INSIDE = """import hashlib
import json
import os
from pathlib import Path
import subprocess

source = Path('/b1-store-trace')
out = Path('/out')
root = Path('/opt/src/sllm/sllm_store')
manifest = json.loads((source / 'manifest.json').read_bytes())
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

assert os.environ.get('CUDA_VISIBLE_DEVICES') == ''
assert os.environ.get('NVIDIA_VISIBLE_DEVICES') == 'void'
assert not list(Path('/dev').glob('nvidia*'))
import torch
assert not torch.cuda.is_initialized()
assert not any(out.iterdir())
target = root / 'csrc/sllm_store'
for name, expected in manifest['image_sources_sha256'].items():
    assert digest(target / name) == expected, name
for name, expected in manifest['prepared_sources_sha256'].items():
    item = source / name
    assert digest(item) == expected, name
    (target / name).write_bytes(item.read_bytes())
build = root / 'build/temp.linux-x86_64-cpython-312'
with (out / 'build.log').open('x') as log:
    subprocess.run(['cmake', '--build', str(build), '--target', '_checkpoint_store',
                    '--parallel', '4'], stdout=log, stderr=subprocess.STDOUT,
                   check=True, timeout=1200)
artifacts = list((root / 'build').glob('lib*/sllm_store/_checkpoint_store*.so'))
artifact, = artifacts
binary = out / 'observed_checkpoint_store_b1_trace.so'
binary.write_bytes(artifact.read_bytes())
assert not torch.cuda.is_initialized()
(out / 'build-receipt.json').write_text(json.dumps(
    dict(manifest, extension_sha256=digest(binary), extension_bytes=binary.stat().st_size,
         cuda_initialized=False), indent=2) + '\\n')
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cpu-fixture", action="store_true", help="Compile and run the fixture with Linux C++")
    args = parser.parse_args()
    model = (args.source / "model.cpp").read_bytes()
    checkpoint = (args.source / "checkpoint_store.cpp").read_bytes()
    if sha(model) != MODEL or sha(checkpoint) not in (CHECKPOINT, OBSERVER):
        raise ValueError("Unrecognized V11 Store source hashes")
    observer = checkpoint.decode()
    if sha(checkpoint) == CHECKPOINT:
        observer = observe_checkpoint(observer)
    patched = {"model.cpp": instrument_model(model.decode()),
               "checkpoint_store.cpp": instrument_checkpoint(observer),
               "b1_store_timing.h": HEADER}
    manifest = dict(image=IMAGE, image_sources_sha256={"model.cpp": MODEL, "checkpoint_store.cpp": CHECKPOINT},
                    observer_source_sha256=OBSERVER,
                    prepared_sources_sha256={name: sha(text.encode()) for name, text in patched.items()},
                    clock="CLOCK_MONOTONIC", local_cpu_fixture=False, cpu_build_argv=CPU_BUILD)
    args.output.mkdir()
    for name, text in (patched | {"build_inside.py": BUILD_INSIDE, "cpu_fixture.cpp": FIXTURE}).items():
        (args.output / name).write_bytes(text.encode())
    if args.cpu_fixture:
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                        "cpu_fixture.cpp", "-o", "cpu-fixture"], cwd=args.output, check=True)
        subprocess.run([str((args.output / "cpu-fixture").resolve())], check=True)
        manifest["local_cpu_fixture"] = True
    (args.output / "manifest.json").write_bytes((json.dumps(manifest, indent=2) + "\n").encode())
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
