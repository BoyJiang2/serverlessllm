# FAILED_RETAINED, not GPU PASS

This archives the consumed cuda-micro-20261103-2045 attempt. The GPU compute marker never occurred and Python did not start. The planned 180-second complete budget was exceeded. Stop GPU escalation and do not retry this failed experiment to replace its terminal evidence.

At 20:49:06 +08 the guard reported ready with GPU 0 MiB and no processes. Docker start timed out after four seconds, and repeated inspect calls also timed out after four seconds. The independent Docker guard could not enforce its complete cleanup budget while runtime create was hung. Root then identity-pinned only runc PID 1368025/startticks 25212033 with pidfd, sent TERM followed by KILL, and confirmed exit. Root reported inspect unlocked at 20:54:07: created, Running=false, Pid=0, exit128, RestartCount=0. This is failed startup followed by release verification, not CUDA compute or guard PASS.

The error identifies createContainer hook #4, startup Go runtime pthread_create EAGAIN. PidsLimit=128 is only a candidate explanation. The actual OCI config/cgroupsPath and cgroup counters were already gone after cleanup, so no causal attribution to that limit is established.

Thirteen release samples span 62.939545 seconds, all non-running/Pid0/RestartCount0/GPU0MiB/no GPU PID, with exact owned runtime PIDs absent. The observer checks exact runc, shim, and docker logs CLI signatures. Sampling proves these observed states, not a successful probe or complete future failure coverage.

Full container inspect and its 172874-byte State.Error are intentionally excluded. The retained summary contains only an at-most-512-character excerpt, error SHA256, and original local inspect SHA256. runtime-root-before.json contains selected proc runtime identity/status/thread records, not full container Config.Env. No secrets, weights, libraries, environments or large traces are archived.

The same milestone includes the previously completed CPU-only installed-vLLM patch dry-run receipt and script. Root ran it in the same immutable image with runc, no GPU, and read-only trees: installed vLLM path /usr/local/lib/python3.12/dist-packages/vllm; inner patch SHA 4d6271fa7cce8e47ccbc10a39812075d3e806426b8cf0a83b42632e807110f84. Forward --batch --fuzz=0 --dry-run -p2 returns 0; forced reverse returns 1; all target hashes before/after match. Classification is unapplied. This did not apply changes to the installed environment and does not prove runtime/model/GPU compatibility. Neither action was rerun while archiving.
