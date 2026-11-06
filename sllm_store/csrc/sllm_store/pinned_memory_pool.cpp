// ----------------------------------------------------------------------------
//  ServerlessLLM
//  Copyright (c) ServerlessLLM Team 2024
//
//   Licensed under the Apache License, Version 2.0 (the "License");
//   you may not use this file except in compliance with the License.
//
//   You may obtain a copy of the License at
//
//                   http://www.apache.org/licenses/LICENSE-2.0
//
//   Unless required by applicable law or agreed to in writing, software
//   distributed under the License is distributed on an "AS IS" BASIS,
//   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//   See the License for the specific language governing permissions and
//   limitations under the License.
//  ----------------------------------------------------------------------------
#include "pinned_memory_pool.h"

#include <chrono>
#include <stdexcept>
#include <cuda_runtime.h>
#include <glog/logging.h>

PinnedMemoryPool::PinnedMemoryPool(size_t total_size, size_t chunk_size)
    : chunk_size_(chunk_size) {
  size_t num_buffers = (total_size + chunk_size - 1) / chunk_size;
  if (num_buffers * chunk_size != total_size) {
    LOG(ERROR) << "PinnedMemoryPool size not multiple of chunk_size";
  }
  LOG(INFO) << "Creating PinnedMemoryPool with " << num_buffers
            << " buffers of " << chunk_size << " bytes";

  for (size_t i = 0; i < num_buffers; ++i) {
    char* buffer = static_cast<char*>(aligned_alloc(4096, chunk_size_));
    if (buffer == nullptr) {
      LOG(FATAL) << "Malloc failed";
    }

    cudaError_t err =
        cudaHostRegister(buffer, chunk_size_, cudaHostRegisterDefault);
    if (err != cudaSuccess) {
      LOG(FATAL) << "cudaHostRegister failed: " << cudaGetErrorString(err);
    }
    pool_.insert(buffer);
    free_list_.insert(buffer);
  }
}

PinnedMemoryPool::~PinnedMemoryPool() {
  try {
    Release();
  } catch (const std::exception& error) {
    LOG(ERROR) << "PinnedMemoryPool destructor release failed: " << error.what();
  } catch (...) {
    LOG(ERROR) << "PinnedMemoryPool destructor release failed: unknown error";
  }
}

void PinnedMemoryPool::Release() {
  std::lock_guard<std::mutex> lock(mutex_);
  if (released_) {
    return;
  }
  if (free_list_.size() != pool_.size()) {
    throw std::runtime_error("PinnedMemoryPool release has outstanding borrowers");
  }
  LOG(INFO) << "PinnedMemoryPool release begin buffers=" << pool_.size()
            << " chunk_size=" << chunk_size_;
  const size_t total = pool_.size();
  size_t attempted = 0;
  size_t unregister_failed = 0;
  cudaError_t last_error = cudaSuccess;
  auto shutdown_started = std::chrono::steady_clock::now();
  for (auto it = pool_.begin(); it != pool_.end();) {
    char* buffer = *it;
    // Keep ownership of a failed registration; it must not be freed as heap memory.
    cudaError_t err = cudaHostUnregister(buffer);
    if (err != cudaSuccess) {
      ++unregister_failed;
      if (err != last_error) {
        LOG(ERROR) << "PinnedMemoryPool cudaHostUnregister failed: "
                   << cudaGetErrorString(err);
        last_error = err;
      }
      ++it;
    } else {
      free(buffer);
      free_list_.erase(buffer);
      it = pool_.erase(it);
    }
    ++attempted;
    if (attempted % 64 == 0) {
      auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                            std::chrono::steady_clock::now() - shutdown_started)
                            .count();
      LOG(INFO) << "PinnedMemoryPool shutdown progress attempted=" << attempted
                << " failed=" << unregister_failed << " total=" << total
                << " elapsed_ms=" << elapsed_ms;
    }
  }
  auto total_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                      std::chrono::steady_clock::now() - shutdown_started)
                      .count();
  LOG(INFO) << "PinnedMemoryPool release attempts finished attempted=" << attempted
            << " failed=" << unregister_failed << " elapsed_ms=" << total_ms;
  if (unregister_failed != 0) {
    throw std::runtime_error("PinnedMemoryPool cudaHostUnregister failures=" +
                             std::to_string(unregister_failed));
  }
  released_ = true;
}

int PinnedMemoryPool::Allocate(size_t size, std::vector<char*>& buffers) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (size == 0) {
    LOG(ERROR) << "PinnedMemoryPool Allocate size is zero";
    return -1;
  }

  int num_buffers_needed = (size + chunk_size_ - 1) / chunk_size_;
  if (num_buffers_needed > free_list_.size()) {
    LOG(ERROR) << "PinnedMemoryPool out of memory (" << free_list_.size()
               << " buffers available, " << num_buffers_needed
               << " buffers needed)";
    return num_buffers_needed - free_list_.size();
  }

  buffers.clear();
  buffers.resize(num_buffers_needed);
  auto it = free_list_.begin();
  for (size_t i = 0; i < num_buffers_needed; ++i) {
    buffers[i] = *it;
    it = free_list_.erase(it);
  }

  LOG(INFO) << "PinnedMemoryPool Allocate " << buffers.size() << " buffers"
            << " free buffers " << free_list_.size() << " total buffers "
            << pool_.size();

  return 0;  // Success
}

int PinnedMemoryPool::Deallocate(std::vector<char*>& buffers) {
  std::lock_guard<std::mutex> lock(mutex_);
  for (char* buffer : buffers) {
    if (pool_.find(buffer) == pool_.end()) {
      LOG(ERROR) << "Buffer not found in pool";
      return -1;
    }
    if (free_list_.find(buffer) != free_list_.end()) {
      LOG(ERROR) << "Buffer already in free list";
      return -1;
    }
    free_list_.insert(buffer);
  }
  LOG(INFO) << "Deallocated " << buffers.size() << " buffers";
  return 0;  // Success
}
