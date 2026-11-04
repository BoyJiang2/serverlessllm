#!/bin/bash
set -euo pipefail
export PATH=/scripts:$PATH
unset NUM_REPLICAS
bash -o pipefail /scripts/run-benchmark.sh --model-name meta-llama/Llama-3.1-8B-Instruct --benchmark-type cached --mem-pool-size 32GB --storage-path /bench/storage --results-path /bench/results/cached
