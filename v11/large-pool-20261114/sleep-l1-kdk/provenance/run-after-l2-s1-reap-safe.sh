#!/usr/bin/env bash
set -euo pipefail
base=/home/boyjiang/sllm-v4-pro/cache-competition-20261109
l2="$base/v11-b2-sleep-repeats-20261117"
run="$l2/runs/B2-WNA16-S1-r3-20261117T135519Z"
cd "$base/v11-b2-l1-kdk-20261117"
while ps -p 1101186 -o stat= | grep -q '^[^Z]'; do
    ps -p 3703243 -o stat= | grep -q '^T'
    sleep 10
done
ps -p 3703243 -o stat= | grep -q '^T'
test "$(( $(date -d '2026-11-18T07:00:00+08:00' +%s) - $(date +%s) ))" -ge 5400
python3 -B - "$run" <<'PY'
import json, pathlib, sys
root=pathlib.Path(sys.argv[1])
q=json.loads((root/'qualification.json').read_bytes())
r,=q['results']
assert q['sequence']=='S1' and q['rep']==3 and q['passed']
assert q['final_all_gpu_zero'] and q['final_release_seconds']>=60
samples=[json.loads(l) for l in (root/'final-release.jsonl').read_bytes().splitlines()]
assert samples[-1]['ns']-samples[0]['ns']>=60*10**9
assert all(s['compute_apps']==0 and all(s[f'gpu{i}']==0 for i in range(8)) for s in samples)
assert r['pid_continuity'] and r['golds_passed'] and len(r['records'])==7
for key in 'KGD':
    state=json.loads((root/f'L2/worker-{key}-final.json').read_bytes())['State']
    assert state['ExitCode']==0 and not state['Running']
print(root.name, q['final_release_seconds'], 'S1 verified before L1')
PY
date -Is
exec python3 -Bu v11_run_b2.py --sequence L1-KDK --levels 1 --rep 1 --booking-start 2026-11-17T13:00:00+08:00 --booking-end 2026-11-18T07:00:00+08:00
