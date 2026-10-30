#!/bin/bash
set -euo pipefail
run_id="$1"
[[ "$run_id" =~ ^sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$ ]]
base="/data/brick1/boyjiang/$run_id"
test "$(stat -c %u "$base")" = 2147
test "$(id -u)" = 0
# v6 window: cleanup at 2026-10-26 02:50:00 +08:00 == 2026-10-25 18:50:00 UTC
test "$(date -u +%s)" -lt 1792954200
test ! -e "/run/$run_id/cleanup-v6.sh"
mkdir -p "/run/$run_id"
chown root:root "/run/$run_id"
chmod 0755 "/run/$run_id"
install -m 0555 "$base/cleanup_v6.sh" "/run/$run_id/cleanup-v6.sh"
chown root:root "/run/$run_id/cleanup-v6.sh"
systemd-run --unit="$run_id-deadline-v6" --on-calendar='2026-10-25 18:50:00 UTC' --timer-property=AccuracySec=1s /bin/bash "/run/$run_id/cleanup-v6.sh" "$run_id" main
test "$(systemctl is-active "$run_id-deadline-v6.timer")" = active
cat > "/run/systemd/system/$run_id.slice" <<EOF
[Unit]
Description=Isolated GLM FP8 experiment $run_id
[Slice]
MemoryMax=1700G
MemorySwapMax=0
TasksMax=8192
EOF
systemctl daemon-reload
systemctl start "$run_id.slice"
systemctl show "$run_id.slice" -p MemoryMax -p MemorySwapMax -p TasksMax
# handover probe: a throwaway container under its own slice must be cleaned by the same script path
target="$run_id-guard-v6-probe"
docker run -d --network=none --runtime=runc --memory=256m --cpus=1 --cgroup-parent="$target.slice" --name="$target" --label="sllm.run_id=$target" --label=sllm.uid=2147 --user=2147:2147 --entrypoint=/bin/sleep aa161e380035 120
pid=$(docker inspect "$target" --format '{{.State.Pid}}')
grep -F "$target.slice/" "/proc/$pid/cgroup"
systemd-run --unit="$run_id-v6-test" --on-active=3s --timer-property=AccuracySec=1ms /bin/bash "/run/$run_id/cleanup-v6.sh" "$run_id" probe
sleep 18
test "$(docker inspect "$target" --format '{{.State.Running}}')" = false
test ! -d "/proc/$pid"
test "$(systemctl is-active "$run_id-deadline-v6.timer")" = active
docker inspect "$target" --format '{{.Name}} {{json .State}}'
cat "$base/cleanup-v6-probe.log"
systemctl show "$run_id-deadline-v6.timer" -p ActiveState -p TimersCalendar -p NextElapseUSecRealtime
sha256sum "/run/$run_id/cleanup-v6.sh"
printf 'guard_v6_passed %s original_probe_pid=%s absent\n' "$(date -Is)" "$pid"

sudo -k
