# Own-resource hold snapshot boundary

resource-hold-owned-state-20261026-v2.json is the corrected read-only collector. systemd returns a nested actual cgroup path, not /sys/fs/cgroup/<run>.slice directly. The first snapshot's null cgroup fields reflect that collector-path error and must not be interpreted as zero usage. Both raw snapshots are retained.

At2026-10-25T17:31:49Z, exact original-run running-container query is empty; its actual cgroup pids.current is0, memory.current80875520bytes and all memory.events counters0. Charged memory is retained cgroup/cache/kernel accounting, not a claim of zero memory cost or current process RSS/PSS. Old02:50timer remains active. New G1 identity has no running containers, inactive slice and no deadline timer installed. No timer/process/resource mutation was made.

This checks only exact owned identities and does not establish that GPUs or host capacity are available. The user's other-job resource hold remains active. No new experiment process was left running; no guard handover has occurred. Before original-run computation, install and verify its new guard/probe first, respecting any still-active old guard.
