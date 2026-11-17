#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-kimi-wna16-sequences-20261117
pid=$(cat suite-remaining.pid)
deadline=$(date -d 2026-11-18T07:00:00+08:00 +%s)
identity=$(ps -p "$pid" -o lstart=)
while [ -n "$identity" ] && [ "$(ps -p "$pid" -o lstart=)" = "$identity" ] && [ "$(ps -p "$pid" -o stat= | cut -c1)" != Z ]; do
    [ "$(date +%s)" -lt "$deadline" ] || exit 2
    sleep 30
done
audit=$(mktemp audit-final.XXXXXX.json)
python3 -B audit_sequences.py runs > "$audit"
python3 -B - "$audit" <<'PY'
import json, sys
data=json.load(open(sys.argv[1]))
accepted=[r for r in data['rounds'] if r['passed']]
assert len(accepted)==3 and {r['sequence'] for r in accepted}=={'S1','S2','S3'}
assert sum(len(r['steps']) for r in accepted)==21
assert sum(step['stage']=='wake' for r in accepted for step in r['steps'])==12
PY
python3 -B freeze_sequences.py publication "$audit"
tar -czf publication.tgz -C publication .
sha256sum publication.tgz > publication.tgz.sha256
date -Is
