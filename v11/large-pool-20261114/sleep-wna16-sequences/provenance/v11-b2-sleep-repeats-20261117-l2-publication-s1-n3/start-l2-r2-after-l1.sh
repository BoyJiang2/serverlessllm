#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117
pid=$(cat l1-batch-single-cycle.pid)
while ps -p "$pid" -o args= | grep -qx 'bash run-l1-batch-single-cycle.sh'; do
    sleep 10
done
grep -q ' qualified=3$' l1-batch-single-cycle.log
temp=$(mktemp l1-audit-before-l2.XXXXXX.json)
python3 -B audit_l1_repeats.py runs >"$temp"
python3 -B - "$temp" <<'PY'
import json,pathlib,sys
rounds = json.loads(pathlib.Path(sys.argv[1]).read_bytes())['rounds']
assert sum(row['passed'] for row in rounds) == 3
print('L1 verified:', ', '.join(row['run'] for row in rounds if row['passed']))
PY
date -Is
test ! -e l2-r2-batch.log
exec bash run-l2-batch.sh 2 >l2-r2-batch.log 2>&1
