# Kimi fixed32 single-token difference: primary-source read-only investigation

Status: CAUSE UNDETERMINED. Source/evidence research only; no runtime, environment change, new probe, full-weight read, Git operation, or automatic retry. Historical failed Storecold attempt remains failed and its gold remains unchanged.

## Observed evidence and coverage

The current public block reconciliation is STOPPED_ON_NEW_GOLD_FAILURE_NO_RETRY03, one qualified condition and zero complete paired blocks. Current SFhot has the expected fixed32 IDs; Storecold has 12635 (物理) instead of 47516 (光学) at one-based position12. Other31 IDs and completion32/finish=length agree. A historical Storepoolhit also matches the expected32 IDs. This proves output disagreement in the observed executions, not its cause or repeatability.

Directly read public fixed32 responses:
- SFhot 33-head-http.stdout SHA256 2e8d400d610e188e1640d868cae097571e4904db8fe10da2c5d7582e86484536.
- Storecold 32-head-http.stdout SHA256 80628a060468c4da0fdf2e81db41fce1f4048989b11a690706218e9753080686.
- historical-pool-hit-fixed32.stdout SHA256 7dad40f1f7cc5823b2e10162d1ce398d1da8b24797254c360477c258ba151ef5.
All are below S0/kimi-four-v1-b01-retry02-partial-block-public-20261107. Each returns only selected/top1 logprob. At position12 SFhot is -0.5291203260421753, historical poolhit -0.5298810601234436, Storecold -0.6373533010482788. Exact runner-up margins are unavailable.

A useful conditional lower bound: under the actual sampler raw normalized-logprob interpretation, exp(SF logprob)=0.58912298 and exp(Store logprob)=0.52868986. Since any other token probability is at most1-p, the top1-vs-any-runner-up logit gaps are at least log(p/(1-p))=0.360341 and0.114886 respectively. Thus the two named candidates' relative logit difference changes by at least0.475227 nat across these executions with the same first11 output IDs. This does NOT identify a layer, prove corruption, or provide exact margins; describing the decision as a proved near-tie would be unsupported. Neutral generation processors must remain checked when interpreting raw versus processed logits.

Existing bounded source/log/metadata extraction: S0/kimi-fixed32-gold-difference-existing-rank-metadata-kernel-excerpts-20261107.json. Both paths show per-rank60 CompressedTensorsWNA16MarlinMoEMethod:Marlin,536 UnquantizedLinearMethod and2 UnquantizedEmbeddingMethod;2762 registered tensors/rank have matching semantic shapes/strides/dtypes/bytes. These are metadata, NOT raw weight equality or coverage of unregistered runtime auxiliaries. Engine logs show TP8 BF16 compressed-tensors/eager, prefix-cache off, chunked-prefill on, custom-allreduce not disabled, engine seed0. Request seed20261024 is a separate sampling setting. FlashInfer sampler is logged as available but disabled by default; FLASHINFER_MLA is rejected for compute capability in the displayed selector. Generic FlashInfer autotuner start/end logs do not establish which operator/tactic ran; in particular they do not override the measured Marlin MoE identity.

## Pinned official source facts

1. vLLM b1388b1fb reproducibility documentation says performance defaults do not provide reproducibility guarantees. Its offline recipes distinguish deterministic scheduling via VLLM_ENABLE_V1_MULTIPROCESSING=0 from batch invariance; online guidance uses batch invariance, with same hardware/version conditions. This is a limitation statement, not proof that this failure is nondeterministic.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/docs/usage/reproducibility.md

2. The pinned sampler greedy_sample is logits.argmax (lines132–133); all_greedy returns before temperature/random sampling (150–158). Raw logprobs are computed before logits processors (69–83); top-k requested probabilities can be returned without changing the greedy token rule (192–225). A seed controls RNG, not identity of floating-point logits produced by different execution paths.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/v1/sample/sampler.py

3. Actual Marlin source: BOTH WNA16 GEMM calls explicitly set use_atomic_add=False and use_fp32_reduce=True (143–144,192–193). Token block size depends on M/topk/E (290–305); final expert-output reduction is torch.sum or the supplied moe_sum (343–350). Therefore blaming an assumed atomic-add Marlin default is contradicted by this source. These settings alone do not establish bitwise whole-model/TP determinism, nor the instantiated moe_sum callback.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/model_executor/layers/fused_moe/fused_marlin_moe.py

4. TP reduction selection is separate: CudaCommunicator tries enabled eligible routes before pynccl fallback (161 onward). CustomAllreduce eligibility includes input byte alignment, contiguous storage, topology and size (217–230); configuration disable_custom_all_reduce=False alone is not a trace proving every collective used that route. A different legitimate rank-to-physical-GPU permutation can alter communication topology without violating the eight-card ownership contract; actual route/order changes remain hypotheses until compared.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/distributed/device_communicators/cuda_communicator.py
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/vllm/distributed/device_communicators/custom_all_reduce.py

5. PyTorch2.10 documents non-associative floating-point arithmetic and possible differences between mathematically equivalent batched/sliced computations. Its reproducibility guidance covers deterministic algorithms for supported operators, with potential errors/performance costs. torch.use_deterministic_algorithms does not by itself prove coverage of vLLM custom Marlin/attention/collective extensions. BF16 GEMM reduced-precision-reduction controls exist, but their applicability to a custom kernel must be established rather than assumed.
https://docs.pytorch.org/docs/2.10/notes/numerical_accuracy.html
https://docs.pytorch.org/docs/2.10/notes/randomness.html

6. CUDA12.9 cuBLAS reproducibility is scoped to its routines/toolkit/hardware and stream/workspace conditions. CUBLAS_WORKSPACE_CONFIG=:16:8 or :4096:8 is documented for relevant multistream cuBLAS workspace behavior; it is NOT a universal fix for Marlin, FlashInfer, TP, restored tensors or this incident.
https://docs.nvidia.com/cuda/archive/12.9.1/cublas/index.html#results-reproducibility

7. The exact vLLM commit requirements list FlashInfer-python/cubin0.6.6, but a dependency declaration is not installed-package proof. FlashInfer v0.6.6 AutoTuner.choose_one profiles runner/tactic candidates, selects measured fastest and caches by configuration; outside tuning it uses cached or fallback tactics. Its autotune(False) context does not purge cached choices or provide an overall reproducibility guarantee. Timing-dependent tactic choice is a plausible mechanism only when the relevant tuned operator actually participates and selections differ. Installed FlashInfer version/source identity and selected operator/tactic are still pending in this report.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/requirements/cuda.txt
https://raw.githubusercontent.com/flashinfer-ai/flashinfer/v0.6.6/flashinfer/autotuner.py

8. VLLM_BATCH_INVARIANT is a pinned beta feature requiring SM90-or-newer hardware. Its validated-model list does not include Kimi-K2.6 packedINT4. It changes attention/other kernels and disables custom allreduce; hardware eligibility does not prove this model/quant path is supported. Do not silently enable it, disable custom allreduce, change BF16 reduction, or disable tuning inside an unchanged formal protocol. Such interventions change the measured computation and need separate source compatibility review and explicit bounded diagnostic authorization.
https://raw.githubusercontent.com/vllm-project/vllm/b1388b1fb/docs/features/batch_invariance.md

## Smallest bounded diagnostic proposal, NOT execution authorization

First finish read-only comparison of existing records: exact prompt IDs/request processors, effective engine/config/seed, per-rank physical map, quant/backend and actual collective/attention/tactic evidence. Matching metadata/counts is necessary but insufficient for loader correctness; neither declare weights damaged nor excuse a restore bug as expected numerical noise.

If a new diagnostic is explicitly approved, preserve the exact32 request shape, greedy parameters, template IDs and capacity, and request at least top2 (or a small fixed top5) probabilities at each step. Capture both candidate IDs47516/12635 at the FIRST divergent common-prefix position12, their ranks/logprobs and actual selected ID. Top2 may still omit one named candidate; do not invent its value. Existing supported selected-token API or a separately reviewed minimal mechanism would then be needed. Avoid teacher-forcing a shortened prefix as the sole reproduction: converting decode into prefill can itself change the kernel shape/path.

A small fixed same-process repetition count can distinguish within-engine variation from cross-process/loader differences; it cannot by itself prove root cause. Keep SF and Store cases separately labeled, same ordering and physical/engine configuration, and preserve all failures. Only after the baseline evidence identifies a specific changed route should one narrowly alter one supported determinism/tactic/collective control, with explicit performance/protocol implications. No automatic retry03, no expanding to40 attempts as diagnosis, no new loader/codec/expert rules, and no weakening exact-gold acceptance.

No source or runtime modification was made. Cause remains UNDETERMINED; the observed drift may reflect a computation-path difference, restored-state difference, or another unmeasured mechanism. Existing evidence does not adjudicate these alternatives.
