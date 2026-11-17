#!/usr/bin/env bash
set -euo pipefail
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-b2-sleep-repeats-20261117
old=$(cat l2-r2-handoff.pid)
while ps -p "$old" -o args= | grep -qx 'bash run-l2-batch.sh 2'; do
    sleep 15
done
grep -q ' repetition=2 qualified=3$' l2-r2-batch.log
audit=$(mktemp "$PWD/l2-audit-n2.XXXXXX.json")
python3 -B audit_sequences.py ../v11-b2-kimi-wna16-sequences-20261117/runs runs > "$audit"
python3 -B - "$audit" <<'PY'
import json, pathlib, sys
from collections import Counter
audit = json.loads(pathlib.Path(sys.argv[1]).read_bytes())
assert Counter(r['sequence'] for r in audit['rounds'] if r['passed']) == {'S1': 2, 'S2': 2, 'S3': 2}
assert all(len(r['steps']) == 7 and all(x['gold_passed'] for x in r['steps'])
           for r in audit['rounds'] if r['passed'])
PY
python3 -B export_l2_repeats.py "$PWD" "$audit" l2-publication-n2
tar -czf l2-publication-n2.tgz.pending -C l2-publication-n2 .
mv l2-publication-n2.tgz.pending l2-publication-n2.tgz
sha256sum l2-publication-n2.tgz > l2-publication-n2.tgz.sha256
printf '%s n2_evidence_ready\n' "$(date -Is)"
test ! -e l2-r3-batch.log
exec bash run-l2-batch.sh 3 >l2-r3-batch.log 2>&1
