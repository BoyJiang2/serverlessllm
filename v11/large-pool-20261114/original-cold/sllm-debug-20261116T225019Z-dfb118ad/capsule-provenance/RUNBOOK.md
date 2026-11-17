# 原版 GLM Store Cold 单臂补测

待机状态。步骤按顺序进行。先确认 Kimi 作业结束、拥有的容器停止、释放样本首末跨度至少 60 秒且八卡显存与计算进程计数均为 0。同步检查当前 qjhs003 GPU、系统 I/O、内存及 Store/Ray 状态。保持现有同节点协调锁，并让当前线程独占调度本窗口。

## 只读验证

```bash
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-glm-original-store-cold
python3 test_cpu.py
sha256sum review-approved.json historical_costs.py
systemctl is-active sllm-debug-guard.timer
```

冻结历史代码 `fb98c22540f8724f2ef298c708a5f004a8e09514`，原始模型运行镜像 `sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f`。原父审阅、代码、工具和资产哈希在审阅对象内。本次独立审阅仅覆盖单臂控制逻辑，保留原版模型、权重、Store 实现、716 GiB 池、金标、冷读准入及时间端点。

## 实际准入与启动

调度方必须在启动前用已认证的预约查询重新检查自己的独占预约。以下固定窗口仅当查询仍显示 2026-11-17 台北时间 08:00–13:00 才可使用，且测量启动时必须至少剩余 3,900 秒（11:55 之前）。节点当前合法占用仍需放行。脚本本身重新检查当前时间、节点/设备状态、root seal、boot ID、独立守卫及 GPU/host/I/O 资源，不向预约服务联网。拥有合法续约时只更新发给 seal 脚本的绝对窗口与引用。

以下命令在 qjhs003 shell 运行。设置 `booking_id` 为刚刚核对的授权记录，使用真实交互认证 `sudo -v`。校验 release 后先写入 root seal，再做由原版工具实施的 CPU 故障探针。探针还需要独立 SSH 连接再次验证。

```bash
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11-glm-original-store-cold
review=$PWD/review-approved.json
capsule=$PWD/capsule/controls/debug_stack_v1
source=/data/brick1/boyjiang/glm5.1-fp8/code/source-debug-fb98c22540f8-58d1d50ae064
run=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["proposed_attempts"][0]["run_id"])' "$review")
holder=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["proposed_attempts"][0]["holder"])' "$review")
unit=$run-controller.service
window_start=2026-11-17T08:00:00+08:00
window_end=2026-11-17T13:00:00+08:00
: "${booking_id:?先设置已通过 API 验证的预约 ID}"
set -euo pipefail
sudo -v
umask 077
auth=$(mktemp)
python3 seal_input.py "$review" "$booking_id" "$window_start" "$window_end" > "$auth"
sudo install -o root -g root -m 0444 "$auth" "/run/$run-controller-window.json"
rm -- "$auth"
args=(measure --source-tree "$source" --condition store_disk_cold --review "$review" --controller-unit "$unit")
/usr/bin/python3 "$capsule/debug_stack.py" "${args[@]}" --preflight
sudo bash "$capsule/install_debug_guard.sh" --supplement-probe-start "$run" "$holder"
```

探针启动后断开该 SSH，在探针开始后 **8–120 秒**内用新 SSH 登录，重新设置上面的变量（不重新 seal，不重新启动探针），并连续执行以下命令。验证探针后不要拖延，空 slice 仅保留 60 秒。Root unit 启动前必须先向用户告知即将使用八卡。

```bash
set -euo pipefail
sudo -v
exec 8<>/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11/active.lock
flock -n 8
/usr/bin/python3 "$capsule/debug_stack.py" "${args[@]}" --preflight
sudo bash "$capsule/install_debug_guard.sh" --supplement-probe-verify "$run" "$holder"
sudo systemd-run --unit "$run-controller" --uid=2147 --gid=2147 --slice="$run.slice"   --property=Type=exec --property=RemainAfterExit=yes   --property=Restart=no --property=KillMode=control-group --property=SendSIGKILL=yes   --property=RuntimeMaxSec=3570s --property=TimeoutStopSec=30s   /usr/bin/python3 "$capsule/debug_stack.py" "${args[@]}" --guard-probe "/run/$run/guard-verification.json"
systemctl show "$unit" -p InvocationID -p MainPID -p ActiveState -p ControlGroup
while systemctl is-active --quiet "$unit" && [ "$(systemctl show "$unit" -p MainPID --value)" != 0 ]; do sleep 5; done
```

守卫的独立 slice 定时器为 3240 秒。在本地拥有 qjhs003 SSH alias 的工作站执行已校验的历史 finalizer
`python historical_costs.py --finalize-controller <run_id>`。它通过 SSH 读取 systemd monotonic 端点并依照旧校验写
`/data/brick1/boyjiang/glm5.1-fp8/debug/<run_id>/controller-final.json`。需要从工作站运行，远端呼叫本脚本会尝试再次 SSH。
保留原始 detector、释放样本、gold、两份 cache admission 和原始失败，检查 success、结果结构和 SHA 后再转入报告。此任务仅一次原版 Store Cold。若现场准入或独立守卫拒绝，保留拒绝并停止该编号，既有结果不受影响。
