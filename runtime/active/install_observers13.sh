#!/bin/bash
set -euo pipefail
umask 077
run_id=sllm-glm51fp8-20261026T161004Z-817be4b6
test "$(id -u)" = 0
test "$1" = "$run_id"
base="/data/brick1/boyjiang/glm5.1-fp8/validation/controls/$run_id"
test "$(stat -c %u "$base")" = 2147
test "$(systemctl is-active "$run_id-deadline-v13.timer")" = active
remaining=$((1793040600-$(date +%s)))
test "$remaining" -gt 120
systemd-run --unit="$run_id-kernelobserver13" --slice="$run_id.slice" --property="RuntimeMaxSec=$remaining" --property=UMask=0077 --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:$base/root-evidence-private13/kernel-private.jsonl" --property="StandardError=append:$base/root-evidence-private13/kernel-error.log" /usr/bin/stdbuf -oL /usr/bin/journalctl -k --since=now --follow --output=json
systemd-run --unit="$run_id-dockerobserver13" --slice="$run_id.slice" --uid=2147 --property="RuntimeMaxSec=$remaining" --property=UMask=0077 --property=MemoryMax=256M --property=CPUQuota=100% --property="StandardOutput=append:$base/docker-events13.jsonl" --property="StandardError=append:$base/docker-events13-error.log" /usr/bin/docker events --since=0s --filter="label=sllm.run_id=$run_id" --format='{{json .}}'
systemctl is-active "$run_id-kernelobserver13.service" "$run_id-dockerobserver13.service"
export CONTROL13_RUN_ID="$run_id"
python3 - <<'PY'
import json,os,pathlib,subprocess,sys
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python forbidden')
run=os.environ['CONTROL13_RUN_ID']
rows=[]
for suffix in ['kernelobserver13','dockerobserver13']:
    unit=run+'-'+suffix+'.service'
    pid=int(subprocess.check_output(['systemctl','show',unit,'-p','MainPID','--value'],text=True))
    proc=pathlib.Path('/proc')/str(pid)
    rows.append(dict(unit=unit,pid=pid,start_ticks=proc.joinpath('stat').read_text().rpartition(')')[2].split()[19],cgroup=proc.joinpath('cgroup').read_text()))
path=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')/'validation/controls'/run/'observer-registration13.json'
with path.open('x') as stream: json.dump(dict(run_id=run,boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),observers=rows),stream,indent=2)
path.chmod(0o444)
PY
sudo -k
