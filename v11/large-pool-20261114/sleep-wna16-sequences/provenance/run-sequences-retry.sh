#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-kimi-wna16-sequences-20261117
for seq in S1 S2 S3; do
    rep=1; if [ "$seq" = S1 ]; then rep=2; fi
    date -Is
    log=$(mktemp "$seq-controller.XXXXXX.log")
    printf '%s %s\n' "$seq" "$log"
    python3 -Bu v11_run_b2.py --sequence "$seq" --levels 2 --reload-mode checkpoint --kv-cache-gib 16 --rep "$rep" --booking-start 2026-11-17T08:00:00+08:00 --booking-end 2026-11-18T07:00:00+08:00 >"$log" 2>&1
    python3 -B - "$seq" "$rep" <<'PY'
import json, pathlib, sys
run, = pathlib.Path('runs').glob(f'B2-WNA16-{sys.argv[1]}-r{sys.argv[2]}-*')
q = json.loads((run / 'qualification.json').read_bytes())
assert q['passed'] and q['final_release_seconds'] >= 60 and q['final_all_gpu_zero'], q
print(run, q['final_release_seconds'])
PY
done
