#!/usr/bin/env bash
set -euo pipefail
base=/home/boyjiang/sllm-v4-pro/cache-competition-20261109
l2="$base/v11-b2-sleep-repeats-20261117"
cd "$base/v11-b2-l1-kdk-exact-20261118"
while ps -p 3703243 -o args= | grep -qx 'bash run-l2-batch.sh 3'; do
    sleep 15
done
grep -q ' repetition=3 qualified=3$' "$l2/l2-r3-batch.log"
test "$(( $(date -d '2026-11-18T07:00:00+08:00' +%s) - $(date +%s) ))" -ge 4200
python3 -B - "$l2/runs" <<'PY'
import json,pathlib,sys
root=pathlib.Path(sys.argv[1])
for seq in ('S1','S2','S3'):
    run,=root.glob(f'B2-WNA16-{seq}-r3-*')
    q=json.loads((run/'qualification.json').read_bytes())
    result,=q['results']
    assert q['passed'] and q['final_release_seconds']>=60 and q['final_all_gpu_zero']
    assert result['pid_continuity'] and result['golds_passed'] and len(result['records'])==7
    print(run.name,q['final_release_seconds'])
PY
date -Is
exec python3 -Bu v11_run_b2.py --sequence L1-KDK --levels 1 --rep 1 --booking-start 2026-11-17T13:00:00+08:00 --booking-end 2026-11-18T07:00:00+08:00
