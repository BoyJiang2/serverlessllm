#!/bin/bash
set -euo pipefail
export PATH=/scripts:$PATH
unset NUM_REPLICAS
bash -o pipefail /scripts/run-benchmark.sh --model-name Qwen/Qwen3-8B --benchmark-type random --mem-pool-size 32GB --storage-path /bench/storage --results-path /bench/results/random
bash -o pipefail /scripts/run-benchmark.sh --model-name Qwen/Qwen3-8B --benchmark-type cached --mem-pool-size 32GB --storage-path /bench/storage --results-path /bench/results/cached
