# Independent user Confirm/GIL source review — 2026-11-05

Verdict: SOURCE_PASS_WITH_EVIDENCE_LIMITS. Read-only review; no source edits, compilation, container, Torch/GPU, Git or synchronization performed. This is not binary-provenance or Store TP8 runtime approval.

## Exact reviewed snapshot

- `sllm_store/sllm_store/server.py`: `a0bf00f10089e76ae1b4528d8d9870cda23aa8f1231fdfd3503c98b304196c25`
- `sllm_store/csrc/sllm_store/checkpoint_store_py.cpp`: `bbca97170ed449efdb83dc668494fd6a26b45d7e0d8ddf7a6a41e524f63e5774`
- `sllm_store/tests/python/test_confirm_model_event_loop.py`: `d86731e8880e87c94eddb8d4f8444db6f94fb1380a4e8e64e73b7c875da451ee`

## Comparison with prior unapplied proposal

The executable server change equals the previous minimal candidate exactly after removing the user's five added comment lines (candidate server SHA 4dc05d9da41dd5bf3318612a4b356c44b33e83a7a3b485106e979124253ce955). The C++ executable change equals the prior single-binding call_guard candidate after removing five comment lines and normalizing only the final newline. The user also adds a final newline to the formerly newline-less C++ file. No additional SDK behavior change was found.

ConfirmModel now awaits asyncio.to_thread for the blocking native wait. Exactly the wait_model_in_gpu binding releases the GIL. Both changes are necessary to keep the asyncio handler thread and Python interpreter available. Native WaitModelInGpu/Model::WaitInGpu use C++ mutex/condition-variable/model state with std::string arguments and int result; the reviewed call does not access Python objects while the GIL is released. Existing mutex ownership protects its shared model lookup/state. Native computation/IPC-close/owner code is unchanged.

Five attempts, ret==0 success, 0.05-second retry sleeps, INTERNAL terminal failure, invalid-argument/unsupported-device status, response fields and exception propagation are preserved. The actual Python 3.12 prefix supports asyncio.to_thread.

## Tests and their limits

The new test extracts the actual production AsyncFunctionDef rather than testing a copied handler. It checks the awaited offload call and a live asyncio heartbeat while a bounded 0.5-second fake storage wait runs. The mock time.sleep releases the GIL; it therefore validates Python scheduling with a GIL-releasing callable. It does not exercise the compiled pybind GIL guard, actual grpc transport/pending queue, CUDA copy or eight-rank restore. Existing client error and IPC-close tests remain relevant within their documented CPU/mock boundaries.

Root's separate receipt user-confirm-gil-host-cpu-regression-20261105.json SHA89ef80eb58b7f104359145a57c769152a01ac80fb66325290091c8c7ba1ecf49 records six host tests passing (2 event-loop +3 client-error +1 IPC CPU fixture). Reviewer read that receipt; did not rerun tests. Identity receipt user-confirm-gil-worktree-identity-20261105.json SHA2dca187e40616d0db09d31894f757dd4489d8f5cf9d9b06e4b23ed01f195864d binds the same three file hashes.

Nonblocking documentation finding: server.py135–139 and test_confirm_model_event_loop.py3–6 state that the blocked handler caused clients to cancel. Actual logs plus grpc1.76 source strongly support pending-request expiry as the mechanism, but these tests do not prove that historical causal chain or a successful production repair. Treat those comments as an explanation/hypothesis, not completed runtime evidence; no user code was changed in this review.

## Build and deployment boundary

The pre-existing feasibility proposal checkpoint-store-confirm-gil-offline-build-feasibility-20261105.json SHA976bee2b62d8f57194679ed37ae70858d88ab597aa0dafeec4e9060744347caf establishes reusable official CMake inputs, existing offline glog/googletest assets and tool/header presence. Preserve its historical findings and old f757 ELF/9d_C/d76 glog identities. A newly found a203691348f5c192e6563ef97cc13ff020cf3d9e9c5d5aa9e7089dbd6a8e8630 ELF with no completed manifest is an unverified artifact: cache /w paths, DISCONNECTED=OFF and ninja elapsed clues are not provenance. Do not claim it was compiled from this snapshot or substitute it into a formal restore until exact source/toolchain/ELF/link closure are established and independently reviewed.

Source publication may proceed with only these explicitly selected three files. Runtime additionally requires authenticated artifact identity, a fresh valid booking, complete budget and existing independent guards/release rules. This review does not grant those gates and does not reset the failed restore or prior gold/history.
