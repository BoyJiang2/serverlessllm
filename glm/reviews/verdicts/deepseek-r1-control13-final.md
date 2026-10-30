## 判定

**R1：`changes_required`。** 当前不能签发 R1 通过；也不是 `blocked`，因为多数问题可在 R1 内修正后复审。**R2 / 正式计时完整 gate 未请求、未审阅、未通过。** 本次只读静态审阅，未执行远端命令、未修改文件、未派遣子代理。

本判定绑定 `control-input-manifest.json` 的 SHA256：

`d1136da321fd635d606dda7714e469ab55b450c6f873455f4599758935d079ba`

我在无 shell 条件下不能独立重算全部文件 SHA；以下哈希矛盾均来自本目录清单、审计文件、sidecar 与实际文件内容的交叉比对，执行前必须用 `sha256sum` 复核。

---

## 一、关键正向结论

1. **Ray readiness/IP 修复在静态代码中有实质内容**  
   `ray_control13.py:40-59` 等待 `"Ray runtime started."` 后才 `ray.init`，传入 `_node_ip_address=ip`，并断言 driver NodeID 等于自身 worker 节点、GPU=8、两个不同 NodeManagerAddress。`bootstrap_control13.py:76-88` 也做了同类检查。这个修复方向是成立的，但它只在该条件下有效，未执行前不能算通过；旧 `exit137` 也不能由此解释。  
   残余点：60 秒 marker 等待是运行条件；`node_ip_address.json` 只被记录、未被断言内容。

2. **静态路径未发现正式 ID 消耗**  
   `service13a-template.json` 没有设置 `SLLM_FORMAL_GATE_FILE`；`service_model_validation.py:68` 只写 `A4-cycle...` 请求 ID，`:121` 明确 `no B attempt consumed`。  
   但 `bootstrap_control13.py:20-26,109-112` 仍保留 formal 分支；必须由运行环境断言 `SLLM_FORMAL_GATE_FILE` 为空，否则存在误入正式 trial 的路径。

3. **硬 deadline 的静态算术基本成立**  
   `resource-window-extension-v3.json:33` 为 `2026-10-26T02:50:00+08:00`，即 `2026-10-25 18:50 UTC`；`install_guard_v6.sh:9,16`、`install_observers_v12.sh:9`、`resource_window_v3.py:21-22` 均对齐 `1792954200`。`bootstrap_control13.py:70-73` 要求启动时至少剩 3600 秒以上，alarm 2400 秒，`supervise_control13.py:6` 外层 2500 秒。数学上在硬 deadline 内；实际池初始化 + 两轮生命周期是否装得下 2400 秒仍是运行条件。

---

## 二、阻断性/高风险不一致

### F01 父资产 manifest 哈希与包内唯一父证据不一致（critical，阻断启动）

**位置**  
- `run-manifest.json:1470`：`parent_manifest_sha256 = 93c05a38ffbc6d8254ed47854abdab4d3ba549c2db8b3fcb480599a91679c60d`
- `launch_control13.py:16`：`assert sha256(asset_root/run-manifest.json) == 93c05a...`
- `control-input-manifest.json:83`：`evidence/run-manifest.json` 的 SHA 为 `e38e31e7...`
- `evidence/handoff-current-audit-v2.json:3890-3892`：父资产 `run-manifest.json` 的实际 SHA 为 `e38e31e7...`

**证据**  
包内唯一父 manifest 是 `evidence/run-manifest.json`，其哈希为 `e38e31e7...`；审计也记录 `e38e31e7...`。新 manifest 却声明父哈希为 `93c05a38...`，包内没有任何文件哈希为 `93c05a38...`。

**影响**  
- 若远端父 manifest 仍是审计时的 `e38e31e7...`，`launch_control13.py:16` 会在任何 Docker create 之前失败，G1 无法开始。
- 若远端父 manifest 确为 `93c05a38...`，则它与审计/包内父证据不是同一字节，父资产的冻结性和历史证据绑定失效。
- 这是 R1 的阻断性证据缺口，不是运行期“可能”。

**验证方法**  
执行前运行：
```bash
sha256sum /data/brick1/boyjiang/sllm-glm51fp8-20261024T051606Z-deb76e57/run-manifest.json
```
与 `93c05a38...`、`e38e31e7...` 对比；同时检查换行/编码差异。必须把实际父文件的字节级哈希、规范化规则和包内副本同步。

---

### F02 父资产以读写方式挂载，可能改写历史 checkpoint、Store、native-home、gold 和旧日志（critical）

**位置**  
- `launch_control13.py:42-50`：`--mount=type=bind,src=asset_root,dst=/experiment` 没有 `readonly`
- `launch_control13.py:47`：父资产根整树读写挂载
- `headray13a.json:8-25`、`headray13b.json:8-25`、`headservice13a.json:8-25`：`HOME=/experiment/native-home`，`HF_HOME`、`TORCHINDUCTOR_CACHE_DIR`、`TRITON_CACHE_DIR`、`VLLM_CACHE_ROOT`、`CUDA_CACHE_PATH` 全部指向父资产
- `service13a-template.json:21`：`STORAGE_PATH=/experiment/store`
- `bootstrap_control13.py:37-39,91-92`：以 `/experiment/store` 启动 Store

**证据**  
父资产根中包括历史 checkpoint、Store v7、native-home 编译缓存、native gold、旧日志和旧 controls。挂载为 rw 后，容器内 UID 2147 对这些路径有写权限。`compilation_guard.py:11-22` 虽有 `artifact_snapshot`，但 `bootstrap_control13.py` 和 `launch_control13.py` 从未调用它；没有运行前后缓存哈希。

**影响**  
- 违反用户要求“不修改历史冻结文件”和“历史文件保持不变”。
- Store 初始化/服务加载可能改写历史 Store 元数据；native-home 会被 JIT 缓存写入；gold/旧日志可被意外覆盖。
- 一旦父资产被写，F01 的父 manifest 哈希和 1142 文件审计都不再成立，G1 证据链失效。
- 这与“新 run 只复用资产、不写旧身份”直接冲突。

**验证方法**  
启动前：
```bash
docker inspect <container> --format '{{json .Mounts}}'   # 确认 /experiment 的 RW
find /experiment -maxdepth 2 -writable
sha256sum /experiment/native-gold.json /experiment/store-restore-validation.json
stat -c '%U %G %a %n' /experiment /experiment/store /experiment/native-home
```
修正应把父资产根设为 `readonly`，仅把新 control 目录作为 rw；Store/cache 需要写入时，必须使用新目录或 overlay，并在运行前后记录哈希。

---

### F03 清理脚本没有绑定自身 run_id，且对 2147 可执行，理论上可终止其他同 UID 任务（critical）

**位置**  
- `cleanup_v6.sh:3-10`：接受任意匹配正则的 `$1`，据此计算 `base` 和 `target`
- `cleanup_v6.sh:13-20`：按 `sllm.run_id=$target` 停容器，再 `systemctl kill/stop "$target.slice"`
- `install_guard_v6.sh:14-16`：安装为 `0555`、root:root，并让 timer 调用
- `cleanup_v6.sh:15`：只检查每个容器的 `sllm.uid=2147`

**证据**  
脚本没有把安装时的 run_id 写死，也没有校验 `$1 == 安装目录名`。任何能执行 `/run/<run_id>/cleanup-v6.sh` 的 2147 用户，都可以传入另一个已知 run_id（包括历史/正式/其他控制 run），只要目标容器标签为 `sllm.uid=2147`，脚本就会尝试停容器并杀对应 slice。`0555` 使它对所有用户可执行，不是 root-only。

**影响**  
- 违反“只清理本实验身份登记下的资源”“不终止其他作业”。
- 由于其他正式 attempt 也可能使用 UID 2147，存在误停正式 ID 资源的路径。
- `systemctl kill` 若无权限会 `|| true`，但 `docker stop` 仍可能成功；不能依赖 systemctl 失败来保护。

**验证方法**  
静态检查 `/run/<run_id>/cleanup-v6.sh` 的 owner/mode 与内容；不要实际用其他 run_id 执行。修正应：
- 脚本内嵌自身 run_id 并拒绝其他值；
- 改为 `0700 root:root` 或仅 timer/root 可执行；
- 校验目标 slice 的 `ControlGroup`/owner；
- 对 `docker stop` 只允许当前 run 的 label，并保留 per-container 失败不中断 slice 清理。

附加可靠性问题：`cleanup_v6.sh:14-20` 在 `set -e` 下，单个异常 label 的 `test` 失败会直接退出，导致后面的 `systemctl kill/stop` 不执行，留下资源。

---

### F04 启动器只验证 timer “active + 下一次时间”，没有验证清理脚本、ExecStart、slice 限额和 boot_id（high）

**位置**  
- `launch_control13.py:36-39`：只检查 `systemctl is-active` 和 `NextElapseUSecRealtime`
- `install_guard_v6.sh:27-28`：`systemctl show` 只打印 `MemoryMax/MemorySwapMax/TasksMax`，没有断言
- `install_guard_v6.sh:42-43`：只打印 cleanup 脚本 SHA，没有在启动器复核
- `cleanup_v6.sh:11`：日志写入 `$base`（用户 2147 可写目录）
- `install_guard_v6.sh:11-16`：cleanup 脚本和 transient timer 放在 `/run`，重启后丢失

**证据**  
一个 timer 可以在正确时间触发，但 `ExecStart` 指向错误脚本；一个 slice 可以没有 `MemoryMax=1700G`、`MemorySwapMax=0`、`TasksMax=8192`；`/run/<run_id>/cleanup-v6.sh` 可以被替换（若权限配置错误）或缺失。启动器都不会发现。`guard_state` 只有两行，没有 unit 名、run_id、ExecStart、限额、脚本哈希。

**影响**  
- 硬 deadline 守护和内存隔离可能只是“看起来存在”。
- 若 slice 限额未生效，768GB Store + 模型 + 页缓存可能触发主机级 OOM，而不是受控 cgroup OOM。
- 若 timer 的 ExecStart 错误，deadline 到达时不会清理。
- 清理日志在用户可写目录，证据可被删除/替换。

**验证方法**  
启动前执行并断言：
```bash
systemctl show <run_id>-deadline-v6.timer -p ActiveState -p NextElapseUSecRealtime -p ExecStart
systemctl show <run_id>.slice -p MemoryMax -p MemorySwapMax -p TasksMax -p ControlGroup
stat -c '%U %G %a %n' /run/<run_id>/cleanup-v6.sh
sha256sum /run/<run_id>/cleanup-v6.sh
cat /proc/sys/kernel/random/boot_id
```
把期望值写进 `launch_control13.py` 的 fail-closed 断言；清理日志改到 root-only 目录。另需规定：`/run` transient guard 不跨重启，启动前必须核对 boot_id，重启后重新安装并重新验证。

---

### F05 预约窗口授权链不足：第二个 booking 明写 Kimi 用途，且代码不检查窗口开始时间（high）

**位置**  
- `resource-window-extension-v3.json:6-8`：`overlay_created_utc`、`human_authorization` 为自由文本
- `resource-window-extension-v3.json:10-31`：两个 booking；第二个 `:27` 的 `purpose` 是 **“fast-launch portable Kimi revalidation, full-cycle verification and owned-resource release; backup window”**
- `resource-window-extension-v3.json:33`：cleanup deadline 02:50
- `resource_window_v3.py:17-23`：只校验 booking 顺序、`user=azure`、结束时间连续、deadline=最后 end-600；**没有校验 `now >= 第一个 start_ms`，没有校验 purpose，没有绑定 `ssh_user/UID`**

**证据**  
`resource-window` 明确把第二段 23:00–03:00 描述为 Kimi 备份窗口，而本实验是 GLM-5.1 FP8 G1。`human_authorization` 只说“用户让 Codex 接手并继续”，没有明确授权把 Kimi booking 用于 GLM。`launch_control13.py:31` 只检查 `now < deadline-120`，因此如果误在 20:00 前启动，也会通过；`install_guard_v6.sh:9` 同样只检查“尚未到 deadline”。

**影响**  
- 可能占用另一个任务（Kimi revalidation）的备份窗口，违反“不干扰其他任务/不终止其他作业”。
- 在窗口开始前启动时，代码不会拒绝，属于未授权资源使用。
- `booking user=azure` 与运行 `ssh_user=boyjiang/UID2147` 的授权链仅由自由文本连接，不能独立核验。

**验证方法**  
- 要求用户明确书面确认第二 booking 可用于本 GLM G1，或把窗口限制为第一 booking（20:00–23:00）。
- 在 `resource_window_v3.py` 增加：
```python
assert now_ms >= min(row['start_ms'] for row in rows)
assert all(row.get('purpose') is None or GLM_PURPOSE in row['purpose'] for row in rows)
```
- 将 `ssh_user/uid` 与 booking 的授权记录绑定，而不是只断言 `azure`。

---

### F06 新 run 没有在启动前强制校验物理节点、machine-id、GPU UUID（high）

**位置**  
- `run-manifest.json:3`：`host=qjhs3`；`:6`：`resource_id=h20/qjhs003`
- `resource_window_v3.py:13`：只断言 `window['host'] == original['host'] == 'qjhs3'`
- `launch_control13.py:13-31`：只校验父 manifest、control manifest、spec、UID、timer；没有 `hostname`、`/etc/machine-id`、GPU UUID 校验
- `CONTROL-IDENTITY.json`：只有 run_id、parent root、deadline、formal_attempts_consumed；没有 host、machine-id、GPU UUID
- `evidence/handoff-current-audit-v2.json:3-14`：有 `ssh_alias=qjhs003`、`hostname=qjhs3`、machine_id、8 个 GPU UUID，但这些证据没有被新 run 的启动器绑定

**影响**  
- 用户要求“hostname/IP 相同不足以证明同一物理节点，应结合 GPU UUID、机器身份和资产记录”。当前启动器只信任 `run-manifest` 里的 `qjhs3`，没有把 handoff 审计中的 machine-id/GPU UUID 变成 fail-closed 门。
- 若 `qjhs003` 别名指向其他节点，新 run 仍可能启动并消耗该节点资源；也可能在没有历史资产的节点上运行。
- 这是“身份隔离”核心缺口。

**验证方法**  
在 `launch_control13.py` 的最前面增加：
```python
assert socket.gethostname() == APPROVED_HOSTNAME
assert open('/etc/machine-id').read().strip() == APPROVED_MACHINE_ID
assert set(nvidia_smi_uuids()) == APPROVED_GPU_UUIDS
```
并把当前 boot_id、machine-id、GPU UUID 写入 dispatch/result；若与审计不一致，在 Docker dispatch 前退出。启动器对 `qjhs003` 的 SSH 别名和 `qjhs3` 的 hostname 关系也要有显式记录。

---

### F07 Store 就绪只等于 TCP 8073 可连接；固定端口可能被旧 Store/sllm 占用（high）

**位置**  
- `bootstrap_control13.py:91-98`：启动 Store 后，`store_ready()` 只做 `socket.create_connection(('127.0.0.1',8073))`，随后 audit 线程亲和性
- `bootstrap_control13.py:99-103`：sllm 健康只做 `http://127.0.0.1:8343/health`
- `bootstrap_control13.py:91-92`：Store 绑定 `0.0.0.0:8073`
- `service13a-template.json:21`：使用父资产 `/experiment/store`
- `service_model_validation.py:15`：只读旧 `store-restore-validation.json` 的 status，不校验当前 pool

**证据**  
`wait_ready` 先检查子进程是否退出，再调用 `store_ready()`。如果旧的 Store 仍监听 8073，新 Store 子进程可能尚未退出，`store_ready()` 就会被旧进程满足；随后 `audit_store_threads(store.pid, target)` 只检查新进程的线程亲和性，不证明 8073 属于新进程。sllm 8343 同理。Store 的 pool-ready 没有任何证据；只有 TCP 端口。

**影响**  
- 可能把旧 Store/sllm 当作新 run 的就绪服务，G1 实际在新身份下运行旧进程/旧日志/旧 pool。
- 即使没有旧进程，端口先于 768GB pool 初始化打开，G1 也可能在 pool 未真正分配时继续；`store-restore-validation` 是历史文件，不是当前 pool 证明。
- `service13a-template` 的 `placement_config.memory_pool=[]`（`service_model_validation.py:35-36`）意味着服务路径实际走 local_disk，并不验证 pool 命中；这与“真实 Store 池初始化”不是同一件事，最终结论不能写成 pool-hit。

**验证方法**  
- 用 `ss -ltnp` 或 `/proc/<child>/fd` 证明 8073/8343 的 listener 属于当前子进程 PID/inode。
- 读取 Store 日志或状态接口，记录实际 `pool_bytes`、pool-ready 时间、失败/回退；不能只看端口。
- 为 run 使用唯一端口或独立网络命名空间；Store 至少绑定 `127.0.0.1`。
- 在 `bootstrap` 中写入 `pool_ready`、`pool_bytes`、`pool_pid`，并在 `service_model_validation` 前断言。

---

### F08 包内“冻结证据”与实际运行时父资产不是同一字节；gold/store 校验链未绑定（high）

**位置**  
- `control-input-manifest.json:63`：`evidence/native-gold.json` SHA `7e7d8089...`
- `evidence/native-gold.sha256:1`：内容 `9a2db690...`
- `evidence/handoff-current-audit-v2.json:3245-3247`：父资产 `native-gold.json` 实际 SHA `9a2db690...`
- `control-input-manifest.json:88`：`evidence/store-restore-validation.json` SHA `02430904...`
- `evidence/handoff-current-audit-v2.json:5440-5442`：父资产 `store-restore-validation.json` 实际 SHA `e400fb42...`
- `service_model_validation.py:23-24`：运行时读 `/experiment/native-gold.json` 和 `/experiment/native-gold.sha256`
- `bootstrap_control13.py:30-32`：运行时读 `/experiment/store-restore-validation.json` 的 status

**证据**  
包内 gold 文件哈希 `7e7d...`，但其 sidecar 写的是 `9a2d...`；审计中父 gold 也是 `9a2d...`。包内 store-restore 是 `0243...`，审计中父文件是 `e400...`。这说明包内副本与运行时父资产不是同一字节；`service_model_validation` 在容器内读的是父资产，不是包内 evidence 副本。

**影响**  
- 审阅者看到的 gold 不是运行时 gold；sidecar 甚至不能验证包内副本。
- 运行时 gold 的 drift 由父资产 sidecar 检查，但该 sidecar 不在控制清单中；`store-restore` 只有 status 文本，没有被哈希绑定。
- 这使“G0 原始证据 / 冻结金标”不可严格审计。

**验证方法**  
- 对父资产和包内副本分别 `sha256sum`，确认差异是换行/编码还是内容。
- 若只是规范化，记录 canonicalization；否则重新复制字节级一致的文件。
- 把运行时实际使用的 `native-gold.json`、`native-gold.sha256`、`store-restore-validation.json` 哈希加入 control manifest，或在启动器断言。

---

## 三、验收/监控/统计缺口

### F09 最终释放 60 秒证据没有实现；cleanup 不判定“已清空”（high）

**位置**  
- `service_model_validation.py:100-115`：每个 cycle 删除后 60 秒采样，但仍在运行中的服务容器内；不是 head/worker/monitor 全部停止后的最终释放
- `bootstrap_control13.py:117-127`：finally 终止进程、`ray stop`，没有 60 秒释放观察
- `ray_control13.py:76-90`：finally 停止 worker，没有 head 停止/释放观察
- `supervise_control13.py:9-10`：子进程返回即写 `passed including cleanup`，没有独立释放证据
- `cleanup_v6.sh:21-23`：打印 remaining containers，但不 `test` 为空；`systemctl stop` 有 `|| true`

**证据**  
PLAN 第 31 行要求“deletion observations and final stopped-workload release60s evidence”。代码只有模型删除后的 60 秒，且只覆盖服务容器 cgroup，不覆盖 head、worker、Store、observer 全部停止后的 GPU/cgroup 释放。cleanup 只打印剩余容器，不以非零退出表示失败。

**影响**  
- 即使 head/worker 停止后 GPU 显存、cgroup 进程、Store pool 仍残留，当前脚本仍可能报告通过。
- 最终释放证据缺失，G1 成功标准不完整。

**验证方法**  
增加独立 release 脚本：停止 head/worker/monitor/observer 后，连续 60 秒采样每卡 NVML 显存、GPU 进程、cgroup `pids.current`、`memory.current`、目标容器列表；断言：
```bash
test -z "$(docker ps -q --filter label=sllm.run_id=$run_id)"
test "$(cat /sys/fs/cgroup/.../$run_id.slice/pids.current)" = 0
```
并把结果写入新 control 目录。

---

### F10 监控连续性没有自动判定；启动器不等待 monitor ready；host 扫描超出“仅本 cgroup 详情”（medium）

**位置**  
- `monitor_control13.py:25-72`：1 秒循环写 JSONL，但没有 gap 检查、没有 ready 租约过期检查
- `monitor_control13.py:67-72`：写 `monitor-v3-ready.json`，但 `launch_control13.py` 没有任何消费者
- `launch_control13.py`：启动 head 前不检查 monitor ready 或 observer active
- `monitor_control13.py:76-84`：host-wide `/proc` 扫描，记录其他 PID/name 到 `interference-v3.jsonl`
- `install_observers_v12.sh:11-12`：只有 kernel 和 docker events observer，没有 service log observer；kernel 日志写入 `$base` 用户可读目录

**证据**  
PLAN 要求“continuous monitor (no unexplained >5s sample gaps)”和“启动即采集”。代码只采样，不断言；启动器也没有把 monitor ready 作为 head 的前置条件。host 扫描虽然只记 name/PID，但 PLAN 第 21 行说“only this run's cgroup process details are collected”，实际记录了他进程名。

**影响**  
- 无法自动证明无 >5s 断档；monitor 可能晚于 head 启动。
- 可能泄露其他任务进程名/PID 到新 run 的日志。
- 服务日志观察缺失，异常归因证据不完整。

**验证方法**  
- 在 head launch 前断言 `monitor-v3-ready.json` 存在、`last_sample_ns` 新鲜、observer unit active。
- 用 JSONL 的 `monotonic_ns` 做 gap 检查，>5s 即 fail。
- host 扫描仅保留 GPU ownership 检查所需的 PID，其他用户进程不写名字；或明确记录为“host-level coverage gap”。
- kernel/docker 日志文件改为 root-only，或加 0600。

---

### F11 八 rank 权重审计漏掉 234 个 runtime index 张量的比较（medium）

**位置**  
- `weight_contract.py:5-13`：把 `attribute:*.topk_indices_buffer` 放入 `runtime_indices`，只断言数量 234、dtype/shape/bytes
- `service_model_validation.py:88-92`：只比较 `actual['tensors'] == reference['tensors']` 和 `kv_runtime_excluded`，**没有比较 `actual['runtime_indices']` 与 `reference['runtime_indices']`**

**证据**  
`weight_state` 返回 `runtime_indices`，但 validator 不使用。PLAN 要求“complete eight-rank audit”并“known workspace/KV exclusions explicitly identified”。当前只识别 exclusion，不证明 runtime index 值一致。

**影响**  
- 234 个 topk indices buffer 可在审计通过时不同。
- 若这些是纯 runtime workspace，应该明确记录“不比较”的理由和哈希；当前没有。

**验证方法**  
- 比较 `actual['runtime_indices'] == reference['runtime_indices']`，或记录其哈希并说明排除理由。
- 在 `model-result.json` 中写入 `runtime_indices_count`、`runtime_indices_sha256`、`excluded_reason`。

---

### F12 新 run manifest 仍带旧状态；旧 monitor/旧身份没有停止和计费（medium）

**位置**  
- `run-manifest.json:1462`：`"status": "A1 report delivered; full checkpoint download in progress; A2-A4 and B not passed"`
- `run-manifest.json:1472`：新 `created_utc`
- `evidence/cleanup-v6-probe.log:1`：target 是旧 run `sllm-glm51fp8-20261024T051606Z-deb76e57-guard-v6-probe`
- `evidence/guard_state:1-2`：只有 `NextElapse...` 和 `ActiveState`，没有 unit/run_id
- PLAN 第 17 行：先停 legacy head，保留旧 monitor 到新 monitor 启动；但没有“新 monitor 启动后停旧 monitor”的步骤

**证据**  
新 manifest 的 status 是旧 run 的 A1 状态，和当前“checkpoint 已完成、做 G1”矛盾。唯一 guard probe 日志是旧身份；`guard_state` 不能证明是新 run 的 timer。旧 monitor 是否仍在运行、由谁停止、成本如何计，没有代码或证据。

**影响**  
- 新身份状态被旧状态污染；审阅者可能误判 checkpoint/A4 状态。
- 旧 monitor 可能继续运行到旧 deadline，消耗资源并写旧日志；新 cleanup 只清 new slice，不会停它。
- 最终 idle baseline 若包含旧 monitor，必须列出成本，否则“最终释放”不完整。

**验证方法**  
- 更新新 manifest 的 status 为 `G1 control only; zero formal IDs; checkpoint complete; A4 not re-claimed`。
- 执行前 `docker ps`/`systemctl`/`ps` 盘点 legacy monitor；新 monitor ready 后停止旧 monitor，并记录其资源成本。
- 新 guard probe 必须用新 run_id 重新执行，不能引用旧日志。

---

### F13 安全/完整性检查主要用 `assert`，且模块在哈希校验前被 import（medium）

**位置**  
- `launch_control13.py:9`：`from resource_window_v3 import load_window` 在任何哈希校验前
- `launch_control13.py:18`：`from materialize_control13 import validate_spec` 在哈希循环 `:20-23` 前
- `launch_control13.py:14-31`：父 manifest、UID、timer、spec 全部用 `assert`
- `service_model_validation.py`：gold、token、权重比较全部用 `assert`

**证据**  
如果 `PYTHONOPTIMIZE=1` 或 `python -O`，所有 `assert` 被移除；`service_model_validation` 会在不校验 gold/权重的情况下“通过”。另外，被审查模块在哈希校验前执行，理论上可在 import 阶段绕过后续检查。

**影响**  
- 安全门和正确性门可被运行方式绕过。
- 与“派生配置在启动前验证其哈希和受审约束”不完全一致。

**验证方法**  
- 用独立的最小引导脚本先校验 `control-input-manifest` 中所有文件哈希，再 import/执行。
- 把安全/正确性 `assert` 改为显式 `if not ...: raise RuntimeError`。
- 启动前断言 `PYTHONOPTIMIZE` 为空，并记录 Python 命令行为。

---

### F14 窗口 overlay 的创建时间早于其绑定的 run manifest（low，但影响证据可信度）

**位置**  
- `resource-window-extension-v3.json:6`：`overlay_created_utc=2026-10-25T14:22:31Z`
- `run-manifest.json:1472`：`created_utc=2026-10-25T14:54:32Z`
- `resource-window-extension-v3.json:5`：`original_manifest_sha256=f49f...`（即新 run manifest 哈希）

**证据**  
overlay 声称绑定一个 14:54 才创建的 manifest 的 SHA，但自身创建时间写的是 14:22。除非 overlay 在之后被编辑却保留旧时间，或 manifest 被回填，否则时间线不一致。

**影响**  
- 不足以单独否定窗口有效性，但削弱“additive booking-derived window; never rewrites earlier authorization”的可信度。
- 结合 F05 的用途问题，应重新生成并记录时间线。

**验证方法**  
在 manifest 冻结后重新生成 overlay，记录 `overlay_created_utc > manifest.created_utc`，并保留原始快照/哈希。

---

## 四、必须核验的运行条件（当前不能视为通过）

1. **新 guard 探针未执行**：`evidence/cleanup-v6-probe.log` 是旧 run `20261024T051606Z-deb76e57`，不是本 run；`guard_state` 没有 unit 名/run_id/ExecStart/限额/脚本哈希。必须在 R1 修正后重新安装并跑 256MiB CPU-only 探针。
2. **实际 timer/slice 属性**：必须捕获 `ExecStart`、`/run/<run_id>/cleanup-v6.sh` 的 SHA/owner/mode、`MemoryMax=1700G`、`MemorySwapMax=0`、`TasksMax=8192`、`NextElapse`、`boot_id`，并在 launcher 中断言。
3. **monitor 连续性**：必须证明 `monitor-v3-ready.json` 在 head 前出现，JSONL 无 >5s 未解释断档；kernel/docker observer 实际 active。
4. **旧 monitor/旧 Store/旧 sllm**：必须盘点并停止旧身份；最终释放不能把旧 monitor 成本隐藏。
5. **Ray13a/Ray13b**：两个全新 head/worker 均需 120 秒稳定、两个不同 NodeManagerAddress、driver NodeID 属于自己 worker、8 GPU；失败即停，不重试。
6. **Store pool**：必须证明 8073 listener 属于新子进程，实际 pool bytes 达到 768799145984，pool-ready 时间已记录；不能只看端口或旧 `store-restore-validation`。
7. **service13a**：两轮完整 create/infer/delete/recreate；每轮 3 个请求匹配 frozen gold；每轮删除后 60 秒；8 rank 权重+scale 审计；runtime index 排除需显式记录。
8. **最终释放**：全部 head/worker/monitor/observer 停止后连续 60 秒 NVML/cgroup/container 证据；目标容器为空、slice `pids.current=0`、GPU 回到基线。
9. **正式 ID 隔离**：确认 `SLLM_FORMAL_GATE_FILE`、`SLLM_NORMAL_FLAGS_SMOKE` 未设置；`bootstrap` formal 分支未触发；正式 ID 账本仍为 0 消耗。
10. **节点身份**：`hostname`、`/etc/machine-id`、8 GPU UUID 与 handoff 审计一致；boot_id 未变化；不从 qjhs003 以外节点运行。
11. **时间预算**：service 启动时距 02:50 至少 3600 秒；alarm 2400/supervisor 2500 内完成 pool + 两轮；否则 fail closed，不延长窗口。
12. **资产只读**：父资产挂载改为 `readonly`；native-home/Store/cache 写入重定向到新目录或 overlay；运行前后哈希。
13. **窗口授权**：第二 booking 的 Kimi 用途必须获得明确 GLM 授权，或只用第一 booking；窗口开始时间必须断言。
14. **父 manifest 哈希**：`93c05a38...` 与 `e38e31e7...` 必须先和解；否则不要启动。

---

## 五、局限

- 本次无 shell，不能独立重算全部文件 SHA；`control-input-manifest` 的 SHA 使用调用提示给定的 `d1136...`，文件级哈希矛盾来自清单/审计/内容交叉比对，需执行前复核。
- `handoff-current-audit-v2.json` 7,097 行，仅分段读取了 GPU/机器身份、`run-manifest`、`native-gold`、`store-restore` 等关键段，未逐行复算 1,142 文件。
- 未审阅父资产中的 `cache_prepare_v8.py`、`run_store_server.py`、`formal_trials_v11.py`、vLLM/SLLM 源码和 `native_repeat7/runtime-rank*.json`；这些文件不在本目录，只有父 manifest/审计哈希。
- 未执行 Ray13a/13b、未安装新 guard、未跑 monitor、未跑 service13a、未做最终释放；所有运行条件均未通过。
- R2 的 FP8 正确性、缓存条件、正式计时/统计/完整 gate 未审阅。`service_model_validation.py:30` 的 `torch_dtype='bfloat16'`、`:35-36` 的 `memory_pool=[]`、以及正式统计协议属于 R2 范围，不能由本次 R1 通过。
- 未访问 qjhs003 或其他节点，未读取账号/凭据，未运行远端命令，未修改本目录文件。

---

## 六、机器可读 JSON

```json
{
  "verdict": "changes_required",
  "stage": "R1",
  "scope": "G1 untimed controls only; no R2/formal approval",
  "manifest_sha256": "d1136da321fd635d606dda7714e469ab55b450c6f873455f4599758935d079ba",
  "r2_status": "not_requested; not_approved",
  "blocking_findings": [
    "R1-F01",
    "R1-F02",
    "R1-F03"
  ],
  "reviewed_files": [
    "USER-OBJECTIVE.md",
    "PLAN.md",
    "REVIEW-PROMPT.md",
    "control-input-manifest.json",
    "cleanup_v6.sh",
    "install_guard_v6.sh",
    "install_observers_v12.sh",
    "resource_window_v3.py",
    "launch_control13.py",
    "launch_control13.py.diff",
    "materialize_control13.py",
    "bootstrap_control13.py",
    "bootstrap_control13.py.diff",
    "ray_control13.py",
    "ray_control13.py.diff",
    "supervise_control13.py",
    "monitor_control13.py",
    "monitor_control13.py.diff",
    "monitor13-template.json",
    "run-manifest.json",
    "resource-window-extension-v3.json",
    "headray13a.json",
    "headray13b.json",
    "headservice13a.json",
    "ray13a-template.json",
    "ray13b-template.json",
    "service13a-template.json",
    "service_model_validation.py",
    "store_numa_exec_v10.py",
    "weight_contract.py",
    "compilation_guard.py",
    "CONTROL-IDENTITY.json",
    "dsh-readonly-review.patch.yml",
    "prepare_control13.py",
    "evidence/cleanup-v6-probe.log",
    "evidence/guard_state",
    "evidence/legacy_control_state",
    "evidence/ray-only-rayonly12__result.json",
    "evidence/ray-only12-supervisor-result.json",
    "evidence/store-restore-validation.json",
    "evidence/native-gold.json",
    "evidence/native-gold.sha256",
    "evidence/measurement-preregistration.json",
    "evidence/run-manifest.json",
    "evidence/handoff-current-audit-v2.json"
  ],
  "findings": [
    {
      "id": "R1-F01",
      "severity": "critical",
      "status": "changes_required",
      "title": "父资产 manifest 哈希与包内唯一父证据不一致，启动器会在 Docker dispatch 前失败或失去证据绑定",
      "location": "run-manifest.json:1470; launch_control13.py:16; control-input-manifest.json:83; evidence/handoff-current-audit-v2.json:3890-3892",
      "evidence": "新 manifest 声明父哈希 93c05a38...；包内 evidence/run-manifest.json 的哈希和审计 actual 均为 e38e31e7...；包内没有任何文件哈希为 93c05a38...。",
      "impact": "若远端父文件仍为 e38e31e7...，launch_control13.py:16 的 assert 直接失败，G1 无法启动。若远端确为 93c05a38...，则实际父文件不是审计/包内父证据，父资产冻结性和 1142 文件审计失效。",
      "verification": "执行前 sha256sum 父资产 run-manifest.json，与 93c05a38... 和 e38e31e7... 对比；检查换行/编码；把实际父文件字节级哈希同步到包内并重新冻结。"
    },
    {
      "id": "R1-F02",
      "severity": "critical",
      "status": "changes_required",
      "title": "父资产整树以 rw 挂载，历史 checkpoint、Store、native-home、gold 和旧日志可被新 run 改写",
      "location": "launch_control13.py:42-50,47; headray13a.json:8-25; headray13b.json:8-25; headservice13a.json:8-25; service13a-template.json:21; bootstrap_control13.py:37-39,91-92",
      "evidence": "父资产根绑定到 /experiment 没有 readonly；HOME/HF_HOME/inductor/triton/vllm/cuda 缓存全部指向父资产 native-home；STORAGE_PATH 指向父资产 store。compilation_guard.py:11-22 的 artifact_snapshot 从未被 bootstrap/launch 调用，没有前后哈希。",
      "impact": "违反不修改历史冻结文件；Store 元数据、JIT 缓存、gold、旧日志可能被写；一旦父资产改变，父 manifest 哈希和 G0 证据链失效。",
      "verification": "docker inspect 查看 /experiment 的 RW；find /experiment -writable；运行前后 sha256sum native-gold、store-restore、native-home 关键缓存；改为父资产 readonly，新 control 目录/overlay 作为唯一 rw。"
    },
    {
      "id": "R1-F03",
      "severity": "critical",
      "status": "changes_required",
      "title": "cleanup 脚本未绑定自身 run_id 且 0555 可执行，可能停掉其他 UID2147 任务；异常 label 还会中断 slice 清理",
      "location": "cleanup_v6.sh:3-10,13-20,21-23; install_guard_v6.sh:14-16",
      "evidence": "脚本接受任意匹配正则的 $1，并据此停 label=sllm.run_id=$target 的容器和 $target.slice；只校验容器 sllm.uid=2147；安装为 0555 root:root。set -e 下单个 docker inspect/test 失败会在 systemctl kill/stop 前退出。",
      "impact": "任何可执行该脚本的 2147 用户可传入其他 run_id，误停历史/正式/其他控制任务；违反不终止其他作业。异常容器 label 可导致 slice 清理被跳过。",
      "verification": "检查 /run/<run_id>/cleanup-v6.sh owner/mode；不要用其他 run_id 实际执行；修正为内嵌 run_id、0700 root-only、校验目标 slice/ControlGroup，并让 per-container 失败不阻断 slice kill。"
    },
    {
      "id": "R1-F04",
      "severity": "high",
      "status": "changes_required",
      "title": "启动器只检查 timer active/next，不验证 ExecStart、cleanup 脚本哈希、owner/mode、MemoryMax/SwapMax/TasksMax 和 boot_id",
      "location": "launch_control13.py:36-39; install_guard_v6.sh:9,14-16,27-28,42-43; cleanup_v6.sh:11; evidence/guard_state:1-2",
      "evidence": "install_guard 只打印 systemctl show 和 sha256，没有 assert；launch 只检查 is-active 和 NextElapse；guard_state 没有 unit 名/run_id/ExecStart/限额。cleanup 日志写入用户可写 base。",
      "impact": "timer 时间正确但命令错误、slice 无内存上限、脚本缺失或被替换时仍可能通过；deadline 守护和 1700G 内存隔离可能是假的；root 清理证据可被用户删除。",
      "verification": "启动前 systemctl show ExecStart/ActiveState/NextElapse、systemctl show slice MemoryMax/MemorySwapMax/TasksMax、stat/sha256sum /run/<run_id>/cleanup-v6.sh、cat boot_id；把这些写成 launcher 的 fail-closed 断言；清理日志移到 root-only 目录。"
    },
    {
      "id": "R1-F05",
      "severity": "high",
      "status": "changes_required",
      "title": "第二 booking 明写 Kimi 用途；代码不检查窗口开始时间，也不绑定 ssh_user/UID 授权",
      "location": "resource-window-extension-v3.json:6-8,10-31,27,33; resource_window_v3.py:17-23; launch_control13.py:31; install_guard_v6.sh:9",
      "evidence": "第二 booking purpose 为 fast-launch portable Kimi revalidation; backup window；human_authorization 只是自由文本；load_window 只校验 user=azure、结束时间连续、deadline=end-600，没有 now>=min(start_ms)，没有 purpose，没有 ssh_user/UID 绑定。",
      "impact": "可能占用 Kimi 备份窗口，干扰其他任务；在 20:00 前启动也不会被代码拒绝；azure booking 到 boyjiang/UID2147 的授权链不可独立核验。",
      "verification": "要求明确授权第二 booking 用于 GLM，或只用第一 booking；在 load_window 增加 start_ms/purpose/ssh_user 校验；在 launcher 记录当前 epoch 与 booking start。"
    },
    {
      "id": "R1-F06",
      "severity": "high",
      "status": "changes_required",
      "title": "新 run 没有在 Docker dispatch 前强制校验 hostname、machine-id、GPU UUID；qjhs3/qjhs003 只靠 manifest 文本",
      "location": "run-manifest.json:3,6; resource_window_v3.py:13; launch_control13.py:13-31; CONTROL-IDENTITY.json; evidence/handoff-current-audit-v2.json:3-14",
      "evidence": "run-manifest host=qjhs3，resource_id=h20/qjhs003；launch 没有 hostname/machine-id/GPU UUID 检查；CONTROL-IDENTITY 没有 host/machine/GPU；handoff 审计有 machine_id 和 8 GPU UUID，但未绑定到新 run。",
      "impact": "qjhs003 别名若指向其他节点，新 run 仍可启动并消耗错误资源；无法按用户要求证明同一物理节点/同一硬件；身份隔离不成立。",
      "verification": "在 launcher 前置断言 hostname、/etc/machine-id、nvidia-smi GPU UUID 集合与审计一致；把 boot_id/machine-id/GPU UUID 写入 dispatch/result；不一致在 Docker create 前退出。"
    },
    {
      "id": "R1-F07",
      "severity": "high",
      "status": "changes_required",
      "title": "Store/sllm 就绪只看固定 TCP/HTTP 端口，旧进程可满足；没有当前 pool-ready 证据",
      "location": "bootstrap_control13.py:91-98,99-103; service13a-template.json:21; service_model_validation.py:15,35-36",
      "evidence": "store_ready 只 connect 127.0.0.1:8073；sllm health 只 GET 127.0.0.1:8343/health；Store 绑定 0.0.0.0:8073；没有 listener PID/inode 绑定，没有 pool_bytes/pool-ready；service_model_validation 只读旧 store-restore status；placement_config.memory_pool=[]。",
      "impact": "旧 Store/sllm 可被当作新服务；新 Store 可能绑定失败但端口检查通过；768GB pool 未初始化也可继续；G1 不能声称真实 pool 初始化，更不能声称 pool hit。",
      "verification": "ss -ltnp 或 /proc/<child>/fd 证明 8073/8343 属于新子进程；Store 日志/状态记录实际 pool_bytes 和 ready；断言当前 PID；Store 绑定 127.0.0.1 或专用网络；报告只写 local_disk lifecycle，不写 pool-hit。"
    },
    {
      "id": "R1-F08",
      "severity": "high",
      "status": "changes_required",
      "title": "包内 native-gold/store-restore 副本与运行时父资产不是同一字节，gold/store 校验链未绑定",
      "location": "control-input-manifest.json:63,88; evidence/native-gold.sha256:1; evidence/handoff-current-audit-v2.json:3245-3247,5440-5442; service_model_validation.py:23-24; bootstrap_control13.py:30-32",
      "evidence": "包内 native-gold 为 7e7d...，sidecar/审计父文件为 9a2d...；包内 store-restore 为 0243...，审计父文件为 e400...；运行时实际读父资产 /experiment/native-gold.json 和 /experiment/store-restore-validation.json。",
      "impact": "审阅者看到的 gold 不是运行时 gold；sidecar 无法验证包内副本；store-restore 只有 status 文本未被哈希绑定；冻结金标证据不严格。",
      "verification": "分别 sha256sum 父资产与包内副本；确认换行/编码差异；若规范化则记录规则；把运行时实际文件哈希加入 manifest 或在 launcher 断言。"
    },
    {
      "id": "R1-F09",
      "severity": "high",
      "status": "changes_required",
      "title": "最终释放 60 秒证据未实现；cleanup 不判定容器/slice 已清空",
      "location": "service_model_validation.py:100-115; bootstrap_control13.py:117-127; ray_control13.py:76-90; supervise_control13.py:9-10; cleanup_v6.sh:21-23",
      "evidence": "只有模型删除后在运行中服务容器内的 60 秒采样；head/worker/monitor/observer 全部停止后的 60 秒不存在；cleanup 只打印 remaining_target_containers，不 test 为空；systemctl stop 有 || true；supervise 在子进程返回即写 passed including cleanup。",
      "impact": "GPU 显存、cgroup 进程、Store pool 残留时仍可能报告通过；不满足 PLAN 第 31 行的 final stopped-workload release60s。",
      "verification": "增加独立 release 脚本：停止全部后连续 60 秒采样 NVML/cgroup/容器；断言目标容器为空、slice pids.current=0、GPU 回基线；cleanup 在 remaining 非空时非零退出。"
    },
    {
      "id": "R1-F10",
      "severity": "medium",
      "status": "changes_required",
      "title": "monitor 连续性无自动判定，launcher 不等待 monitor ready；host 扫描记录其他进程名，缺少 service log observer",
      "location": "monitor_control13.py:25-72,67-72,76-84; launch_control13.py 全文; install_observers_v12.sh:11-12",
      "evidence": "1 秒循环只采样不检查 gap；monitor-v3-ready.json 没有消费者；launch 启动 head 前不检查 monitor/observer；host-wide /proc 扫描把其他 PID/name 写入 interference-v3.jsonl；只启动 kernel/docker observer，没有 service log observer；kernel 日志在用户可读 base。",
      "impact": "无法证明 no unexplained >5s gaps；monitor 可能晚于 head；其他任务进程名/PID 进入本 run 日志；服务日志和权限日志覆盖不完整。",
      "verification": "head 前断言 monitor ready 新鲜且 observer active；JSONL monotonic_ns gap>5s 即 fail；host 扫描只记录 GPU ownership 所需 PID，不写名字；日志文件 0600/root-only；增加 service log observer 或明确 coverage gap。"
    },
    {
      "id": "R1-F11",
      "severity": "medium",
      "status": "changes_required",
      "title": "八 rank 审计未比较 234 个 runtime index 张量",
      "location": "weight_contract.py:5-13; service_model_validation.py:88-92",
      "evidence": "weight_state 返回 runtime_indices，但 validator 只比较 tensors 和 kv_runtime_excluded；runtime_indices 只断言数量 234、dtype/shape/bytes，不比较值或哈希。",
      "impact": "topk_indices_buffer 可在审计通过时不同；complete eight-rank audit 不完整；workspace 排除没有被显式证明。",
      "verification": "比较 actual/reference runtime_indices 或记录其 sha256；在 model-result.json 写入 runtime_indices_count/hash/excluded_reason。"
    },
    {
      "id": "R1-F12",
      "severity": "medium",
      "status": "changes_required",
      "title": "新 run manifest 仍写旧 A1 状态；旧 monitor 未停止/未计费；新 guard 探针未执行",
      "location": "run-manifest.json:1462,1472; evidence/cleanup-v6-probe.log:1; evidence/guard_state:1-2; PLAN.md:17",
      "evidence": "新 manifest status 仍为 A1 report delivered; full checkpoint download in progress; A2-A4 and B not passed；cleanup probe target 是旧 run 20261024T051606Z-deb76e57；guard_state 没有 unit/run_id；PLAN 只说要保留旧 monitor 到新 monitor 启动，没有停旧 monitor 的步骤。",
      "impact": "新身份被旧状态污染；旧 monitor 可能继续消耗资源并写旧日志；新 guard 仍未证明；最终 idle baseline/释放不完整。",
      "verification": "更新 status 为 G1 control only/zero formal IDs/checkpoint complete；盘点并停止 legacy monitor 并计成本；用新 run_id 重新跑 guard probe，记录 unit/属性/哈希。"
    },
    {
      "id": "R1-F13",
      "severity": "medium",
      "status": "changes_required",
      "title": "被审模块在哈希校验前 import；安全/正确性门主要为 assert，-O/PYTHONOPTIMIZE 可绕过",
      "location": "launch_control13.py:9,18,20-23,14-31; service_model_validation.py 全文",
      "evidence": "resource_window_v3 和 materialize_control13 在 hash 循环前 import；父 manifest/UID/timer/spec、gold/token/权重检查全部 assert。",
      "impact": "import 阶段可执行未校验代码；python -O 会移除所有 assert，可能导致未校验 gold/权重仍报告通过。",
      "verification": "先做独立最小哈希引导再 import；安全/正确性检查改显式 raise；启动前断言 PYTHONOPTIMIZE 为空；记录命令行。"
    },
    {
      "id": "R1-F14",
      "severity": "low",
      "status": "changes_required",
      "title": "窗口 overlay 创建时间早于其绑定的 run manifest",
      "location": "resource-window-extension-v3.json:5-7; run-manifest.json:1472",
      "evidence": "overlay_created_utc=2026-10-25T14:22:31Z，run manifest created_utc=2026-10-25T14:54:32Z，但 overlay 声称绑定 manifest SHA f49f...。",
      "impact": "时间线不自洽；削弱 additive window 的证据可信度，但与 F05 的用途问题叠加更严重。",
      "verification": "manifest 冻结后重新生成 overlay，记录 overlay 时间晚于 manifest；保留原始快照和哈希。"
    }
  ],
  "runtime_conditions": [
    "新 run_id 的 guard 探针尚未执行；evidence/cleanup-v6-probe.log 是旧 run，guard_state 无 unit/run_id/ExecStart/限额/脚本哈希。",
    "必须实际核验 timer ExecStart、cleanup 脚本 sha256/owner/mode、MemoryMax=1700G、MemorySwapMax=0、TasksMax=8192、NextElapse、boot_id，并在 launcher 中断言。",
    "必须在 head 前证明 monitor-v3-ready.json 新鲜、kernel/docker observer active，且 JSONL 无 >5s 未解释断档。",
    "必须盘点并停止 legacy monitor/Store/sllm，记录其成本；最终 idle baseline 不能隐藏旧身份进程。",
    "Ray13a/13b 必须各用全新 head/worker，120 秒稳定、两个不同 NodeManagerAddress、driver NodeID 属于自身 worker、8 GPU；失败即停。",
    "Store 必须证明 8073 listener 属于新子进程，实际 pool_bytes=768799145984 且 pool-ready；不能只看端口或旧 store-restore 文件。",
    "service13a 必须完成两轮 create/infer/delete/recreate、每轮 3 个 gold 请求、删除后 60 秒、8 rank 权重+scale 审计，并显式记录 runtime index 排除。",
    "全部 head/worker/monitor/observer 停止后必须连续 60 秒释放观测，目标容器为空、slice pids.current=0、GPU 回基线。",
    "必须确认 SLLM_FORMAL_GATE_FILE、SLLM_NORMAL_FLAGS_SMOKE 未设置，正式 ID 账本仍为 0 消耗。",
    "必须确认 hostname、/etc/machine-id、8 GPU UUID 与 handoff 审计一致，且 boot_id 未变化；不从 qjhs003 以外节点运行。",
    "service 启动时距 02:50 至少 3600 秒；pool+timer/两轮必须在 2400 秒 alarm 和 2500 秒 supervisor 内完成，否则 fail closed，不延长窗口。",
    "父资产必须 readonly；native-home/Store/缓存写入必须使用新目录或 overlay；运行前后记录哈希。",
    "第二 booking 的 Kimi 用途必须获得明确 GLM 授权，或只用第一 booking；窗口开始时间必须断言。",
    "父 manifest 的 93c05a38... 与 e38e31e7... 必须先和解；否则不要启动。"
  ],
  "limitations": [
    "无 shell，不能独立重算全部文件 SHA；control-input-manifest 的 SHA 使用调用提示的 d1136...；哈希矛盾来自清单/审计/内容交叉比对，执行前需 sha256sum 复核。",
    "handoff-current-audit-v2.json 共 7097 行，仅分段读取关键段，未逐行复算 1142 文件。",
    "父资产中的 cache_prepare_v8.py、run_store_server.py、formal_trials_v11.py、vLLM/SLLM 源码和 native_repeat7/runtime-rank*.json 不在本目录；仅有父 manifest/审计哈希，无法在本包内完整审阅。",
    "未执行 Ray13a/13b、未安装新 guard、未跑 monitor、未跑 service13a、未做最终释放；所有运行条件均未通过。",
    "R2 的 FP8 正确性、缓存条件、正式计时/统计/完整 gate 未审阅；service_model_validation.py:30 的 torch_dtype=bfloat16、:35-36 的 memory_pool=[] 和正式统计协议属于 R2 范围。",
    "未访问 qjhs003 或其他节点，未读取账号/凭据，未运行远端命令，未修改本目录文件。"
  ]
}
```