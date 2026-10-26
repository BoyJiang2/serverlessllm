#!/bin/bash
set -euo pipefail
run_id="$1"
[[ "$run_id" =~ ^sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$ ]]
test "$(id -u)" = 0
base="/data/brick1/boyjiang/$run_id"
test "$(stat -c %u "$base")" = 2147
test "$(systemctl is-active "$run_id-deadline-v6.timer")" = active
remaining=$((1792954200-$(date +%s)))
test "$remaining" -gt 120
systemd-run --unit="$run_id-kernelobserver12" --slice="$run_id.slice" --property="RuntimeMaxSec=$remaining" --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:$base/kernel-observer12-private.jsonl" --property="StandardError=append:$base/kernel-observer12-error.log" /usr/bin/journalctl --kernel --since=now --follow --output=json
systemd-run --unit="$run_id-dockerobserver12" --slice="$run_id.slice" --uid=2147 --property="RuntimeMaxSec=$remaining" --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:$base/docker-events12.jsonl" --property="StandardError=append:$base/docker-events12-error.log" /usr/bin/docker events --since=0s --filter="label=sllm.run_id=$run_id" --format='{{json .}}'
systemctl show "$run_id-kernelobserver12.service" "$run_id-dockerobserver12.service" -p ActiveState -p MainPID -p User -p ControlGroup -p RuntimeMaxUSec
sudo -k
