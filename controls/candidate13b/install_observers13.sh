#!/bin/bash
set -euo pipefail
umask 077
run_id=sllm-glm51fp8-20261025T145409Z-0b3a25a1
test "$(id -u)" = 0
test "$1" = "$run_id"
base="/data/brick1/boyjiang/$run_id"
test "$(stat -c %u "$base")" = 2147
test "$(systemctl is-active "$run_id-deadline-v13.timer")" = active
remaining=$((1792975800-$(date +%s)))
test "$remaining" -gt 120
systemd-run --unit="$run_id-kernelobserver13" --slice="$run_id.slice" --property="RuntimeMaxSec=$remaining" --property=UMask=0077 --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:/run/$run_id/kernel-private.jsonl" --property="StandardError=append:/run/$run_id/kernel-error.log" /usr/bin/journalctl --kernel --since=now --follow --output=json
systemd-run --unit="$run_id-dockerobserver13" --slice="$run_id.slice" --uid=2147 --property="RuntimeMaxSec=$remaining" --property=UMask=0077 --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:$base/docker-events13.jsonl" --property="StandardError=append:$base/docker-events13-error.log" /usr/bin/docker events --since=0s --filter="label=sllm.run_id=$run_id" --format='{{json .}}'
systemctl is-active "$run_id-kernelobserver13.service" "$run_id-dockerobserver13.service"
sudo -k
