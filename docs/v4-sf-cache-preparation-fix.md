# Full-size SF hot admission correction

The genuine SF-hot model pilot stopped before model creation:211117887 target pages,188157260 resident. Original failure, raw vectors and82.7s final release are retained. Clearing the unused Store file cache alone still failed a no-GPU1008GiB/4CPU check (194905180 resident). Do not count either as hot or formal samples.

Host NUMA1 had only17GiB free while NUMA0 had342GiB; existing SF cache was heavily concentrated on NUMA1. The preparation now clears only the frozen Store inputs, and SF-hot resets its own frozen pages before reading. Its four readers use CPUs0/1 on NUMA0 and120/121 on NUMA1; per-file affinity/bytes and the full final mincore vector are recorded. Affinity is restored after each reader; inference affinity is unchanged. No host sysctl, foreign process, file permission, data or global cache-drop changes. All work remains inside the preparation origin/window.

Under the same1008GiB/4CPU ceiling, real CPU-only full-size validation now has all211117887 SF pages resident; cold reset and Store counterpart are0. Both original failed archives and the successful raw archive are attached.144 CPU contracts pass. Corrected full-model SF-hot runtime remains pending; new40 stays unfrozen0/40 and old12 remains unchanged.
