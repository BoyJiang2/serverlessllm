#!/bin/bash
set -euo pipefail
run_id=sllm-glm51fp8-20261026T225201Z-02cd4cdc
test "$(id -u)" = 0
test "$1" = "$run_id"
base="/data/brick1/boyjiang/glm5.1-fp8/validation/controls/$run_id"
test "$(stat -c %u "$base")" = 2147
test "$(date -u +%s)" -ge 1793019600
test "$(date -u +%s)" -lt 1793062200
test ! -e "/run/$run_id/cleanup-v13.sh"
install -d -m 0755 -o root -g root "/run/$run_id"
test ! -L "$base/root-evidence-private13"
install -d -m 0700 -o root -g root "$base/root-evidence-private13"
install -m 0700 -o root -g root "/data/brick1/boyjiang/glm5.1-fp8/code/experiment/cleanup_guard13.sh" "/run/$run_id/cleanup-v13.sh"
cat > "/run/systemd/system/$run_id.slice" <<EOF
[Unit]
Description=Independent untimed GLM FP8 controls $run_id
[Slice]
MemoryMax=1700G
MemorySwapMax=0
TasksMax=8192
EOF
systemctl daemon-reload
systemctl start "$run_id.slice"
test "$(systemctl show "$run_id.slice" -p MemoryMax --value)" = 1825361100800
test "$(systemctl show "$run_id.slice" -p MemorySwapMax --value)" = 0
test "$(systemctl show "$run_id.slice" -p TasksMax --value)" = 8192
systemd-run --unit="$run_id-deadline-v13" --on-calendar='2026-10-27 00:50:00 UTC' --timer-property=AccuracySec=1s --property=TimeoutStartSec=300 /bin/bash "/run/$run_id/cleanup-v13.sh" "$run_id" main
test "$(systemctl is-active "$run_id-deadline-v13.timer")" = active
target="$run_id-guard-v13-probe"
docker run -d --network=none --runtime=runc --memory=256m --cpus=1 --cgroup-parent="$target.slice" --name="$target" --label="sllm.run_id=$target" --label=sllm.uid=2147 --user=2147:2147 --entrypoint=/bin/sleep aa161e380035 120
pid=$(docker inspect "$target" --format '{{.State.Pid}}')
grep -F "$target.slice/" "/proc/$pid/cgroup"
systemd-run --unit="$run_id-sentinel13" --uid=2147 --slice="$target.slice" /bin/sleep 120
sentinel_pid=$(systemctl show "$run_id-sentinel13.service" -p MainPID --value)
test "$sentinel_pid" -gt 1
grep -F "$target.slice/" "/proc/$sentinel_pid/cgroup"
systemd-run --unit="$run_id-v13-test" --on-active=3s --timer-property=AccuracySec=1ms --property=Type=oneshot --property=RemainAfterExit=yes --property=TimeoutStartSec=120 /bin/bash "/run/$run_id/cleanup-v13.sh" "$run_id" probe
sleep 18
test "$(docker inspect "$target" --format '{{.State.Running}}')" = false
test ! -d "/proc/$pid"
test ! -d "/proc/$sentinel_pid"
test "$(systemctl show "$run_id-v13-test.service" -p Result --value)" = success
test "$(systemctl show "$run_id-v13-test.service" -p ExecMainStatus --value)" = 0
test "$(systemctl show "$run_id-v13-test.service" -p SubState --value)" = exited
grep -E 'cleanup_finished .*failed=0$' "$base/root-evidence-private13/cleanup-v13-probe.log"
export CONTROL13_RUN_ID="$run_id" CONTROL13_PROBE_PID="$pid" CONTROL13_SENTINEL_PID="$sentinel_pid"
python3 - <<'PY'
import hashlib,json,os,pathlib,subprocess,sys
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'): raise RuntimeError('optimized Python is forbidden')
run=os.environ['CONTROL13_RUN_ID']
root=pathlib.Path('/run')/run
script=root/'cleanup-v13.sh'
stat=script.stat()
def call(*args): return subprocess.check_output(args,text=True).strip()
probe_result=call('systemctl','show',run+'-v13-test.service','-p','Result','--value')
probe_status=int(call('systemctl','show',run+'-v13-test.service','-p','ExecMainStatus','--value'))
probe_substate=call('systemctl','show',run+'-v13-test.service','-p','SubState','--value')
probe_group=call('systemctl','show',run+'-guard-v13-probe.slice','-p','ControlGroup','--value')
probe_empty=not probe_group or int((pathlib.Path('/sys/fs/cgroup')/probe_group.lstrip('/')/'pids.current').read_text())==0
proof=dict(run_id=run,probe_pid=int(os.environ['CONTROL13_PROBE_PID']),sentinel_pid=int(os.environ['CONTROL13_SENTINEL_PID']),
    probe_passed=probe_result=='success' and probe_status==0 and probe_substate=='exited' and probe_empty,
    probe_service_result=probe_result,probe_exec_status=probe_status,probe_substate=probe_substate,probe_slice_empty=probe_empty,
    boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    cleanup_sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
    script_stat={name:getattr(stat,name) for name in ['st_uid','st_gid','st_mode','st_ino','st_size','st_mtime_ns','st_ctime_ns']},
    service_exec=call('systemctl','show',run+'-deadline-v13.service','-p','ExecStart','--value'),
    timer_state=call('systemctl','show',run+'-deadline-v13.timer','-p','ActiveState','-p','NextElapseUSecRealtime'),
    slice=call('systemctl','show',run+'.slice','-p','MemoryMax','-p','MemorySwapMax','-p','TasksMax','-p','ControlGroup'),
    probe_log=(pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')/'validation/controls'/run/'root-evidence-private13/cleanup-v13-probe.log').read_text())
if not proof['probe_passed'] or 'failed=0' not in proof['probe_log']: raise RuntimeError('cleanup probe failed')
assert proof['cleanup_sha256']==hashlib.sha256((pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')/'code/experiment/cleanup_guard13.sh').read_bytes()).hexdigest()
record=root/'guard-verification.json'
record.write_text(json.dumps(proof,indent=2))
record.chmod(0o444)
archive=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8')/'validation/controls'/run/'root-evidence-private13/guard-verification.json'
archive.write_bytes(record.read_bytes())
archive.chmod(0o600)
for path in root.glob('cleanup-v13-*.log'): path.chmod(0o600)
print(json.dumps(proof,indent=2))
PY
sudo -k
