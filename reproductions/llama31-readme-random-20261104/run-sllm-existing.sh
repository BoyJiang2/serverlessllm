#!/bin/bash
# ---------------------------------------------------------------------------- #
#  ServerlessLLM Automated Benchmark Runner                                   #
# ---------------------------------------------------------------------------- #

set -e

# Help function
show_help() {
    cat << EOF
Usage: $(basename "$0") [OPTIONS]

Run ServerlessLLM benchmarks with automated model loading and testing.

OPTIONS:
  -m, --model-name NAME        Model to benchmark (default: facebook/opt-6.7b)
  -n, --num-replicas N         Number of test replicas (default: 30)
  -p, --mem-pool-size SIZE     Memory pool size (default: 32GB)
  -s, --storage-path PATH      Model storage directory (default: ~/models)
  -r, --results-path PATH      Results output directory (default: ./results)
  -t, --benchmark-type TYPE    Test type: random|cached (default: random)
      --generate-plots         Generate visualization plots
      --keep-alive             Keep container running after completion
  -h, --help                   Show this help message

EXAMPLES:
  # Basic usage with custom model
  $0 --model-name meta-llama/Meta-Llama-3-8B

  # Full configuration
  $0 --model-name facebook/opt-6.7b \\
     --num-replicas 50 \\
     --mem-pool-size 64GB \\
     --generate-plots

  # Backward compatible (env vars still work)
  MODEL_NAME=facebook/opt-6.7b NUM_REPLICAS=30 $0

For more information, see: benchmarks/README.md
EOF
}

# Parse command-line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -m|--model-name)
            CLI_MODEL_NAME="$2"
            shift 2
            ;;
        -n|--num-replicas)
            CLI_NUM_REPLICAS="$2"
            shift 2
            ;;
        -p|--mem-pool-size)
            CLI_MEM_POOL_SIZE="$2"
            shift 2
            ;;
        -s|--storage-path)
            CLI_STORAGE_PATH="$2"
            shift 2
            ;;
        -r|--results-path)
            CLI_RESULTS_PATH="$2"
            shift 2
            ;;
        -t|--benchmark-type)
            CLI_BENCHMARK_TYPE="$2"
            shift 2
            ;;
        --generate-plots)
            CLI_GENERATE_PLOTS="true"
            shift
            ;;
        --keep-alive)
            CLI_KEEP_ALIVE="true"
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            exit 1
            ;;
    esac
done

# Configuration: CLI args > environment variables > defaults
MODEL_NAME="${CLI_MODEL_NAME:-${MODEL_NAME:-facebook/opt-6.7b}}"
NUM_REPLICAS="${CLI_NUM_REPLICAS:-${NUM_REPLICAS:-30}}"
MEM_POOL_SIZE="${CLI_MEM_POOL_SIZE:-${MEM_POOL_SIZE:-32GB}}"
STORAGE_PATH="${CLI_STORAGE_PATH:-${STORAGE_PATH:-$HOME/models}}"
RESULTS_PATH="${CLI_RESULTS_PATH:-${RESULTS_PATH:-./results}}"
BENCHMARK_TYPE="${CLI_BENCHMARK_TYPE:-${BENCHMARK_TYPE:-random}}"
GENERATE_PLOTS="${CLI_GENERATE_PLOTS:-${GENERATE_PLOTS:-false}}"
KEEP_ALIVE="${CLI_KEEP_ALIVE:-${KEEP_ALIVE:-false}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="${RESULTS_PATH}/benchmark.log"
SUMMARY_FILE="${RESULTS_PATH}/summary.txt"

# Logging function
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Create results directory
mkdir -p "$RESULTS_PATH"

log "=== ServerlessLLM Automated Benchmark ==="
log "Model: $MODEL_NAME"
log "Replicas: $NUM_REPLICAS"
log "Memory Pool: $MEM_POOL_SIZE"
log "Storage: $STORAGE_PATH"
log "Benchmark Type: $BENCHMARK_TYPE"
log ""

# Activate conda environment if running in Docker (serverlessllm/sllm image)
if [ -f "/opt/conda/etc/profile.d/conda.sh" ]; then
    log "Activating conda environment..."
    source /opt/conda/etc/profile.d/conda.sh
    conda activate worker || conda activate head || true
    log "Conda environment: $(conda info --envs 2>/dev/null | grep '*' | awk '{print $1}' || echo 'default')"

    # Install dependencies in Docker
    log "Installing benchmark dependencies..."
    pip install -q seaborn matplotlib pandas sentencepiece 2>&1 | tee -a "$LOG_FILE" || true
fi

# Global variable for sllm-store PID
SLLM_STORE_PID=""

# Function to start sllm-store
start_sllm_store() {
    log "Starting sllm-store server..."
    sllm-store start \
        --storage-path "$STORAGE_PATH" \
        --mem-pool-size "$MEM_POOL_SIZE" \
        --chunk-size 16MB \
        --num-thread 4 \
        > "${RESULTS_PATH}/sllm-store.log" 2>&1 &

    SLLM_STORE_PID=$!
    log "sllm-store started (PID: $SLLM_STORE_PID)"

    # Wait for sllm-store to be ready (up to 300 seconds)
    log "Waiting for sllm-store to be ready (pinning $MEM_POOL_SIZE memory)..."
    local elapsed=0
    local timeout=300
    local interval=2

    while [ $elapsed -lt $timeout ]; do
        # Check if process is still running
        if ! kill -0 $SLLM_STORE_PID 2>/dev/null; then
            log "ERROR: sllm-store process died during startup"
            cat "${RESULTS_PATH}/sllm-store.log"
            exit 1
        fi

        # Check if sllm-store is ready by looking for success message in log
        if grep -q "Starting gRPC server on" "${RESULTS_PATH}/sllm-store.log" 2>/dev/null; then
            log "sllm-store is ready (took ${elapsed}s)"
            return 0
        fi

        sleep $interval
        elapsed=$((elapsed + interval))

        # Log progress every 30 seconds
        if [ $((elapsed % 30)) -eq 0 ]; then
            log "Still waiting for sllm-store... (${elapsed}s elapsed)"
        fi
    done

    # Timeout reached
    log "ERROR: sllm-store failed to become ready within ${timeout}s"
    log "Last log output:"
    tail -50 "${RESULTS_PATH}/sllm-store.log"
    exit 1
}

# Function to stop sllm-store
stop_sllm_store() {
    if [ -n "$SLLM_STORE_PID" ] && kill -0 $SLLM_STORE_PID 2>/dev/null; then
        log "Stopping sllm-store (PID: $SLLM_STORE_PID)"
        kill $SLLM_STORE_PID || true
        wait $SLLM_STORE_PID 2>/dev/null || true
        SLLM_STORE_PID=""
    fi
}

# Function to cleanup on exit
cleanup() {
    log "Cleaning up..."
    stop_sllm_store
}

trap cleanup EXIT INT TERM

cd "$SCRIPT_DIR"
start_sllm_store
python3 test_loading.py --model-name "$MODEL_NAME" --model-format sllm --model-dir "$STORAGE_PATH" --num-replicas "$NUM_REPLICAS" --benchmark-type random --output-dir "$RESULTS_PATH" 2>&1 | tee -a "$LOG_FILE"
stop_sllm_store
python3 generate_report.py --model-name "$MODEL_NAME" --num-replicas "$NUM_REPLICAS" --benchmark-type random --results-dir "$RESULTS_PATH" --output-file "$SUMMARY_FILE" --generate-plots false
