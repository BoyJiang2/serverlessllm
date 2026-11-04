#!/bin/bash
set -euo pipefail
export PATH=/scripts:$PATH
bash -o pipefail /scripts/run-benchmark.sh --model-name meta-llama/Llama-3.1-8B-Instruct --num-replicas 1 --benchmark-type random --mem-pool-size 32GB --storage-path /bench/storage --results-path /bench/results/random
