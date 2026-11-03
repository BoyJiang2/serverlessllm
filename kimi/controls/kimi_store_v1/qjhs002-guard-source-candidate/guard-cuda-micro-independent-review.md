# Single-CID guard: independent source review

2026-11-04 verdict: **PASS_SOURCE_SCOPE_FOR_BOUNDED_CPU_FAULT_INJECTION**, conditional on the separately reviewed two native40-second fallbacks and launcher checks. This does not establish kernel cleanup success, validate every guard failure mode, or grant GPU admission.

Exact candidate: `work/guard_cuda_micro_candidate.py`, SHA256 `48c3729fb1d6137f3bd44bc3c87ffe18da40dd101b5afaf280ed2f794910b59d`. The rejected75ba source remains separately preserved as `work/guard_cuda_micro_rejected_75ba6db0.py`, SHA256 `75ba6db0df73dac6e2effeef7c4123d5e6c8f036c887a3c4bfd232f5c537a9a4`; it was not edited.

## Rechecked source fixes

- ARM/ACK publication writes an exclusive temporary file, syncs it, then publishes a complete file. GO-ACK exceptions, including directory fsync failure after publication, now set a failure and immediate abort trigger, then enter the cleanup loop instead of exiting.
- Each snapshot obtains its own fresh OCI identity result. A missing scope closes the old dirfd. A replacement inode requires that snapshot's valid full-CID/cgroupsPath OCI check before repinning; historical oci_seen alone never authorizes a new generation. cgroup.kill remains relative to the pinned directory fd, with path/dev/inode comparison, not an unconstrained path write.
- The65-second cutoff check now precedes the first quiet timestamp assignment. Cleanup first observed after GO65 is retained as failure instead of becoming a release pass.
- Root-owned runc matching requires exact final CID and a unique --root equal to the reviewed namespace; shim matching requires exact -id/CID and -namespace/moby pairs. PIDfd acquisition is followed by a second full identity check, preventing PID-reuse substitution. Later matching runtimes are scanned continually and signaled against the already-due deadline.
- Normal discovery/capture/signal/log errors inside the post-GO loop retain failure flags and continue cleanup. ABORT after GO accelerates the trigger, marks failure, and continues watching/cleaning; ABORT before GO denies start. Raw OCI is kept0600 on the server; selected logs filter process/hook environment to NVIDIA_CTK_DEBUG. No Docker API kill, GPU query, global service action or unrelated resource cleanup is present.

## Budget and execution conditions

ARM permits10 seconds for GO. The regular cutoff is GO25, external runtime TERM then KILL after3 seconds; scope cgroup.kill acts at the cutoff. Cleanup cutoff65 and an additional60 seconds release fit the loop's GO125 end. With ARM10 this is at most135 seconds before final-output overhead, fitting a separately verified native guard unit140 and total180-second budget. The script itself does not create or verify that native unit. A cycle may overrun due to OS scheduling/I/O; observed cycle timing and native timers remain material evidence.

For this CPU fault injection only, the real systemd-created docker-CID scope must have native Runtime40/Stop3, and the parent runc launcher must independently have native40/Stop3. They are siblings, so neither is treated as covering the other's subtree. Verify actual scope/launcher identity, active state, kill properties, and remaining deadlines before SIGSTOP. Scope hook ready plus SIGSTOP is not enough by itself: require guard scope kill and pidfd KILL/identity death before both native deadlines, then at least60 seconds clear observation. If a fallback acts first or required identity/scope evidence is missing, retain FAILED/NOT_INJECTED; do not attribute fallback cleanup to guard success. Only the actual CPU recipe can validate those external conditions.

## Regression evidence and limits

`work/test_guard_cuda_micro.py` SHA256 `8bcd1d7db44642ad169e5523552157a7a2ad88a06557e04fb99e7a458167a46f` extracts actual source, without executing the root entry point. The supplied server log reports four offline passes: published-ACK/fsync failure control flow, new-generation directory-fd targeting, rejection without fresh OCI, and late first-quiet cutoff. Real temporary-directory inodes are used for scope replacement, but cgroup.kill is an ordinary test file; these tests do not exercise kernel cgroup killing or PIDfd signaling. The supplied old-source log records two assertion FAILs, without ERROR, reproducing the two previous scope defects. This reviewer read these logs and AST-parsed source/tests; no tests were rerun.

Remaining boundaries: missing OCI cannot authorize a new scope pin and remains failed evidence; transient processes can evade polling; inaccessible/malformed metadata retains failures rather than being treated as zero. The guard must be matched to the actual future OCI format/namespace separately. One nonblocking receipt edge remains: final emit can add an event-log failure after the result status/list was constructed; external review must not accept a final PASS if final logging failed or required evidence is absent. This does not block the doubly bounded CPU exercise but prevents treating source review as comprehensive production readiness.

Reviewer actions: local source/log reads, SHA/AST checks and this report only. No host execution, sudo, container, runc, GPU, Git, upload, production edit or change to rejected/historical evidence.
