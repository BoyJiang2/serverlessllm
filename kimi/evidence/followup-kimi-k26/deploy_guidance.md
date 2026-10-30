# Kimi-K2.6 Deployment Guide

> [!Note]
> This guide only provides some examples of deployment commands for Kimi-K2.6, which may not be the optimal configuration. Since inference engines are still being updated frequenty,  please continue to follow the guidance from their homepage if you want to achieve better inference performance.

> [!Note]
> Kimi-K2.6 has the same architecture as Kimi-K2.5, and the deployment method can be directly reused.
## vLLM Deployment

You can refer to https://recipes.vllm.ai/moonshotai/Kimi-K2.5 for the newest deployment guide.

This model is available in nightly vLLM wheel:
```
uv pip install -U vllm \
    --torch-backend=auto \
    --extra-index-url https://wheels.vllm.ai/nightly
```

Nightly wheels may be unstable and are considered experimental. For stable production use, we recommend vLLM 0.19.1, which has been manually verified.

Here is the example to serve this model on a H200 single node with TP8 via vLLM:
```bash
vllm serve $MODEL_PATH -tp 8 --mm-encoder-tp-mode data --trust-remote-code --tool-call-parser kimi_k2 --reasoning-parser kimi_k2
```
**Key notes**
- `--tool-call-parser kimi_k2`: Required for enabling tool calling
- `--reasoning-parser kimi_k2`: Kimi-K2.6 enables thinking mode by default. Make sure to pass this for correct reasoning processing.

## SGLang Deployment

You can refer to https://cookbook.sglang.io/autoregressive/Moonshotai/Kimi-K2.6 for the newest deployment guide.

This model is supported in SGLang v0.5.10 and later stable releases (no nightly / main build required). `uv` is preferred:

```
uv pip install "sglang>=0.5.10.post1" --prerelease=allow
```

Here is the example for it to run with TP8 on H200 in a single node via SGLang:
``` bash
sglang serve --model-path $MODEL_PATH --tp 8 --trust-remote-code --tool-call-parser kimi_k2 --reasoning-parser kimi_k2
```
**Key parameter notes:**
- `--tool-call-parser kimi_k2`: Required when enabling tool usage.
- `--reasoning-parser kimi_k2`: Required for correctly processing reasoning content.

## KTransformers Deployment
### KTransformers+SGLang Inference Deployment
Launch with KTransformers + SGLang for CPU+GPU heterogeneous inference:

```
python -m sglang.launch_server \
  --host 0.0.0.0 \
  --port 31245 \
  --model /path/to/kimi-k2.6 \
  --kt-weight-path /path/to/kimi-k2.6 \
  --kt-cpuinfer 96 \
  --kt-threadpool-count 2 \
  --kt-num-gpu-experts 30 \
  --kt-method RAWINT4 \
  --kt-gpu-prefill-token-threshold 400 \
  --trust-remote-code \
  --mem-fraction-static 0.94 \
  --served-model-name Kimi-K2.6 \
  --enable-mixed-chunk \
  --tensor-parallel-size 4 \
  --enable-p2p-check \
  --disable-shared-experts-fusion \
  --chunked-prefill-size 32658 \
  --max-total-tokens 50000 \
  --attention-backend flashinfer
```

Achieves 640.12 tokens/s Prefill and 24.51 tokens/s Decode (48-way concurrency) on 8× NVIDIA L20 + 2× Intel 6454S.

More details: https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/Kimi-K2.5.md .

### KTransformers+LLaMA-Factory Fine-tuning Deployment

You can use below command to run LoRA SFT with KT+llamafactory.

```
# For LoRA SFT
USE_KT=1 llamafactory-cli train examples/train_lora/kimik2_lora_sft_kt.yaml
# For Chat with model after LoRA SFT
llamafactory-cli chat examples/inference/kimik2_lora_sft_kt.yaml
# For API with model after LoRA SFT
llamafactory-cli api examples/inference/kimik2_lora_sft_kt.yaml
```

This achieves end-to-end LoRA SFT Throughput: 44.55 token/s on 2× NVIDIA 4090 + Intel 8488C with 1.97T RAM and 200G swap memory.

More details refer to https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/SFT_Installation_Guide_KimiK2.5.md .
