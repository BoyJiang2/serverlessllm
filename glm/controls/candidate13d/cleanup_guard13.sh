#!/bin/bash
set -euo pipefail
umask 077
run_id=sllm-glm51fp8-20261025T145409Z-0b3a25a1
test "$(id -u)" = 0
test "$1" = "$run_id"
case "$2" in
  main) target="$run_id" ;;
  probe) target="$run_id-guard-v13-probe" ;;
  *) exit 2 ;;
esac
base="/data/brick1/boyjiang/$run_id"
private="$base/root-evidence-private13"
test ! -L "$private"
test "$(stat -c %u "$private")" = 0
exec >> "$private/cleanup-v13-$2.log" 2>&1
printf 'cleanup_started %s target=%s\n' "$(date -Is)" "$target"
# Kill this exact owned slice first; Docker/logging failures cannot delay the deadline.
systemctl kill --kill-who=all --signal=SIGKILL "$target.slice" || true
failed=0
mapfile -t containers < <(timeout 10s docker ps -aq --filter "label=sllm.run_id=$target")
for container in "${containers[@]}"; do
  if test "$(timeout 10s docker inspect "$container" --format '{{index .Config.Labels "sllm.uid"}}')" != 2147; then
    printf 'identity_mismatch_skip %s\n' "$container"
    failed=1
    continue
  fi
  timeout 15s docker stop --time=5 "$container" || failed=1
  timeout 10s docker inspect "$container" --format '{{.Name}} {{json .State}}' || failed=1
done
remaining=$(timeout 10s docker ps -q --filter "label=sllm.run_id=$target")
test -z "$remaining" || failed=1
group=$(systemctl show "$target.slice" -p ControlGroup --value)
if test -n "$group" && test -f "/sys/fs/cgroup$group/pids.current"; then
  test "$(cat "/sys/fs/cgroup$group/pids.current")" = 0 || failed=1
fi
# Only after zero owned resources, cancel this own timer to keep final raw evidence immutable.
if test "$2" = main && test "$failed" = 0; then
  timeout 10s systemctl stop "$run_id-deadline-v13.timer" || failed=1
fi
printf 'cleanup_finished %s remaining=%s failed=%s\n' "$(date -Is)" "$remaining" "$failed"
exit "$failed"
