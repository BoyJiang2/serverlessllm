#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117
start=2026-11-17T13:00:00+08:00
end=2026-11-18T07:00:00+08:00
grep -q ' qualified=3$' l1-batch-single-cycle.log
if (( $# != 1 )) || [[ $1 != 2 && $1 != 3 ]]; then exit 2; fi
rep=$1
if [[ $rep = 3 ]]; then grep -q ' repetition=2 qualified=3$' l2-r2-batch.log; fi
for seq in S1 S2 S3; do
    test "$(( $(date -d "$end" +%s) - $(date +%s) ))" -ge 6000
    if compgen -G "runs/B2-WNA16-$seq-r$rep-*" >/dev/null; then exit 3; fi
    log=$(mktemp "L2-$seq-r$rep-controller.XXXXXX.log")
    printf '%s sequence=%s round=%s controller=%s\n' "$(date -Is)" "$seq" "$rep" "$log"
    python3 -Bu v11_run_b2.py --sequence "$seq" --levels 2 --reload-mode checkpoint --kv-cache-gib 16 --rep "$rep" --booking-start "$start" --booking-end "$end" >"$log" 2>&1
    python3 -B - "$seq" "$rep" <<'PY'
import json, pathlib, sys
run, = pathlib.Path('runs').glob(f'B2-WNA16-{sys.argv[1]}-r{sys.argv[2]}-*')
q = json.loads((run/'qualification.json').read_bytes())
result, = q['results']
assert q['passed'] and q['final_release_seconds'] >= 60 and q['final_all_gpu_zero'], q
assert result['pid_continuity'] and result['golds_passed']
assert [r['stage']['kind'] for r in result['records']] == ['load']*3+['wake']*4
print(run.name, q['final_release_seconds'])
PY
done
printf '%s repetition=%s qualified=3\n' "$(date -Is)" "$rep"
