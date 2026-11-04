# S4a dedicated component runner / cooperative barrier independent review

Verdict: PASS_SOURCE_REVIEW_FOR_DEPLOYMENT_AND_FOCUSED_CPU_VALIDATION_ONLY. No new runtime authorization. Reviewer read source and hashes only; no runner, tests, SDK, Store, GPU, remote, Git or sync was executed.

Stable reviewed files:
- work/s4a_component_runner.py SHA256 b631fc291851d223b199efd07ba037a2574d2f92e0d9a3c47f49d62dae2fbb24
- work/test_kimi_tensor_component.py SHA256 7af76fda0b764a753e380de5b3c96d4e2c768ecf5c6de730d95ec018af89c1fe
- work/test_component_barrier.py SHA256 926ef9f81fb8dc6eed141df1ed8dee14cbbc30f54af28e12de82689c38e17c4a
- work/test_component_cleanup.py SHA256 dc83cf2c53074ce1601eda57536f049cca4da400fbc79eb3c96d078b101cdba7

No blocking source defect found in this dedicated one-attempt parent/server/client protocol, conditional on the already reviewed blocking-confirm SDK and the planned guarded same-container mounts/environment. It is a component orchestration helper, not a substitute SDK loader or general deployment framework.

## Source findings

GO metadata is restricted to the evidence parent's component-go.json, regular non-symlink file/ancestors, exact mode0644, <=1024 bytes, exact {monotonic,run_id} keys, matching run ID and age0..<4s. No guard nonce is read or distributed. This relies on the external controller to atomically publish the intended task-owned file from its guard GO clock; the runner does not prove that origin itself. UID2147 readability and the container's monotonic clock correspondence remain integration checks.

Deadlines are absolute GO+8 startup, GO+16 client and GO+21 stop, leaving four seconds before external GO25. All wait/select durations derive from those absolute deadlines, not fresh per-phase durations. failure_order attempts server stop by stop_end-1 before waiting/stopping the client. stop_owned checks owned PID/startticks before signals, waits/reaps, escalates only that process, and verifies poll-exit plus disappearance/replacement of the original /proc identity. Failed server reap prevents ACK and client stop; parent parks for native containment. No guaranteed completion latency is inferred from these cutoffs. Timeout remains FAILED_RETAINED and does not extend guard25/cleanup65/release60/native140/full180.

The parent starts only official server.serve with registration_required=True, host127.0.0.1/8073,64MiB host pool,1MiB chunks,one I/O thread. Both child commands use -I plus explicit sys.path insertion of /source/sllm_store; client runs the component file at that root. LD_LIBRARY_PATH prepends /source/sllm_store/sllm_store while preserving the image path. This avoids relying on isolated-mode PYTHONPATH or importing the image's stale SDK. Mount-to-active-root identity and reviewed SDK source/ELF hashes must still be checked by the controller; a hardcoded /source path alone does not prove deployment identity.

Component primary-exception finally calls wait_server_failure_barrier before clearing actual/restored, GC/synchronize or process exit. The original primary exception/traceback remains live. Its failure request is length-bounded; parent receives it and enters server-first stop. Parent ACK contains server PID/startticks only after verified server reap. Client checks exact ACK and independently checks original /proc identity is gone before proceeding. EOF/malformed ACK cannot release the barrier: client logs FAILED_RETAINED and parks. After a verified server-exit barrier, CPU unload is skipped because the server is gone; channel close and local cleanup retain the original exception. This change is necessary to avoid a dead-service RPC wait after the barrier, and is reflected in cleanup tests.

Success still requires client exit0 plus final OFFICIAL_COMPONENT_ROUNDTRIP JSON, not socket EOF alone. Component's exact-bit checks and last-alias driver release complete before parent stops the server. Parent's success label covers its local roundtrip and ordered stop only; external guard, driver/NVML release observation and noRestart evidence remain separate required evidence.

Failure labels are appropriately narrower. SERVER_REAPED_ACK_SENT_CLIENT_REAPED records parent send and process exit; it does not establish the client consumed ACK or performed cooperative cleanup. A timed-out client may still require termination after server reap. Only the client's independent SERVER_EXIT_VERIFIED receipt supports cooperative barrier consumption. All such paths retain FAILED_RETAINED. CLIENT_ALREADY_EXITED_NO_BARRIER explicitly covers crash/early exit without pretending barrier success. Guard-forced teardown or inability to reap is not a cooperative PASS.

## CPU test scope and remaining checks

The focused six-test source covers no-ACK-before-server-exit, delayed server kill/reap before ACK/client stop, unreapable server withholding ACK/client stop, client crash classification, expired deadline server-first ordering, and actual component finally retaining owner/traceback until a simulated valid exit ACK. Cleanup test now asserts barrier-before-clear and no CPU unload after primary-error barrier while retaining original exception identity. These use fake process objects and AST extraction; they prove local ordering logic, not actual Linux signals/reap, native lifetime, CUDA, gRPC closure or physical release. Writer reports six barrier plus one cleanup PASS and existing archive checks with Windows symlink skip; reviewer did not rerun them. Deployment CPU receipt should use actual deployed paths/hashes, and Linux archive symlink regression should remain enabled.

Additional CPU-only hardening can test malformed/EOF ACK parking and main's source/cutoff constants without launching Store; their absence is not evidence that the current source permits those ACKs, because the helper visibly rejects and parks them. Do not add production abstractions merely for those tests.

Before a separately authorized runtime, controller review must establish task-owned atomic GO0644 publication, source/archive read-only mounts, evidence writability, exact active SDK/ELF identities, approved UUID visibility in both environment variables, PID/container budget, server/client same-container membership, and independent guard/observer/window readiness. These are execution integration requirements, not granted by this source verdict. No new image/build, model, checkpoint-wide read or full conversion is needed.

S4a remains readback not run. The barrier is cooperative process containment; it does not prove eager raw-allocation release on ambiguous errors, repair public nonblocking failures, guarantee arbitrary client crash ordering, or close S5 full SDK ownership. Process exit/quiet60 cannot replace the successful-path in-process last-alias evidence.

## Graceful-success tiny update independent review

PASS_SOURCE_ONLY_FOR_DEPLOYMENT_AND_FOCUSED_CPU_VALIDATION. Reviewed runner SHA256 b5e8de4446349f3fb571bb59d2c59ff3e3c1d1a3c3f359e54dbd96d1e4266543 and barrier test SHA256 68c3ed0e9c1164fb72af8c19df4c4ea69e177764b5f99b01117d7249d58e0272. Component helper and failure_order retain the prior reviewed semantics.

New success-only graceful_server_stop checks live owned PID/startticks, sends SIGINT and waits within the existing GO21 stop deadline; success requires return code zero and verified original-process reap. Exact official serve catches cancellation/KeyboardInterrupt and awaits its existing server.stop(5), so SIGINT is an appropriate request for that shutdown path under asyncio.run; no latency success is assumed. Nonzero or timeout cannot produce success and instead enters unchanged server-first fallback handling. Current success result records SIGINT_GRACEFUL, forced=False and server/client return codes; failure records FAILURE_TERM_KILL_FALLBACK/forced=True to mean fallback entered, not necessarily that KILL was sent. This resolves the prior ambiguity between successful graceful shutdown and forced containment.

Source tests cover zero-exit plus reap, rejection of nonzero exit and timeout, alongside the original six barrier checks. Read work/s4a-barrier-cpu-test.txt showing nine PASS; reviewer did not rerun tests or perform any deployment/remote/runtime operation. The focused tests remain fake-process CPU ordering evidence, not real signal/native/GPU proof. Runtime authorization and independent guard/release evidence remain absent.
