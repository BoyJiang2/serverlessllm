#!/bin/bash
set -euo pipefail
unit=kimi-scratch-hook-cpu-20261104-0136.service
base=/data/brick1/boyjiang/kimi-k2.6/p0/${unit%.service}
test "$(id -u)" = 0
test ! -e "$base"
mkdir -m 755 "$base"
sha256sum /data/brick1/boyjiang/kimi-k2.6/p0/scratch-runc-hook-cpu-c6072021.py > "$base/stimulus.sha256"
set +e
systemd-run --wait --unit="$unit" --property=Type=exec --property=Restart=no --property='Delegate=cpu memory pids' --property=RuntimeMaxSec=30 --property=TimeoutStopSec=3 --property=KillMode=control-group --property=SendSIGKILL=yes --property=FinalKillSignal=SIGKILL --property=CPUQuota=100% --property=MemoryMax=3G --property=TasksMax=1024 --property="StandardOutput=append:$base/unit.log" --property="StandardError=append:$base/unit.log" /usr/bin/python3 /data/brick1/boyjiang/kimi-k2.6/p0/scratch-runc-hook-cpu-c6072021.py "$unit" "$base"
result=$?
printf '%s\n' "$result" > "$base/launch-exit.txt"
systemctl show "$unit" --no-pager --property=LoadState,ActiveState,SubState,MainPID,Result,ExecMainCode,ExecMainStatus,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic > "$base/unit-final.txt"
printf 'LAUNCH_EXIT=%s\nBASE=%s\n' "$result" "$base"
cat "$base/unit-final.txt"
exit "$result"
