#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117
start=2026-11-17T13:00:00+08:00
end=2026-11-18T07:00:00+08:00
for rep in 1 2 3; do
    test "$(( $(date -d "$end" +%s) - $(date +%s) ))" -ge 2700
    log=$(mktemp "L1-D-r$rep-controller.XXXXXX.log")
    printf '%s round=%s controller=%s\n' "$(date -Is)" "$rep" "$log"
    python3 -Bu v11_run_b2.py --sequence L1-D --levels 1 --rep "$rep" --booking-start "$start" --booking-end "$end" >"$log" 2>&1
    python3 -B - "$rep" <<'PY'
import json, pathlib, sys
run, = pathlib.Path('runs').glob(f'B2-L1-D-r{sys.argv[1]}-*')
q = json.loads((run/'qualification.json').read_bytes())
assert q['passed'] and q['final_release_seconds'] >= 60 and q['final_all_gpu_zero'], q
assert [r['stage']['kind'] for r in q['results'][0]['records']] == ['load','wake'], q
print(run.name, q['final_release_seconds'])
PY
done
printf '%s qualified=3\n' "$(date -Is)"
