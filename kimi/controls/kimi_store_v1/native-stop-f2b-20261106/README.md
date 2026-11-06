# Kimi Store final shutdown: reconciled evidence and F2b correction

Authorized node: qjhs000; source: /home/boyjiang/kimi-new/ServerlessLLM.
Source commit efcb9d4d930254dfd4d048e204d7266ab06a65fd was published on kimi-qjhs002-aligned-tail and its GitHub ref verified.

The two historical 30-second Store stop failures remain failures. Their 10/20 second raw snapshots belong to distinct PID/startticks/container identities; the sanitized summary has the correct mapping. Namespace PID 1232 corresponds to host PID 171691 in the pool-progress run; the earlier run used namespace PID 1233 / host PID 3765980.

Observed log intervals are 770.043097 seconds for pool initialization and 21.691433 seconds for the first 1024 counters. The original compiled code ignored cudaHostUnregister return values. Those counters show both calls returned, not that all CUDA unregisters succeeded. Serial iteration does not establish constant latency, a measured 217-second completion, or a new exit allowance. The V2 document's stronger successful-unregister wording is not accepted as evidence.

The user F2b drain -> ClearMem -> pool release ordering is preserved. The correction retains buffers on failed unregister, rejects outstanding borrowers, makes destructor errors nonthrowing, and propagates explicit release failures after confirmed gRPC termination. Background task results are drained and logged; prior task failure is not reclassified as a shutdown failure.

Seven whole-C++ stub tests and eight actual-serve AST tests passed; the source review accepted this error contract. These are CPU/source evidence only. The historical release-v2 ELF lacks an immutable build/import provenance receipt and cannot represent the corrected source. No new ELF, deployment, GPU execution or 640 GiB normal shutdown is claimed here. L1 remains incomplete; no Store or fixed32 performance measurement is approved by this evidence.

GLM reanalysis remains owned by the original ServerlessLLM agent. Its reported numerical results still require artifact verification, and its event gate is pending. We did not modify GLM inputs/results or rerun GLM. Four Kimi conditions and historical gold/failures remain unchanged.
