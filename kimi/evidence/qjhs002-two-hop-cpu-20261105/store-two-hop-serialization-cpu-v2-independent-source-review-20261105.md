# Independent CPU two-hop recipe V2 review

Verdict: SOURCE_REVIEW_PASS for the single bounded CPU mechanism attempt, subject to the existing fresh external reservation/resource admission. This review executes no fixture, Torch import, container, Store, model, GPU, build or Git operation.

Frozen inputs independently read and hashed:
- spec: 64a60475d342ac1d2ecda3e2a7e7a6c85f5a996fb915774958cde5ae6c6dfc4f
- final manual-root-command-v2: 5632229f40a4ad252bc4faf8284eeea334d5ef3e8baf37bfee5040cd7bd4ebe3
- final offline-verification-v2: add4f7be67bbeab6cfbe3b46de3ce1f103d9a3fc58aa6e8d0722a4b65adf06cd
- root Python: 05ee89b3529e8757b42a13f0d05f880a43037dcdf1e831d3945e2b1b287e9063 (119 lines)
- child Python: a3d22e29af174e2b525ccdd5395bfabd523f944b337cd17ac671202fbf1f09d4

The final offline receipt binds V2 correctly. The earlier draft receipt remains historical, binds 6eb513..., and is not this admission input. Root and child compile as source without executing; no stale new_sha reference remains in V2.

The child verifies six installed vLLM source hashes before and after, imports actual MsgpackEncoder/MsgpackDecoder/run_method, extracts the installed executor METHOD branch plus enqueue expression and WorkerBase.apply_model, and uses ordinary pickle for the second-hop transport envelope. The old reconstructed callback in argument position must fail at that second pickle (recorded PicklingError or AttributeError); unexpected exceptions escape. The new callable METHOD must become bytes with empty positional args, survive the pickle envelope, invoke the actual worker apply_model method on a fake model yielding exactly sentinel 8675309, and survive the response encoder/decoder roundtrip. Module file identities are recorded. This is actual serializer plus installed-source-fragment mechanism evidence with a fake worker/model and in-memory enqueue, not a live multiprocess SHM/executor, eight-rank, Store or GPU proof. Runtime results remain pending.

The runc/network-none profile has empty CUDA visibility, NVIDIA void, 4 CPUs, 8 GiB memory/swap, PID512, readonly rootfs, UID2147, capabilities dropped, no-new-privileges and bounded 2 GiB temporary filesystem. Only a new versioned small output directory is writable; source is readonly and original artifact hashes are rechecked. Actual Docker config and raw OCI lack NVIDIA devices/hooks and must match the intended profile. The serialization permission is local to this temporary network-none trusted fixed-code CPU process, not a native/external service default.

The 420-second admission includes native outer 417+1 seconds and actual ActiveEnter+418 absolute bound. Guard launch is checked both before (386 seconds required) and after (actual start+383); GO must follow within20 seconds. The normal marker follows actual container/client exit0 and strict result acceptance. Independent guard quiet>=60 seconds, successful native guard exit, exact pinned identities gone and CID scope gone are required. Failure requests ABORT and retains failure; no automatic additional computation. This review does not reset V5 or earlier failed restore evidence and does not authorize a GPU retry.
