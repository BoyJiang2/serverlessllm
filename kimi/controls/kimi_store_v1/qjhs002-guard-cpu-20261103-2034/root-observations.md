# Root observations (recorded after observation; not raw tool output)

Root confirmed from this run's original tool output that SSH tool session 4790 printed the connection closed and reached terminal exit 0. A new native SSH observation at 2026-11-03 20:31:00 +08:00 reported MainPID=1263200 and ActiveState=active, matching unit-before-disconnect. Those two observations were not independently saved as raw files at the time; this note is a retrospective account, not a reconstruction of raw output.

The separately saved final observation is 2026-11-03 20:32:31 +08:00. Container FinishedAt is 12:31:08.531792334Z (20:31:08 +08:00), providing approximately 83 seconds of no-restart observation. It reports exited, exit 137, RestartCount=0, and unit inactive/Result=success/ExecMainStatus=0.

This is a CPU runc sleep-container deadline probe crossing an actual SSH disconnect and reconnect. It does not authorize or validate CUDA/GPU, model/Store, child processes escaping a container, GPU cleanup, or an independent GPU budget/release observer. Container and unit launch plan is retained as evidence only; it was not rerun during evidence collection.
