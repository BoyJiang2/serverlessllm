#!/bin/bash
set -euo pipefail
run_id="$1"
[[ "$run_id" =~ ^sllm-glm51fp8-[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$ ]]
base="/data/brick1/boyjiang/$run_id"
case "$2" in
  main) target="$run_id" ;;
  probe) target="$run_id-guard-v6-probe" ;;
  *) exit 2 ;;
esac
exec >> "$base/cleanup-v6-$2.log" 2>&1
printf 'cleanup_started %s target=%s\n' "$(date -Is)" "$target"
mapfile -t containers < <(docker ps -aq --filter "label=sllm.run_id=$target")
for container in "${containers[@]}"; do
  test "$(docker inspect "$container" --format '{{index .Config.Labels "sllm.uid"}}')" = 2147
  timeout 25s docker stop --time=10 "$container" || timeout 10s docker kill "$container" || true
  docker inspect "$container" --format '{{.Name}} {{json .State}}' || true
done
systemctl kill --kill-who=all --signal=SIGKILL "$target.slice" || true
systemctl stop "$target.slice" || true
printf 'remaining_target_containers\n'
docker ps --filter "label=sllm.run_id=$target" --format '{{.ID}} {{.Names}}'
printf 'cleanup_finished %s\n' "$(date -Is)"
