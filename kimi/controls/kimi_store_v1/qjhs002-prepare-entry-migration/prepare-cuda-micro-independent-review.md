# CUDA micro preparation entry: independent source review

2026-11-04 verdict: **PASS_SOURCE_CHANGE_SCOPE; no source-change blocker found.** This reviews the user's approved entry migration and PID argument change. It is not container/GPU execution approval or guard readiness.

Reviewed local exact copies and historical diff:

- `work/prepare_cuda_micro_migrated.py` SHA256 `f99229c338a240b7a5f78dd1d635004038414c4763f082ad73938280ab2e1c29`, intended active path `/data/brick1/boyjiang/kimi-new/ServerlessLLM/sllm_store/tests/prepare_cuda_micro.py`.
- `work/test_prepare_cuda_micro.py` SHA256 `adb6fa18f07425776d6f5e3bec78c158e737a2c4856e54ee06c1f5c0deac3274`, intended `sllm_store/tests/python/test_prepare_cuda_micro.py`.
- Diff SHA256 `3da2fbde71bda04c55d1561800cfc72fd15f55f119924611bc87a0e8b2059b47`.

The active source root changes only to the approved kimi-new checkout. A recorded `git rev-parse HEAD` replaces the obsolete hard-coded commit assertion and receipt value; this avoids making a future source commit depend on its own SHA. `source_commit` is observed metadata, not proof of a clean worktree, exact script bytes, remote publication or a runtime approval gate. This review binds the actual candidate bytes separately. The reviewer has not re-read server file bytes; upload/parity is reported by root.

Argparse accepts exactly two source filenames and an integer `--pids-limit`, default128, rejecting zero/negative values before Docker interaction. Explicit `--pids-limit 512` reaches Docker as `--pids-limit=512` and is recorded numerically. The inspected `HostConfig.PidsLimit` must equal the requested value. New nonce/name/label/evidence directory are distinct from the original failed run and are exclusively created. The nonce includes pids512 even when the default128 is selected; command and numeric receipt field, rather than the name, establish the requested limit.

The historical diff retains image digest, GPU UUID, Docker socket, NVIDIA runtime, one GPU request, restart/network policy, UID/GID2147, CPU1, memory2GiB and swap-total2GiB, core0, readonly root, tmpfs64MiB, three thread environment limits, driver capabilities, readonly probe mount, timeout45s/kill-after5s, probe Python invocation and the one-MiB/no-model scope. Only PID argument, nonce-derived fields, active root and source-commit recording change. There is no start call. The historical entry/receipt, GLM and other CPU probes are not modified by this diff.

The test resolves correctly from `sllm_store/tests/python/` to `sllm_store/tests/prepare_cuda_micro.py`. Its subprocess/path mocks exercise default128, explicit512 plus actual-HEAD recording, nonpositive rejection and inspect mismatch. The provided server log reports four tests passing in0.012s; it performs mocked offline command construction, not Docker create/start, GPU runtime or CUDA execution. This reviewer only read the log and performed local AST parsing, without rerunning tests.

Existing receipt budget/guard fields remain declarations only: budget180s, inner45+5, guard deadlines, systemd65s and release60s do not create or verify any guardian, valid booking, available time or allocation cap. They must not be read as readiness. As before, create/inspect/assertion failure can leave the uniquely named owned container without a completed prepared receipt; the authorized caller must account for it. No automatic cleanup of other resources is introduced.

No container, GPU, host execution, Git operation or production edit was performed by this review. Any subsequent preparation or run remains subject to the separately applicable authorization, effective resource/budget/independent-guard checks and failure-stop policy.
