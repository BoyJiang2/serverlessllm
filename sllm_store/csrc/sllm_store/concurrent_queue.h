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
#pragma once

#include <condition_variable>
#include <mutex>
#include <queue>

template <typename T>
class ConcurrentQueue {
 public:
  ConcurrentQueue() = default;
  ConcurrentQueue(const ConcurrentQueue&) = delete;
  ConcurrentQueue& operator=(const ConcurrentQueue&) = delete;

  void enqueue(T item);
  void close();
  T dequeue();
  bool isEmpty();

 private:
  std::queue<T> queue_;
  bool closed_ = false;
  std::mutex mtx_;
  std::condition_variable cond_;
};

template <typename T>
void ConcurrentQueue<T>::enqueue(T item) {
  std::unique_lock<std::mutex> lock(mtx_);
  if (closed_) return;
  queue_.push(std::move(item));
  lock.unlock();  // explicitly unlock before notifying to minimize the waiting
                  // time of the notified thread
  cond_.notify_one();
}

template <typename T>
T ConcurrentQueue<T>::dequeue() {
  std::unique_lock<std::mutex> lock(mtx_);
  while (queue_.empty() && !closed_) {
    cond_.wait(lock);  // release lock and wait to be notified
  }
  if (queue_.empty()) return T{};
  T item = std::move(queue_.front());
  queue_.pop();
  return item;
}

template <typename T>
bool ConcurrentQueue<T>::isEmpty() {
  std::unique_lock<std::mutex> lock(mtx_);
  return queue_.empty();
}

template <typename T>
void ConcurrentQueue<T>::close() {
  std::lock_guard<std::mutex> lock(mtx_);
  closed_ = true;
  cond_.notify_all();
}
