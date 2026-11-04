# Llama generation-policy correction: preserved intermediate result

Both backends now restore the complete baseline generation config, use identical prompt/max_new_tokens=50, paired seed20261104+index and identical replica permutation. Random30 completed with one token mismatch (row23, seed20261127, first difference at generated token43). Cached5 token sequences all matched. This result does not satisfy full paired-output acceptance.

CPU inspection of real SafeTensors baseline proves both Llama RoPE buffers are FP32, while old SLLM construction casts them to FP16 (max absolute error0.0001640915870666504). A separate minimal initializer fix preserves FP32 buffers while setting parameter dtype at construction. Actual expressions from both loader branches pass CPU baseline-buffer comparisons for LlamaFP16 and QwenBF16. GPU validation of that change remains pending.

Raw files and this failure remain unchanged. Container09791c7848e9bfbbf023d958f141b17967d6a3c5be09d918bdc452261b7638f3 exited0; replicas cleaned and GPU7memory0 throughout61.87seconds release observation. Previous reproduction with missing generation config is not policy-parity evidence.
