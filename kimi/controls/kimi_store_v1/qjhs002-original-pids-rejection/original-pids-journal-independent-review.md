# Original CID PID-controller rejection: independent review

Reviewed 2026-11-04. **Verdict: ORIGINAL_PID_CONTROLLER_REJECTION_CONFIRMED.** This adds evidence to the original failed run; it does not rewrite its frozen receipt or authorize a GPU retry.

CID: `c6ae802f821246c41cf8982688ee6e68e412cdba9182cc11a1ec85ee64c056a7`.
Source summary: `work/original-cid-journal-20261104.json`, SHA256 `549d598ade2773f55dbde17c8bc946809a56fbafdc97b3bc7861643d3a8be237`.
Primary retained entry: `work/original-pids-kernel-exact-20261104.jsonl`; its kernel transport, exact message, cursor, boot ID and timestamps were inspected. Its `_BOOT_ID=24dbe46388f247c9b4836ae220e34807` equals the normalized current boot UUID supplied by root. This reviewer did not execute a host command to re-read the current boot ID.

## What is now confirmed

The retained primary record has `_TRANSPORT=kernel`, hostname `qjhs2`, and the exact original CID scope in its message. Its realtime journal timestamp is `1793710147951685` (`2026-11-03 20:49:07.951685 +08:00`). The summary associates that same scope with start at20:49:07.549604 and deactivation at20:49:08.231473. Thus a PID-controller fork/task-creation rejection is established in the original container's startup window, rather than inferred merely from pthread EAGAIN. The primary entry has no rejecting process/thread PID. The journal and source-monotonic timestamps differ; retain both without treating journal receipt microseconds as exact syscall timing.

## Exact upstream Linux5.15 semantics

`pids_try_charge` checks the charged cgroup and its ancestors; any exceeded limit returns EAGAIN. `pids_can_fork` logs the initial charged css, not the ancestor where charging failed. Normally this is the forking task's cgroup; with a supplied destination cset it is the destination cgroup. The printed path therefore identifies the accounting/destination scope, not necessarily the limiting scope. The event counter belongs to that initial css, even for an ancestor rejection. Logging occurs only when this counter first reaches1, so one message does not mean one rejected attempt. [Primary upstream source, charging](https://github.com/torvalds/linux/blob/v5.15/kernel/cgroup/pids.c#L125-L157), [logging](https://github.com/torvalds/linux/blob/v5.15/kernel/cgroup/pids.c#L203-L223).

This inspects upstream Linux5.15; the host was previously recorded as Ubuntu5.15.0-91-generic. The distro patch delta has not been independently compared. The exact retained kernel message matches the inspected upstream logging path.

## Causation and remaining limits

The confirmed same-CID startup rejection plus the original hook runtime/cgo pthread EAGAIN strongly supports PID-controller enforcement as an explanation of that hook failure. **Hook-specific causation remains supported, not proven:** the kernel entry provides no PID/TID/syscall, and another task in the same charged scope could have generated it. It does not establish whether the local pids.max128 or an ancestor limit rejected the task, actual concurrent task counts, the complete hook sequence, the number of rejections, or why pressure existed. Original OCI/leaf counters and failure-time process identities remain missing. Memory/other independent failures are not excluded.

The later instrumented scratch control demonstrates that the current host binary can execute --version at createContainer with pids128, memory2GiB and CPU quota1. It does not contradict this historical rejection or reconstruct its missing ancestor/transient state. The new evidence changes the original rejection category from unknown to confirmed; it does not establish that changing128 would be correct, remove the failure stop, or grant GPU admission.

## Smallest useful next step

Preserve this append-only evidence with its cursor/boot binding. Build a read-only historical difference table from already-retained original request settings, Docker/systemd records and exact-CID logs: original requested PID limit versus effective local/ancestor limits, and known versus missing task/rlimit/hook identity data. Do not substitute current ancestor counters or the fallback CDI spec for historical facts. If retained records cannot identify the limiting domain/task, hand this precise missing evidence to the runtime administrator for a separately reviewed capture plan; do not repeat generic CPU success controls, tune PID/affinity, invoke device-modifying hooks or retry GPU on this result.

Reviewer actions: local evidence reads and primary source inspection, plus these small work reports. No host command, new test, upload, Git operation, environment change or historical-receipt modification was performed.
