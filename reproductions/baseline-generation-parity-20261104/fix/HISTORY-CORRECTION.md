# Earlier results and correction scope

The original Llama README reproduction (commit0a435ca4eac95f43c453895c463b3dc457e148df) did not restore SLLM generation_config. Its generation/throughput comparison is not baseline-policy-matched. Original raw results remain historical evidence.

The generation-config-only Qwen run (commit265c2dc64ccdd127842658490bb33460d37d638d) did match all measured parameter bytes, complete sampling config and selected paired output sequences. It did not check nonpersistent RoPE buffers. Subsequent CPU inspection demonstrated a loader-wide cast that changes those buffers, so the final acceptance now explicitly includes buffer dtype/value parity and a fresh GPU rerun.

The generation-config-only Llama run completed random30 and cached5, but random seed20261127 diverged at generated token43. That failure is retained under llama31-generation-parity; cached5 matched. The separate dtype-construction fix is not inferred solely from the token mismatch: actual baseline CPU buffer values prove the FP32-to-FP16 cast gap.

The first targeted diagnostic exited before model loading because its task storage directory was missing. It is preserved separately; the corrected attempt uses a new run directory and container ID. No historical outcome is reset or erased.
