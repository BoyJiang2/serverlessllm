# R1b 独立只读交叉审阅判定

**结论：`changes_required`（不批准 R1b）。**

不是 `blocked`：预约窗口、直接授权、父资产和候选包仍在，主要问题可在 R1 内修正后复审。也不是 `approved`：新 guard/probe/monitor/模型均未执行，且静态审查发现会直接导致 service13a 失败或使 guard/验收证据不成立的实质缺陷。**R2、FP8 正确性、正式计时、统计与完整 gate 未审、未通过。**

绑定：

- `code_commit`: `e1af9dd51055c2d1e4e8e3c2ea3dc0a9227f1592`
- `control-input-manifest` SHA256: `7682cfe51de10cc06a2715e8c831626a1ed1335e41ffef5364daa9da64b881f4`
- 审阅对象：`controls/candidate13b`。`reviews/r1-candidate13` 与旧 `changes_required` verdict 未作为本版审批。
- 方式：只读实际文件；未运行 shell/远端/资源控制，未读凭据，未派子代理。无 shell 不能独立重算全部 SHA；下文哈希结论来自候选清单、父 manifest、审计文件、sidecar 与实际文件内容的交叉比对。

---

## 一、绑定与哈希交叉核验

我实际读取了 `USER-OBJECTIVE.md`、`PLAN.md`、`R1-RESPONSE.md`、`control-input-manifest.json`，以及候选源码、diff、模板、测试和证据。关键交叉一致性如下：

- `run-manifest.json:1470` 的父哈希已改为 `e38e31e7...`，与 `control-input-manifest.json` 中 `evidence/run-manifest.json`、`evidence/handoff-current-audit-v2.json` 的实际父文件哈希一致。旧的 `93c05a38...` 矛盾已消除。
- `launch_control13.py:19` 硬编码的 `review-input-manifest-v11.json` 哈希 `da681f...`，与 `handoff-current-audit-v2.json:20` 的 `manifest_sha256` 一致；后者记录 `manifest_files_checked=1142`、`manifest_hash_mismatches=[]`。launcher 在 `:20-23` 重哈希全部 1,142 行。
- `native-gold.json` 为 `9a2db690...`，`native-gold.sha256` sidecar 内容也为 `9a2db690...`，父 manifest 行和审计均为同值。`store-restore-validation.json` 为 `e400fb42...`，父 manifest 行也为同值。旧的 `7e7d/0243` 不一致已消除。
- `run-manifest.json` 的 `30b9365d...` 与 `resource-window-extension-v3.json:5` 的 `original_manifest_sha256` 一致；overlay 创建时间 `15:48:45Z` 晚于 manifest 的 `created_utc 14:54:32Z`，control manifest 创建于 `15:53:10Z`。F14 的时间线矛盾已消除。
- `handoff-current-audit-v2.json` 的主机身份、`MemTotal=2,016,708,628 kB`、`MemAvailable=1,972,058,300 kB`、8 张 H20-3e 143,771 MiB GPU、`/data/brick1` 可用 `2,172,212,756,480` 字节，均与 `run-manifest.json` 的 `runtime_identity` 和候选模板一致。

**但必须准确界定：** 1,142 个父文件不包含 142 个 756GB 权重文件。权重是历史 LFS SHA-256；`checkpoint/VERIFIED.json` 记录 150 个文件尺寸、8 个 metadata 哈希和 142 个历史权重哈希，`checkpoint_scope` 明确写着“no new full 756GB hash pass yet”。因此“全部 1,142 父文件已核验”成立，但**不是**“756GB 权重已重新全量核验”。当前运行依赖的是加载后 8 rank 权重审计，该审计尚未执行，属 R2/GPU 审计范围。

---

## 二、既有 14 条 finding 逐项复核

### F01 父资产 manifest 哈希 — 已解决（事实澄清）

- **位置/证据**：`run-manifest.json:1470`；`launch_control13.py:17-23`；`control-input-manifest.json:117-120`；`handoff-current-audit-v2.json:20-22`。
- **结论**：父 manifest 为 `e38e31e7...`，与包内 `evidence/run-manifest.json`、父审计和 launcher 断言一致；1,142 行由 launcher 逐文件重哈希。旧的 `93c05a38...` 不一致已消除。
- **影响**：launcher 不会因父哈希 assert 在 Docker create 前失败；父证据链成立。**不是** 756GB 权重新哈希。
- **验证**：执行前 `sha256sum` 父 `run-manifest.json`、`review-input-manifest-v11.json`；运行 `launch_control13.py`；核对 handoff 的 1,142 项。

### F02 父资产可写 — 主体已解决，但仍有父树写入点

- **位置/证据**：`launch_control13.py:59-63` 将父资产 `readonly`，另挂新 `native-home` 和控制目录；`headray13a.json:8-25` 的 HOME/HF/inductor/triton/vllm/cuda 都指向 `/experiment/native-home`；`service13a-template.json:21` 的 `STORAGE_PATH=/experiment/store`；`bootstrap_control13.py:38-40` 使用 `/experiment/store`；`model.cpp:40-71,96,103` 只以 `O_RDONLY` 打开 Store 的 `tensor.data_*`；`prepare_host13.py:20-25` 复制 18,875,581B 的 native-home。
- **结论**：父 checkpoint/Store/gold 在容器内不可写；Store 只读；新的 JIT 缓存写入隔离到新 native-home。
- **剩余问题（N06）**：`prepare_host13.py:26` 仍创建 `asset/controls/<run_id>`，这是父资产树中的一个新目录，代码中未被任何 mount 使用；与“只有新控制目录和独立 native-home 可写”的表述不完全一致，也不是 hash-bound。
- **影响**：不会改写历史冻结文件，但会在父树留下未受控目录；若 `asset/controls` 不可写，prepare 会失败。
- **验证**：`docker inspect` 确认 `/experiment` 为 `RW=false`；`find /experiment -writable`；`sha256sum` 父 gold/store-restore；检查 `asset/controls`；建议删除或移动到新控制目录。另：copy 目标未再哈希（N14）。

### F03 清理范围 — 主体已解决，探针证明不足

- **位置/证据**：`cleanup_guard13.sh:4-34` 硬编码 run_id、要求 root、精确参数匹配、`0700` 安装、先杀自身 slice、再按 label/uid 停容器、`remaining`/`pids.current` 计入 `failed`；`install_guard13.sh:11-12,29-35` 安装 root-only 脚本并跑 256MiB CPU 探针。
- **结论**：旧 F03 的核心问题（任意 run_id 可执行、0555、异常 label 阻止 slice kill）已实质修复。
- **剩余问题（N02）**：`install_guard13.sh:44-47` 的 `probe_passed=True` 是硬编码；它只检查探针容器停止和 `/proc/$pid` 消失，**没有检查** `systemd-run` 服务的 `Result`/`ExecMainStatus`，也没有解析 `cleanup_finished ... failed=0`。`cleanup_guard13.sh:33` 的 `failed=1` 可能在探针日志中出现，但 `preflight_control13.py:32` 仍只读 `probe_passed`。
- **影响**：探针可能在“容器被 docker stop 停止、但 slice kill/pids 检查失败”的情况下仍报通过；主 run slice 的 `systemctl kill` 路径没有被直接证明。脚本主体可靠，但准入证明弱于 R1-RESPONSE 的表述。
- **验证**：`systemctl show <run_id>-v13-test.service -p Result -p ExecMainStatus` 必须为 0；解析 `cleanup_finished ... failed=0`；在探针 slice 中加一个非容器哨兵进程或验证 `pids.current=0`；再检查主 slice。

### F04 守护有效性 — 已大部分解决，证据持久化仍有缺口

- **位置/证据**：`install_guard13.sh:13-25` 写 slice 限额并断言；`:26` 创建 08:50 UTC 定时器；`:37-58` 生成 `0444` root proof，含脚本哈希/stat、boot_id、ExecStart、timer、slice 限额和探针日志；`preflight_control13.py:28-49` 校验 proof、脚本 stat/hash、ExecStart、timer、NextElapse、限额和 ControlGroup；`launch_control13.py:48-51` 再次检查 timer。
- **结论**：启动前对 guard 身份、ExecStart、限额、boot 的 fail-closed 检查已经实现，旧的“只看 active/next” 已修复。
- **剩余问题（N02/N03）**：proof 和主 cleanup 日志只在 `/run/<run_id>/`（tmpfs），没有复制到控制/证据目录；`G1-acceptance` 不绑定它们；`dispatch` 的 `preflight` 子集不含 slice 限额和 ExecStart；探针退出状态未检查。
- **影响**：`/run` 被清或重启后，完整 guard proof 和 08:50 cleanup 日志丢失；最终验收无法独立证明 guard 的限额/ExecStart/清理动作。
- **验证**：在 guard 安装后把 `/run/<run_id>/guard-verification.json`、`cleanup-v13-main.log` 以 root 只读方式归档到证据目录并记录 SHA256；`G1-acceptance` 引用这些哈希；检查 `systemctl show` 的 live 值。

### F05 窗口/授权 — 已解决（事实澄清），保留运行期刷新

- **位置/证据**：`resource_window_control13.py:14-29` 校验 original manifest hash、run/host/resource、UID/ssh_user、三个精确 booking ID、users `azure,azure,boyjiang`、resource_id、连续性、`rows[0].start<=now<rows[-1].end`、`deadline=last.end-600`、guard timer；`resource-window-extension-v3.json:10-44` 保留第二个 booking 的 Kimi 用途并新增 03:00–09:00 booking；`R1-RESPONSE.md:21-25,39-41` 记录直接人工授权；`PLAN.md:7` 明确有限窗口。
- **结论**：旧的 start-time、UID/ssh_user、purpose 问题已实质处理。用户已直接说明“继续用 Azure 也是我们的”“后面没有人 book 就继续用”，并给出 03–09 预约；这是**已有直接授权**，不需要重复确认。第二个 booking 的 Kimi 用途被透明保留，但用户授权覆盖本次 GLM 有限使用；代码未推导无限窗口、未授予终止其他作业的权力。
- **剩余运行条件**：代码使用 15:38Z 的 booking 快照，不会自动查询实时 API。启动前必须在授权范围内刷新实际 booking/occupancy，与三个冻结 row 对比并记录；若出现后续冲突 booking，不使用冲突区间，不停止其他作业。本候选 08:50 在三个连续 row 内，不需要为“09:00 后继续”做代码检查。
- **验证**：启动前 API 快照 + 与冻结 rows 对比；记录时间与哈希；不修改 purpose；不使用 09:00 后区间。

### F06 硬件/节点身份 — 已解决

- **位置/证据**：`run-manifest.json:1478-1492` 记录 hostname/machine-id/boot-id/有序 8 GPU UUID；`preflight_control13.py:17-22` 在每次 dispatch 前校验 hostname、machine-id、boot-id、GPU UUID 顺序和 UID；`deploy_control13.py:22-33` 在目标机校验 machine-id/boot-id/GPU UUID/UID；`handoff-current-audit-v2.json:2-13` 与 run-manifest 一致。
- **结论**：qjhs003 别名若指向错误节点、重启或 GPU 顺序变化，会在 Docker create 前 fail-closed。deploy 未单独检查 hostname，是小的不对称，但 launch preflight 覆盖。
- **验证**：`hostname`、`/etc/machine-id`、`/proc/sys/kernel/random/boot_id`、`nvidia-smi --query-gpu=uuid`；核对 dispatch 记录。

### F07 Store/sllm 就绪与真实 pool — 旧 TCP 风险已解决；pool-created log 断言不成立

- **位置/证据**：`bootstrap_control13.py:94-108` 用 loopback 连接、`listener(store.pid,8073)`、5s `GetServerConfig`、`mem_pool_size/chunk_size`、`VmRSS>=pool_bytes`、`pool-ready.json`；`listener_control13.py:4-18` 校验 child PID 的 socket inode；`server.py:47-49` 在 `StorageServicer.__init__` 构造 `CheckpointStore`，`:222-245` 在 `add_insecure_port`/`server.start()` 之前完成；`checkpoint_store.cpp:80-83` 构造 `PinnedMemoryPool`；`pinned_memory_pool.cpp:23-46` 以 1,432×512MiB=`716GiB` 的 `aligned_alloc` + `cudaHostRegister` 分配，失败 `LOG(FATAL)`；`GetServerConfig` 返回配置的 pool/chunk。
- **结论**：**构造顺序和源码证明成立**：pool 在 listen 之前创建；`GetServerConfig` 返回配置值；`VmRSS` 可证明实际驻留；私有网络/PID namespace 使旧容器的 127.0.0.1 不能冒充当次 Store；`service13a` 使用 `local_disk`，不声称 pool-hit，这一点诚实。
- **关键新发现（N01）**：`bootstrap_control13.py:107` 断言 `'Memory pool created with 716GB' in store.log`。该字符串来自 `checkpoint_store.cpp:83` 的 C++ glog `LOG(INFO)`；而 `store.log` 捕获的是 Store 进程的 stdout/stderr。glog 默认把 INFO 写入 glog 文件（`/tmp` 等），stderrthreshold 默认 ERROR；模板未设置 `GLOG_logtostderr`，候选包**没有包含** `sllm_store/logger.py`（父 manifest 有该文件，但未复制进审阅包），仓库中也没有任何捕获到的 `store.log` 能证明该行出现。该断言很可能在 pool 正确时仍失败；即使 glog 输出到 stderr，默认 30s 缓冲也没有 poll/retry。`server.py:40-45` 的 Python `logger.info("StorageServicer: ... mem_pool_size=...")` 是更可靠的断言对象。
- **剩余问题（N04/N10）**：`accept_control13.py:20` 只检查 `pool-ready.json` 的 `pool_bytes`，不复核 `rpc_config`、`listener`、`rss_bytes`、`store-affinity`；Store 仍绑定 `0.0.0.0:8073`。
- **影响**：service13a 可能因日志断言失败而无法通过；即使 pool 正确，G1 也会失败。accept 的“real pool proof”弱于 bootstrap 的断言。
- **验证**：运行 Store 后检查 `store.log`；包含 `logger.py`/glog 配置或设置 `GLOG_logtostderr=1`；或改为轮询 Python `StorageServicer` 行；accept 复检 `pool-ready` 全字段和 `store-affinity-ready.json` 的线程掩码。

### F08 gold/store 字节 — 已解决

- **位置/证据**：`control-input-manifest.json:88-95,313`；`evidence/native-gold.sha256:1`；`handoff-current-audit-v2.json:3298-3305,5493-5495`；`review-input-manifest-v11.json:3298-3305,5493-5495`；`validation_control13.py:26-27`；`bootstrap_control13.py:31-33`。
- **结论**：包内 gold `9a2db690...`、sidecar `9a2db690...`、父 gold `9a2db690...`；包内 store-restore `e400fb42...`、父文件 `e400fb42...`；父 manifest 钉住这些文件，launch 重哈希。旧的 7e7d/0243 不一致已消除。
- **剩余**：validation 未用 gold 的 `rank_audits[*].sha256` 复核 runtime-rank 参考文件，但父 manifest 已钉住这些文件；属低风险。
- **验证**：`sha256sum /experiment/native-gold.json /experiment/native-gold.sha256 /experiment/store-restore-validation.json`；与清单/父 manifest 对比；运行 launch。

### F09 最终释放 — 已实现独立脚本；accept 只检查长度

- **位置/证据**：`release_control13.py:4-47` 做 61 样本/60s，记录 NVML、own containers、slice `pids.current`、memory/cpu、host meminfo、processes；`:38` 要求无 GPU 进程；`:39-44` allstopped 要求零 own 容器、`pids.current=0`、每卡 `<=baseline+16MiB`；`:45-46` ray stage 只允许 monitor；`accept_control13.py:21-22` 要求三个 61 行 receipt。
- **结论**：最终释放证据已实现；`supervise_control13.py:13` 的 “passed including cleanup” 仍只是子进程返回 0 的文本，但独立 release 承担了真正证据。
- **剩余**：release 是只读脚本，不停止 head/monitor/observer，操作者必须先停；`allstopped` 的全局 `gpu_processes` 假设独占 GPU；accept 只检查行数，不复核内容。
- **验证**：每个 ray 后和全部停止后运行；检查 61 行、零 own 容器、`pids=0`、GPU 回归 baseline；在 accept 中复检关键字段。

### F10 监控 — 主要解决；持续性和日志持久化仍有缺口

- **位置/证据**：`monitor_control13.py:13-89` 限制在精确 cgroup，记录 NVML、cgroup memory.current/peak/events、RSS/PSS（smaps_rollup）、CPU/threads、shared/locked、host/NUMA、processes；`:76-84` 只对 GPU 进程做 host 检查，已移除 host-wide 非 GPU 进程名扫描；`preflight_control13.py:50-58` 要求 monitor ready 新鲜和两个 observer active 且在 slice 内；`accept_control13.py:23-35` 检查 JSONL、最后一行 partial、gap<=5、覆盖 first dispatch 到 last cleanup；`install_observers13.sh:12-14` 启动 kernel/docker observer，UMask 0077，kernel 0600。
- **结论**：旧 F10 的 host 扫描和 monitor ready 消费者问题已修复；own cgroup/GPU/NUMA 覆盖较强。
- **剩余（N03/N09）**：observer 只在每次 dispatch 前检查 active，不持续验证；kernel 日志在 `/run`，accept 不归档；monitor JSONL 无大小上限，`accept` 用 `read_bytes().splitlines()` 全量读入内存；全局 daemon/audit 日志仍不持续导出（已列覆盖缺口）。
- **影响**：足以支持本 run 的 bounded 观测，但不足以排除 host 全局行为者；kernel 证据可能随 `/run` 丢失；长日志有内存/磁盘风险。
- **验证**：运行后检查 ready、gap、observer；归档 kernel/docker 日志；估算 JSONL 大小并改为流式解析；明确保留全局缺口。

### F11 DSA scratch 与 checkpoint 权重 — 合理澄清，R2 未签

- **位置/证据**：`weight_contract.py:4-14` 把 `attribute:*.topk_indices_buffer` 单列为 runtime indices，断言 `int32 [2048,2048]`、`bytes=16,777,216`、数量 234；`validation_control13.py:94-100` 比较 `tensors` 和 `kv_runtime_excluded`，记录 `dsa_workspace_references_excluded=234`，**不比较 scratch 值**；`native-weight-contract.json` 记录每 rank 5,814 个 weight references、234 个 runtime indices、156 个 KV 排他；`native-gold.json:221-282` 的 `tensor_count=6048=5814+234`，logical bytes 差值正好 `234×16,777,216`。
- **结论**：234 个 DSA top-k scratch 引用不是 checkpoint 权重/scale；新代码明确排除其值比较，只保留计数/dtype/shape/bytes，并比较 5,814 个非 scratch 引用和 156 个 KV 排他。A2 contract 中“scratch bytes 一致”不是 native service 的权重相等要求。这个界定是诚实的，不应被当作必须重复确认的问题。
- **剩余**：新的 native-service 8 rank 审计尚未执行；`tensor_audit.py` 和 `runtime-rank*.json` 的行 schema 不在审阅包内，无法确认 `tensors` 行是否包含值级哈希；这是 R2/GPU 审计范围，不能由 R1b 签。`accept_control13.py:18` 硬编码 5,814/234（数值已被 contract 证实，但应从冻结 contract 读取，N13）。
- **验证**：运行审计；检查 16 个 rank 记录；确认 5,814/234/156；记录 contract；不声称 scratch 值相等，也不把 scratch 值当 checkpoint 权重证明。

### F12 旧状态/旧 monitor — 已解决；新 guard/probe 未执行

- **位置/证据**：`run-manifest.json:1462,1471-1492` 已改为 G1-only/zero formal/checkpoint present；`PLAN.md:7` 明确旧 v6 guard 保持 02:50；`R1-RESPONSE.md:17` 说明旧 head/monitor 已停止、61 release 通过；仓库 `evidence/g0/legacy-monitor12-release60.json` 和 `legacy-ray12-head-release.json` 有旧进程停止证据；`evidence/guard_state` 是旧的 v6 02:50 状态，`evidence/cleanup-v6-probe.log` 是旧 run probe。
- **结论**：新身份状态不再被旧 A1 状态污染；旧 head/monitor 已停止；旧 v6 guard 未被静默停掉；新 guard/probe 是独立的，尚未执行。旧的 `guard_state`/`cleanup-v6-probe.log` 明确是 v6，不能当作新证明。
- **验证**：用新 run_id 安装 guard/probe；不要引用旧 v6 probe；检查旧 v6 timer 仍为 02:50；归档新 proof。

### F13 `-O`/import 顺序 — 主要解决；helper/test 覆盖仍不足

- **位置/证据**：`launch_control13.py:9-10` 拒绝 `-O/PYTHONOPTIMIZE`，`:16-31` 先哈希再在 `:32-33` import；`preflight_control13.py:4-5`、`materialize_control13.py:1-3`、`ray_control13.py:1-3`、`validation_control13.py:1-3`、`release_control13.py:1-3`、`accept_control13.py:1-3`、`prepare_host13.py:4-5`、`deploy_control13.py:23,38` 都有检查；`test_control13.py:35-38` 只对 materialize 做 `-O` 负测试。
- **结论**：launch/prepare/deploy 的“哈希先于自定义 import”已实现；主入口的 `-O` 拒绝已实现；`preflight`/`prepare_host` 用显式 `require`。
- **剩余（N11）**：`bootstrap_control13.py`、`monitor_control13.py`、`store_numa_exec_v10.py`、`run_store_server.py` 没有直接顶置检查（bootstrap 由 supervise 保护，monitor 经 `resource_window_control13` 传递保护，store_numa/run_store 是受保护 bootstrap 的子进程）；如果直接调用子脚本 `-O`，其 assert 会关闭。`test_control13` 未覆盖这些入口。
- **验证**：给这些入口加顶置检查；扩展负测试到每个入口；保持哈希先于 import。

### F14 时间线 — 已解决

- **位置/证据**：`resource-window-extension-v3.json:5-7` 的 `overlay_created_utc=2026-10-25T15:48:45Z`；`run-manifest.json:1472` `created_utc=14:54:32Z`；`control-input-manifest.json:462-465` 的 run-manifest SHA `30b9365d...`；control manifest 创建于 `15:53:10Z`。
- **结论**：overlay 15:48 晚于 manifest 的 14:54；overlay 的 `original_manifest_sha256` 与 control manifest 的最终 run-manifest 哈希一致；旧的 14:22/14:54 矛盾已消除。manifest 的 `created_utc` 是 run 身份创建时间，不是最后编辑时间；hash 绑定最终字节，时间线自洽。
- **验证**：`sha256sum` run-manifest；与 overlay/control manifest 对比；检查创建顺序。

---

## 三、新发现（需修正）

### N01（high）service13a 的 pool-created log 断言很可能不可满足

- **位置**：`bootstrap_control13.py:107`。
- **证据**：C++ 行来自 `checkpoint_store.cpp:83` 的 glog `LOG(INFO)`；`store.log` 是 Store 进程 stdout/stderr；glog 默认 INFO 写 glog 文件、stderrthreshold 默认 ERROR；模板没有 `GLOG_logtostderr`；候选包未包含 `sllm_store/logger.py`（父 manifest 有，但未复制）；仓库没有捕获到该行的 `store.log`；断言后无 poll/retry。`server.py:40-45` 的 Python `logger.info` 行才是可靠落在 `store.log` 的证据。
- **影响**：即使 716GiB pool 正确、`GetServerConfig` 和 `VmRSS` 通过，service13a 也可能在该 assert 失败，G1 无法完成。
- **验证**：实际运行 Store；检查 `store.log`；或在包内加入 `logger.py`/`GLOG_logtostderr=1`；或改为轮询 `StorageServicer: ... mem_pool_size=...`，保留 RPC/RSS/listener 为主证明。

### N02（high）guard 探针没有验证 cleanup 的退出状态/`failed=0`

- **位置**：`install_guard13.sh:32-35,44-47`；`preflight_control13.py:32`；`cleanup_guard13.sh:33`。
- **证据**：`probe_passed=True` 硬编码；install 只检查探针容器 `Running=false` 和 `/proc/$pid` 消失；不查 `systemd-run` 服务的 `Result`/`ExecMainStatus`；不解析 `cleanup_finished ... failed=...`；探针目标 slice 由 Docker 创建，主 run slice 由 systemd 创建，`systemctl kill` 路径未被直接证明。
- **影响**：探针可在 `failed=1`（remaining 非空或 pids 非 0）时仍报通过；也可能只证明 `docker stop` 有效，而非 slice kill 有效。guard 的准入证明弱于声明。
- **验证**：`systemctl show <run_id>-v13-test.service -p Result -p ExecMainStatus`，必须 0；解析 `failed=0`；加非容器哨兵或验证探针 slice `pids.current=0`；再检查主 slice 的 `systemctl kill`。

### N03（medium/high）guard proof、主 cleanup 日志和 kernel observer 证据只在 `/run`

- **位置**：`install_guard13.sh:53-56`；`cleanup_guard13.sh:12`；`install_observers13.sh:12`；`accept_control13.py` 全文。
- **证据**：`guard-verification.json` 在 `/run/<run_id>`（0444 但 tmpfs）；主 cleanup 日志 `/run/<run_id>/cleanup-v13-main.log`（0600）；kernel log `/run/<run_id>/kernel-private.jsonl`（0600）；`G1-acceptance.json` 不引用它们。`/run` 重启或被清后丢失。
- **影响**：08:50 的 root cleanup 证据和 guard 限额/ExecStart/probe 证据无法在最终交付中独立核验；若发生 exit137，own kernel 证据也可能丢失。这是“取证”缺口，不一定是控制失败，但影响 R1 证据完整性。
- **验证**：安装/运行后把 proof、主 cleanup log、kernel log（或授权导出）以 root 只读方式归档到证据目录并记录 SHA256；`G1-acceptance` 引用这些哈希；明确“全局 daemon/audit 未导出”的覆盖缺口。

### N04（medium）accept_control13 不复核输入/身份/pool/observer，receipt 不自绑定

- **位置**：`accept_control13.py:7-45`。
- **证据**：accept 读取控制目录中的 mutable 文件，不重新校验 `control-input-manifest.json` 的 SHA 或行哈希；不读 `deployment.json` 的 `code_commit`；不检查所有 dispatch 的 `boot_id` 与 manifest 一致；`pool-ready.json` 只查 `pool_bytes`，不查 `listener.pid`/`rpc_config`/`rss_bytes`；不查 `store-affinity-ready.json` 的线程掩码；不查 observer 日志/active；release 只查 61 行；`G1-acceptance.json` 不含 `code_commit`/`manifest_sha256`/`run_id`/`boot_id`。
- **影响**：acceptance receipt 不能自证由哪个 commit/manifest/boot 产生；控制目录在 dispatch 后若被改动，accept 可能仍通过；“real pool proof”和“observer active”没有在最终验收中复检。
- **验证**：accept 开头重哈希 manifest 和所有 rows；读 `deployment.json` 并要求 `code_commit` 等于审阅 SHA；要求所有 dispatch `boot_id` 等于 manifest；检查 `pool-ready` 全字段和 `store-affinity`；检查 observer unit/log；把绑定字段写入 `G1-acceptance.json`。

### N05（medium）deploy 不验证 origin/branch/审阅 commit

- **位置**：`deploy_control13.py:3-20,36-48`；`launch_control13.py:85-92`。
- **证据**：deploy 记录 `origin` 字面量 `https://github.com/BoyJiang2/serverlessllm-glm51-fp8`，但不运行 `git remote get-url origin`；不检查 branch；不要求 `head` 等于审阅 `e1af9dd`；launch 只检查 `deployment['code_commit']` 是 40 hex，不检查等于审阅 commit。
- **影响**：来自不同 remote/branch、但文件字节哈希相同的 clean checkout 仍可部署；审阅 verdict 绑定 `e1af9dd`，部署路径没有强制同一绑定。内容风险由 manifest 哈希限制，但审计绑定较弱。
- **验证**：`git remote get-url origin`、`git rev-parse --abbrev-ref HEAD`；deploy 接收 expected commit 并 assert `head==expected`；launch/preflight assert `deployment['code_commit']==expected`。

### N06（medium/low）prepare_host 仍在父资产树创建目录

- **位置**：`prepare_host13.py:26`。
- **证据**：`(asset/'controls'/manifest['run_id']).mkdir(parents=True,exist_ok=True)`；该路径未被任何 mount 使用；`R1-RESPONSE` F02 表述“only the new control directory and its separate native-home cache are writable”。
- **影响**：在父资产树留下新目录，虽不改冻结文件，但削弱“父资产未被写”的声明；若 `asset/controls` 不可写，prepare 失败。
- **验证**：检查 `ls -ld /data/brick1/.../controls`；删除该行或移动到 `root` 下；在 host-preparation 记录父树未被修改。

### N07（medium）launch 只检查剩余 120s，小于 ray/service supervisor 预算

- **位置**：`launch_control13.py:42-43`；`supervise_control13.py:9`；`bootstrap_control13.py:71-74`。
- **证据**：launch 只要求 `now < deadline-120`；ray supervisor 330s，service supervisor 3420s；bootstrap 对 service 要求 `budget>3600`，但 ray 没有等价检查。若 ray 在 08:48 启动，08:50 guard 会杀掉它；若 service 在 08:48 启动，container 已创建后 bootstrap 才失败。
- **影响**：并非假通过，但会产生可预防的失败控制尝试，违背“失败即停、不盲目重试”的预算纪律。
- **验证**：launch/supervise 在 create 前要求 `remaining >= supervisor_budget + margin`（ray 330+、service 3420+/3600+）；或把该检查放入 supervise；测试。

### N08（medium）validation 未比较 gold 的 logprob 值

- **位置**：`validation_control13.py:82-85`。
- **证据**：只检查 `len(logprobs)==len(token_ids)` 和每个 logprob `math.isfinite`；`native-gold.json` 的 `results[*].logprobs` 是冻结值；`PLAN.md:18` 要求 “Frozen token/text/finish/logprob checks ... mandatory”。
- **影响**：token/text/finish 正确但 logprob 漂移时仍可通过 G1 业务检查，弱于 PLAN 的“完整业务”标准。
- **验证**：与 gold logprob 比较（精确或明确容差），记录 max abs diff/hash；在 `model-result.json` 和 accept 中体现。

### N09（low/medium）monitor JSONL 无界，accept 全量读入内存

- **位置**：`monitor_control13.py:24-65`；`accept_control13.py:23-30`。
- **证据**：1Hz，每行包含 host_meminfo、NUMA、cgroup、GPU 和所有进程的 status/stat/cmdline/smaps；约 13 小时可生成数 GB；accept 用 `read_bytes().splitlines()` 和 `samples` 列表全量解析。
- **影响**：占用 `/data` 空间；accept 可能占用数十 GB 内存；主机有 1.9TiB 内存和 2.17TB 可用盘，暂不致命，但未量化。
- **验证**：估算实际行大小；限制/压缩/分段；accept 改为流式解析；在 host-preparation/accept 记录磁盘成本。

### N10（low）Store 仍绑定 `0.0.0.0:8073`

- **位置**：`bootstrap_control13.py:92-93`。
- **证据**：`--host 0.0.0.0`；容器在 Docker bridge 上，head 等其他容器可达；Readiness 用 127.0.0.1 和 child inode，已避免旧服务误判。
- **影响**：跨容器暴露面；不是 readiness 假通过。
- **验证**：改为 `--host 127.0.0.1`（所有客户端都在同一容器）；或在专用网络命名空间。

### N11（low/medium）`-O` 检查未覆盖 bootstrap/monitor/store_numa/run_store_server，测试只覆盖 materialize

- **位置**：`bootstrap_control13.py` 顶部；`monitor_control13.py` 顶部；`store_numa_exec_v10.py`；`evidence/run_store_server.py`；`test_control13.py:35-38`。
- **证据**：bootstrap 依赖 supervise 的检查；monitor 依赖 `resource_window_control13` 的 import 检查；store_numa/run_store_server 是受保护 bootstrap 的子进程；`-O` 负测试只跑 materialize。
- **影响**：主路径实际受保护，但直接调用子脚本 `-O` 时 assert 会关闭；测试未证明“每个 entry”都拒绝。
- **验证**：给这些入口加直接检查；扩展负测试。

### N12（low）head spec 的 validate/test 是自比较

- **位置**：`materialize_control13.py:12-13`；`test_control13.py:33-34`。
- **证据**：`expected_spec` 对 `headray13a/headray13b/headservice13a` 返回 `root/<name>.json` 本身；`test_head_exact` 因此是 tautology。
- **影响**：head 文件由 control manifest 哈希钉住，所以不是安全漏洞；但测试没有独立模板覆盖，docstring “vary only by head IP/exact slice” 对 head 不成立。
- **验证**：为 head 使用独立 template/expected dict；或明确说明 head 由哈希钉住，删除误导性测试。

### N13（low）accept 硬编码 5,814/234，而非读冻结 contract

- **位置**：`accept_control13.py:18`。
- **证据**：`evidence/reference/native-weight-contract.json` 和 `native-gold.json` 已给出 5,814/234；父 manifest 钉住 contract；accept 未读。
- **影响**：当前数值正确，但 contract 更新时 accept 会漂移。
- **验证**：从 hash-pinned contract/gold 读取期望值。

### N14（low）prepare_host 未复核 copy 目标哈希

- **位置**：`prepare_host13.py:24-25`。
- **证据**：只记录 source 的 per-file hashes；`shutil.copytree` 后没有对 `root/native-home` 重新计算。
- **影响**：静默复制损坏可能进入新缓存；18.8MB 的复核成本很低。
- **验证**：copy 后逐文件 hash 并与 source 比较，写入 host-preparation。

---

## 四、R1b 准入与全局覆盖缺口的实际影响

**R1b 当前不能准入。** 主要不是“代码完全不可用”，而是以下三项在运行前必须解决：

1. `N01`：service13a 的 `store.log` 断言很可能使正确的 716GiB pool 也失败；这是 G1 服务阶段的直接阻断。
2. `N02/N03`：guard 探针没有验证 cleanup 的 `failed=0`，且 proof/主 cleanup/kernel 证据只在 `/run`；这是“窗口守护/清理/取证”的核心准入缺口。
3. `N04/N07`：最终 accept 不自绑定 code_commit/manifest/boot，且 launch 的 120s 窗口余量小于 supervisor 预算；前者削弱验收链，后者会制造可预防的失败控制。

**全局 Docker daemon/audit 日志未持续导出的影响：**

- 对 **bounded G1**：不足以致命。本 run 有 own Docker events（按 `sllm.run_id` 过滤）、kernel observer、monitor 的 cgroup memory.events/local、host/NUMA、NVML 和进程/RSS/PSS。这足以观测本 run 的容器、cgroup、GPU 和主机压力。
- 对 **因果排除**：不足。它不能排除 host 全局行为者（root kill、系统 OOM killer、audit 驱动动作、其他 job 的 cgroup 压力）作为未来 exit137 的原因。
- 因此：**不阻断**在“historical exit137 cause remains unknown / only observed stability”表述下的 R1b 有限控制准入；**阻断**任何“external SIGKILL/OOM 已排除”“historical 137 root cause solved”“G1 通过等于 R2/正式计时通过”的声明。
- 具体不足：own kernel observer 只写 `/run`（root 0600），accept 不归档；若 `/run` 被清，连 own kernel 证据也会丢失。应在控制结束前把 `guard-verification.json`、`cleanup-v13-main.log`、`kernel-private.jsonl`（或其授权导出）和 `docker-events13.jsonl` 归档并纳入最终证据；全局 daemon/audit 仍应明确列为未覆盖。

**旧 v6 guard**：新代码不停止它，02:50 仍属旧 run；新 guard 使用独立 `-deadline-v13` timer，08:50 只杀新 run slice。这一点与任务要求一致，不需要“静默 handover”。

**已有直接授权**：03–09 新 booking 与“继续用 Azure/后面没人 book 就继续用”的直接人工授权已在 PLAN/R1-RESPONSE 记录；第二个 booking 的 Kimi 用途被透明保留，但用户直接授权覆盖本次 GLM 有限使用。**不需要重复确认**；需要的是启动前实时刷新 occupancy/booking，且不得终止其他作业、不得推导无限窗口。

---

## 五、运行条件（均未通过，不得伪造）

1. 新 `install_guard13.sh` 未安装；无新 `guard-verification.json`；旧 `guard_state` 是 v6 02:50，旧 `cleanup-v6-probe.log` 是旧 run，不能引用。
2. 新 `install_observers13.sh` 未运行；无新 kernel/docker observer active 证据。
3. 新 `monitor_control13.py` 未运行；无 `resource-monitor-v12.jsonl`、无 `monitor-v3-ready.json`。
4. ray13a/ray13b 未运行；无 `ray-only-ray13a/b/result.json`、无对应 supervisor result。
5. service13a bootstrap 未运行；无 `pool-ready.json`、无 `store.log`、无 `store-affinity-ready.json`、无 `model-result.json`。
6. 两个格式 × 两个 cycle 的 native lifecycle 未执行；8 rank 权重/scale 审计未执行；`accept_control13.py` 的 16 rank 记录、61 行 release、monitor 覆盖均不存在。
7. `release_control13.py` 的 ray13a/ray13b/allstopped 未运行；无 `release60-*.json`。
8. 当前 GPU 仍空闲；host 身份/booking/内存/磁盘需在启动前实时复核（证据快照：MemAvailable 1.97TB、/data 可用 2.17TB、8×H20-3e、0 GPU compute）。
9. 父资产 1,142 行在 handoff 中无 mismatch；launch 会重哈希；但 756GB 权重没有新鲜全量哈希，加载权重审计属 R2。
10. 716GiB pool 的构造顺序、`GetServerConfig`、`VmRSS` 尚未实际验证；`store.log` 断言是 N01 的直接风险。
11. 正式 ID 仍为 9 terminal/31 unstarted，zero formal consumed；`accept` 必须验证这 9 个结果哈希不变、31 个目录不存在。
12. 新 guard 必须在 20:00–07:50 窗口内安装；service 必须在 07:50 前启动（`budget>3600`）；ray 控制必须有足够剩余时间（N07）。
13. 旧 v6 guard 仍应在 02:50 保留；新 guard 不得被旧 cleanup 误杀；旧 monitor/head 已停止的 61 样本证据在 `evidence/g0`。
14. 全局 Docker daemon/audit 日志仍不持续导出；own kernel 日志是 root 0600 且仅 `/run`；最终报告必须保留“cause unknown”限制。
15. R2/FP8 正确性、`torch_dtype=bfloat16`、`placement_config.memory_pool=[]` 的语义、缓存/mincore、正式计时、统计和完整 gate 均未审；R1b 通过不等于 R2/正式准入。

---

## 六、局限

- 无 shell，不能独立重算全部 SHA256；本判定绑定调用给出的 `code_commit=e1af9dd...` 和 `manifest_sha256=7682cfe...`，并用清单/父 manifest/审计/sidecar/实际文件内容做交叉核验。
- 读取了候选源码、diff、模板、测试和证据；未逐行复算 1,142 个父文件，未重哈希 756GB 权重，未读取父资产中的 `sllm_store/logger.py`、`tensor_audit.py`、`runtime-rank*.json` 行 schema（这些不在候选包内）。
- 未访问 qjhs003 或其他节点；未运行远端命令；未修改文件；未读账号凭据；未派子代理。
- `handoff-current-audit-v2.json` 7,097 行，重点读取了身份/内存/尝试/版本/路径段，未逐行复核全部。
- `reviews/r1-candidate13` 和旧 verdict 保留原状，不作为本版审批；本版仅审 `controls/candidate13b`。

---

## 七、机器可读 JSON

```json
{
  "stage": "R1b",
  "verdict": "changes_required",
  "code_commit": "e1af9dd51055c2d1e4e8e3c2ea3dc0a9227f1592",
  "manifest_sha256": "7682cfe51de10cc06a2715e8c831626a1ed1335e41ffef5364daa9da64b881f4",
  "scope": "G1 untimed stability, window guard, identity, cleanup and forensics only; R2 and formal timing not reviewed",
  "r2_status": "not_reviewed_not_approved",
  "formal_timing_status": "not_reviewed_not_approved",
  "blocking_findings": ["R1b-N01", "R1b-N02", "R1b-N03", "R1b-N04", "R1b-N07", "R1b-N08"],
  "findings": [
    {"id": "R1b-F01", "severity": "resolved", "status": "resolved", "title": "父 manifest 哈希已与包内/审计一致", "location": "run-manifest.json:1470; launch_control13.py:17-23; control-input-manifest.json:117-120", "evidence": "父哈希 e38e31e7...；包内 evidence/run-manifest.json 与 handoff actual 同值；launch 断言父 manifest、review-input-manifest-v11 和全部 1,142 行；handoff manifest_sha da681f...，files=1142，mismatches=[]。", "impact": "旧 93c05a38 矛盾已消除；但 1,142 项不含 142 个 756GB 权重，权重为历史 LFS SHA-256，当前加载权重审计属 R2。", "verification": "启动前 sha256sum 父 run-manifest.json 和 review-input-manifest-v11.json；运行 launch 的断言；核对 handoff 1,142。"},
    {"id": "R1b-F02", "severity": "resolved_with_caveat", "status": "resolved_with_caveat", "title": "父资产在容器内只读；仍有父树写入点", "location": "launch_control13.py:59-63; headray13a.json:8-25; service13a-template.json:21; prepare_host13.py:20-26; model.cpp:40-71,96,103", "evidence": "asset_root readonly；新 native-home rw；控制目录 rw；Store 的 /experiment/store 只读；Model 以 O_RDONLY 打开 tensor.data_*；prepare_host 复制 18,875,581B 缓存；line 26 创建 asset/controls/run_id。", "impact": "历史 checkpoint/Store/gold 在容器内不可写；但 asset/controls 新目录削弱父树未修改声明，且 copy 目标未复核。", "verification": "docker inspect mount RW；find /experiment -writable；sha256sum 父 gold/store-restore；检查 asset/controls；删除 line 26；复核 copy 目标哈希。"},
    {"id": "R1b-F03", "severity": "partially_resolved", "status": "changes_required", "title": "清理脚本主体已强化；探针未验证 cleanup 退出状态", "location": "cleanup_guard13.sh:4-34; install_guard13.sh:11-12,29-35; preflight_control13.py:32", "evidence": "run_id 硬编码、精确参数、root、0700、先 slice kill、label/uid、remaining/pids 计入 failed；probe_passed=True 硬编码；install 只查容器停止/PID 消失；不查 service Result/ExecMainStatus，不解析 failed=0；探针 slice 由 Docker 创建。", "impact": "探针可在 cleanup failed=1 时仍通过；主 systemd slice 的 kill 路径未直接证明。", "verification": "systemctl show run_id-v13-test.service -p Result -p ExecMainStatus 必须 0；解析 cleanup_finished failed=0；加非容器哨兵或验证探针 slice pids.current=0；再检查主 slice。"},
    {"id": "R1b-F04", "severity": "partially_resolved", "status": "changes_required", "title": "guard proof 校验较强；证据只在 /run", "location": "install_guard13.sh:13-58; preflight_control13.py:28-49; launch_control13.py:48-51", "evidence": "proof 0444 root，含脚本 stat/hash、boot、ExecStart、timer、限额、探针日志；preflight 校验；launch 复检 timer；proof/主 cleanup 日志在 /run。", "impact": "启动前 guard 身份 fail-closed 成立；/run 清空或重启后完整 proof/cleanup 证据丢失；dispatch preflight 子集不含限额/ExecStart。", "verification": "归档 guard-verification.json 和 cleanup-v13-main.log 并哈希；纳入 G1-acceptance；systemctl show live 值。"},
    {"id": "R1b-F05", "severity": "resolved_with_runtime_refresh", "status": "resolved_with_runtime_refresh", "title": "窗口/授权已按精确 booking 绑定", "location": "resource_window_control13.py:14-29; resource-window-extension-v3.json:10-44; R1-RESPONSE.md:21-25,39-41; PLAN.md:7", "evidence": "三个精确 ID/users/resource、连续性、start<=now<last end、deadline=last-600、guard timer、UID/ssh_user；03-09 新 booking；直接人工授权；第二个 booking 的 Kimi 用途保留。", "impact": "旧 purpose/start/UID 问题已处理；直接授权足够本次有限窗口；无无限窗口、无其他作业终止。代码使用快照，不查询实时 API。", "verification": "启动前刷新真实 booking/occupancy 并与冻结 rows 对比；记录；不使用 09:00 后区间；不停止其他作业。"},
    {"id": "R1b-F06", "severity": "resolved", "status": "resolved", "title": "节点/硬件身份在 dispatch 前强制校验", "location": "run-manifest.json:1478-1492; preflight_control13.py:17-22; deploy_control13.py:22-33", "evidence": "hostname、machine-id、boot-id、有序 8 GPU UUID、UID；deploy 校验 machine/boot/GPU/UID；host 8×H20-3e；handoff 一致。", "impact": "错误节点/重启/GPU 顺序在 Docker create 前失败；deploy 未查 hostname 是小不对称。", "verification": "nvidia-smi -L；/etc/machine-id；boot_id；运行 launch preflight。"},
    {"id": "R1b-F07", "severity": "partially_resolved", "status": "changes_required", "title": "就绪/pool 证明强，但 pool-created log 断言不成立", "location": "bootstrap_control13.py:94-108; listener_control13.py:4-18; server.py:47-49,215-245; checkpoint_store.cpp:80-83; pinned_memory_pool.cpp:23-46; vllm_backend.py:181-197", "evidence": "私有 net/PID namespace；loopback；listener PID/inode；5s GetServerConfig；VmRSS；source 在 listen 前构造 CheckpointStore/PinnedMemoryPool；1,432×512MiB=716GiB；C++ 行是 glog LOG(INFO)；模板无 GLOG_logtostderr；候选包无 logger.py；无捕获 store.log；断言无 poll。", "impact": "旧 TCP 误判已修复；但日志断言可能使正确 pool 的 service13a 失败；accept 只查 pool_bytes；Store 仍绑 0.0.0.0。service13a 是 local_disk，不声称 pool-hit。", "verification": "运行 Store 检查 store.log；加入 logger.py/GLOG 配置或轮询 Python StorageServicer 行；accept 复核 pool-ready 全字段/affinity；绑定 127.0.0.1。"},
    {"id": "R1b-F08", "severity": "resolved", "status": "resolved", "title": "gold/store bytes 已一致", "location": "control-input-manifest.json:88-95,313; evidence/native-gold.sha256:1; handoff:3298-3305,5493-5495; validation_control13.py:26-27; bootstrap_control13.py:31-33", "evidence": "包内 gold 9a2db690...，sidecar 9a2db690...，父 gold 9a2db690...；store-restore e400fb42...，父 e400fb42...；父 manifest 钉住；launch 重哈希。", "impact": "旧 7e7d/0243 不一致已消除；validation 未用 gold rank_audits sha256 复核 reference 文件，但父 manifest 已钉住，低风险。", "verification": "sha256sum 父 gold/sidecar/store-restore；与清单/父 manifest 对比；运行 launch。"},
    {"id": "R1b-F09", "severity": "resolved_with_accept_gap", "status": "resolved_with_accept_gap", "title": "最终释放脚本已实现；accept 只查长度", "location": "release_control13.py:4-47; accept_control13.py:21-22; supervise_control13.py:13", "evidence": "61 样本/60s；NVML、own containers、slice pids、memory/cpu、host meminfo、processes；无 GPU 进程；allstopped 零容器/pids=0/baseline+16；ray stage 只允许 monitor。", "impact": "旧缺失已实现；release 只读，操作者须先停 head/monitor/observer；全局 no-GPU 假设独占；accept 不复核内容。", "verification": "每个 ray 后和全部停止后运行；检查 61 行、零容器、pids=0、GPU baseline；accept 内容复核。"},
    {"id": "R1b-F10", "severity": "resolved_with_gap", "status": "resolved_with_gap", "title": "monitor cgroup/GPU/NUMA 覆盖强；持续性/持久化有缺口", "location": "monitor_control13.py:13-89; preflight_control13.py:50-58; accept_control13.py:23-35; install_observers13.sh:12-14; launch_control13.py:36-37", "evidence": "cgroup-only；移除 host-wide 非 GPU 扫描；ready；两个 observer active；gap<=5；最后 partial；kernel 0600；own Docker events；NVML/cgroup/host/NUMA/RSS/PSS。observer 只在 dispatch 前检查；kernel 日志在 /run；monitor 无界；accept 全量读。", "impact": "own 覆盖强；host-global actor 不可排除；kernel 证据可能丢失；长日志有内存/磁盘风险。", "verification": "运行后检查 ready/gap/observer；归档 kernel/docker 日志；估算并流式解析 JSONL；保留全局缺口声明。"},
    {"id": "R1b-F11", "severity": "clarified", "status": "clarified", "title": "234 DSA scratch 是合理排除，R2 未签", "location": "weight_contract.py:4-14; validation_control13.py:94-100,125-130; native-weight-contract.json:1-81; native-gold.json:221-282; accept_control13.py:18", "evidence": "234 个 attribute:*.topk_indices_buffer，int32 [2048,2048]，16,777,216 bytes；5,814 非 scratch；156 KV；6048=5814+234；logical bytes 差 234×16,777,216；新审计记录排除计数、不比较 scratch 值。", "impact": "234 不是 checkpoint 权重/scale；非 scratch 相等才是权重/scale 检查；新 native-service 审计未执行；tensor_audit/行 schema 不在包内，值级相等属 R2。", "verification": "运行审计；检查 runtime-rank 行；确认 5,814/234/156；引用 contract；不声称 scratch 值相等。"},
    {"id": "R1b-F12", "severity": "resolved", "status": "resolved", "title": "旧状态已清理；新 guard/probe 未执行", "location": "run-manifest.json:1462,1471-1492; PLAN.md:7; R1-RESPONSE.md:17; evidence/g0/legacy-*; evidence/guard_state; evidence/cleanup-v6-probe.log", "evidence": "status G1-only/zero formal/checkpoint present；旧 head/monitor 61 release；旧 v6 guard 02:50 未停；新 guard/probe 未执行；旧 evidence 明确 v6。", "impact": "新身份不被旧 A1 污染；旧 v6 保留；新 proof 必须新做；旧 probe 不得引用。", "verification": "用新 run_id 安装/运行 guard/probe；检查旧 v6 timer 仍 02:50；归档新 proof。"},
    {"id": "R1b-F13", "severity": "partially_resolved", "status": "changes_required", "title": "-O 主入口已拒绝；helper/test 覆盖不足", "location": "launch_control13.py:9-10,16-37; preflight_control13.py:4-5; materialize_control13.py:1-3; ray_control13.py:1-3; validation_control13.py:1-3; release_control13.py:1-3; accept_control13.py:1-3; prepare_host13.py:4-5; deploy_control13.py:23,38; bootstrap_control13.py 顶部; store_numa_exec_v10.py; test_control13.py:35-38", "evidence": "hash-before-import 已实现；主入口拒绝 -O；test 只对 materialize 做负测试；bootstrap/monitor/store_numa/run_store_server 无直接检查（部分靠父/导入传递）；多数安全门仍 assert。", "impact": "主路径受保护；直接调用子脚本 -O 时 assert 关闭；测试未证明每个入口。", "verification": "给入口加顶置检查；扩展负测试；保持 hash-before-import。"},
    {"id": "R1b-F14", "severity": "resolved", "status": "resolved", "title": "overlay 时间线已自洽", "location": "resource-window-extension-v3.json:5-7; run-manifest.json:1472; control-input-manifest.json:462-465", "evidence": "overlay 15:48:45Z > manifest created 14:54:32Z；overlay original_manifest_sha 30b9365d = control manifest run-manifest sha；control manifest 15:53:10Z。", "impact": "旧 14:22/14:54 矛盾已消除；created_utc 是身份创建时间，hash 绑定最终字节。", "verification": "sha256sum run-manifest；与 overlay/control 对比；检查创建顺序。"},
    {"id": "R1b-N01", "severity": "high", "status": "changes_required", "title": "service13a 的 pool-created log 断言很可能不可满足", "location": "bootstrap_control13.py:107; checkpoint_store.cpp:83; server.py:40-45; service13a-template.json 全文", "evidence": "C++ LOG(INFO) 走 glog；store.log 是进程 stdout/stderr；glog 默认 INFO 写文件、stderrthreshold=ERROR；模板无 GLOG_logtostderr；候选包无 sllm_store/logger.py；无捕获 store.log；断言无 poll。", "impact": "正确 716GiB pool 也可能在 service13a 失败；G1 无法完成。", "verification": "运行 Store 检查 store.log；加入 logger.py/GLOG 配置或轮询 Python StorageServicer 行；保留 RPC/RSS/listener 主证明。"},
    {"id": "R1b-N02", "severity": "high", "status": "changes_required", "title": "guard 探针不检查 cleanup 退出状态", "location": "install_guard13.sh:32-35,44-47; preflight_control13.py:32; cleanup_guard13.sh:33", "evidence": "probe_passed=True 硬编码；只查容器停止/PID 消失；不查 systemd-run Result/ExecMainStatus；不解析 failed=0；探针 slice 由 Docker 创建，主 slice 由 systemd 创建。", "impact": "探针可在 failed=1 时通过；主 slice kill 路径未直接证明。", "verification": "systemctl show service Result/ExecMainStatus=0；解析 failed=0；加非容器哨兵或验证 pids.current=0；再测主 slice。"},
    {"id": "R1b-N03", "severity": "medium_high", "status": "changes_required", "title": "guard/主 cleanup/kernel 证据只在 /run", "location": "install_guard13.sh:53-56; cleanup_guard13.sh:12; install_observers13.sh:12; accept_control13.py 全文", "evidence": "guard-verification.json、cleanup-v13-main.log、kernel-private.jsonl 在 /run；G1-acceptance 不引用。", "impact": "/run 清空/重启后 guard 和 cleanup 证据丢失；exit137 的 own kernel 证据可能丢失。", "verification": "归档 proof/cleanup/kernel 并哈希；纳入 G1-acceptance；明确全局缺口。"},
    {"id": "R1b-N04", "severity": "medium", "status": "changes_required", "title": "accept 不复核输入/身份/pool/observer，receipt 不自绑定", "location": "accept_control13.py:7-45", "evidence": "不重哈希 manifest/rows；不读 deployment code_commit；不查 dispatch boot_id；pool-ready 只查 pool_bytes；不查 affinity/observer；release 只查 61 行；receipt 无 code_commit/manifest/run_id/boot_id。", "impact": "acceptance 不自证 commit/manifest/boot；控制目录被改动后可能仍通过；real pool proof/observer 未在最终验收复检。", "verification": "accept 开头重哈希；读 deployment/boot；检查 pool-ready 全字段/affinity/observer；receipt 写入绑定。"},
    {"id": "R1b-N05", "severity": "medium", "status": "changes_required", "title": "deploy 不验证 origin/branch/审阅 commit", "location": "deploy_control13.py:3-20,36-48; launch_control13.py:85-92", "evidence": "origin 是字面量；无 git remote get-url；无 branch 检查；不要求 head 等于 e1af9dd；launch 只检查 40 hex。", "impact": "不同 remote/branch 但相同文件哈希的 clean checkout 可部署；审计绑定较弱。", "verification": "git remote get-url origin；branch；expected commit assert；launch/preflight assert deployment code_commit。"},
    {"id": "R1b-N06", "severity": "medium_low", "status": "changes_required", "title": "prepare_host 仍在父资产树创建目录", "location": "prepare_host13.py:26", "evidence": "asset/controls/run_id mkdir；未被 mount 使用；与 only new control/cache writable 表述不一致。", "impact": "父树留下未受控目录；若不可写则 prepare 失败。", "verification": "检查 asset/controls；删除或移动到 root；记录父树未修改。"},
    {"id": "R1b-N07", "severity": "medium", "status": "changes_required", "title": "launch 120s 余量小于 supervisor 预算", "location": "launch_control13.py:42-43; supervise_control13.py:9; bootstrap_control13.py:71-74", "evidence": "launch 只要求 now < deadline-120；ray supervisor 330s，service 3420s；ray 无 3600 等价检查；08:48 启动会被 08:50 guard 杀掉。", "impact": "可预防的失败控制尝试；不违反不重试，但浪费预算。", "verification": "launch/supervise 要求 remaining >= budget + margin；测试。"},
    {"id": "R1b-N08", "severity": "medium", "status": "changes_required", "title": "validation 未比较 gold logprob 值", "location": "validation_control13.py:82-85", "evidence": "只检查长度和 finite；native-gold results[*].logprobs 是冻结值；PLAN.md:18 要求 logprob 检查。", "impact": "token/text/finish 正确但 logprob 漂移仍可通过。", "verification": "与 gold logprob 比较（精确或容差）；记录 max diff/hash；accept 体现。"},
    {"id": "R1b-N09", "severity": "low_medium", "status": "changes_required", "title": "monitor JSONL 无界，accept 全量读", "location": "monitor_control13.py:24-65; accept_control13.py:23-30", "evidence": "1Hz 全量 processes/status/smaps；13h 可数 GB；accept read_bytes/splitlines/samples。", "impact": "/data 空间和 accept 内存风险。", "verification": "估算行大小；限制/压缩/分段；流式解析。"},
    {"id": "R1b-N10", "severity": "low", "status": "changes_required", "title": "Store 绑定 0.0.0.0", "location": "bootstrap_control13.py:92-93", "evidence": "容器 bridge 上 head 等可达；readiness 已用 loopback/child inode。", "impact": "跨容器暴露面；非 readiness 假通过。", "verification": "改为 --host 127.0.0.1 或专用网络。"},
    {"id": "R1b-N11", "severity": "low_medium", "status": "changes_required", "title": "-O 检查未覆盖全部入口/测试", "location": "bootstrap_control13.py 顶部; monitor_control13.py 顶部; store_numa_exec_v10.py; run_store_server.py; test_control13.py:35-38", "evidence": "部分依赖父/import 传递；负测试只 materialize。", "impact": "直接子脚本 -O 时 assert 关闭；测试未证明每个 entry。", "verification": "加直接检查；扩展负测试。"},
    {"id": "R1b-N12", "severity": "low", "status": "changes_required", "title": "head spec validate/test 是自比较", "location": "materialize_control13.py:12-13; test_control13.py:33-34", "evidence": "expected_spec 返回同一 head json；test tautology。", "impact": "hash 钉住所以非安全漏洞；测试假保证。", "verification": "独立 template/expected dict 或明确说明。"},
    {"id": "R1b-N13", "severity": "low", "status": "changes_required", "title": "accept 硬编码 5814/234", "location": "accept_control13.py:18", "evidence": "contract/gold 已给值；父 manifest 钉住；accept 未读。", "impact": "contract 更新时漂移。", "verification": "从 hash-pinned contract 读取。"},
    {"id": "R1b-N14", "severity": "low", "status": "changes_required", "title": "prepare_host 未复核 copy 目标", "location": "prepare_host13.py:24-25", "evidence": "只记录 source hash；copytree 后无目标复核。", "impact": "静默复制损坏可能进入新缓存。", "verification": "copy 后逐文件 hash 对比。"}
  ],
  "runtime_conditions": [
    "新 guard/probe 未安装/未执行；无 guard-verification.json；旧 guard_state 是 v6 02:50，旧 cleanup-v6-probe.log 是旧 run，不得引用。",
    "新 install_observers13 未运行；无新 kernel/docker observer active 证据。",
    "新 monitor 未运行；无 resource-monitor-v12.jsonl、无 monitor-v3-ready.json。",
    "ray13a/ray13b 未运行；无 ray-only-* result/supervisor result。",
    "service13a bootstrap 未运行；无 pool-ready.json、store.log、store-affinity-ready.json、model-result.json。",
    "两个格式 × 两个 cycle 的 native lifecycle 未执行；8 rank 权重/scale 审计未执行；16 rank 记录不存在。",
    "release_control13 的 ray13a/ray13b/allstopped 未运行；无 release60-*.json。",
    "当前 GPU 空闲；启动前需实时复核 host 身份/booking/MemAvailable/磁盘；证据快照 MemAvailable 1.97TB、/data 2.17TB、8×H20-3e、0 GPU compute。",
    "父资产 1,142 行 handoff 无 mismatch；launch 会重哈希；756GB 权重无新鲜全量哈希，加载权重审计属 R2。",
    "716GiB pool 的构造顺序/GetServerConfig/VmRSS 尚未实际验证；store.log 断言是 N01 直接风险。",
    "正式 ID 9 terminal/31 unstarted，zero formal consumed；accept 必须验证 9 个结果哈希不变、31 个目录不存在。",
    "新 guard 必须在 20:00–07:50 安装；service 必须在 07:50 前启动（budget>3600）；ray 控制必须有足够剩余时间（N07）。",
    "旧 v6 guard 仍应 02:50 保留；新 guard 独立；旧 monitor/head 的 61 样本 release 在 evidence/g0。",
    "全局 Docker daemon/audit 日志不持续导出；own kernel 日志 root 0600 且只在 /run；最终报告必须保留 cause unknown。",
    "R2/FP8/正式计时/统计/完整 gate 未审；R1b 通过不等于 R2/正式准入。"
  ],
  "limitations": [
    "无 shell，不能独立重算全部 SHA256；绑定调用给出的 code_commit e1af9dd... 和 manifest_sha256 7682cfe...，以清单/父 manifest/审计/sidecar/实际内容交叉核验。",
    "未逐行复算 1,142 个父文件；未重哈希 756GB 权重；未读取父资产 sllm_store/logger.py、tensor_audit.py、runtime-rank 行 schema（不在候选包内）。",
    "未访问 qjhs003 或其他节点；未运行远端命令；未修改文件；未读账号凭据；未派子代理。",
    "handoff-current-audit-v2.json 7,097 行，仅重点读取身份/内存/尝试/版本/路径段。",
    "reviews/r1-candidate13 和旧 verdict 保留原状，不作为本版审批；本版仅审 controls/candidate13b。",
    "R2 的 FP8 正确性、torch_dtype=bfloat16、placement_config.memory_pool=[] 语义、缓存/mincore、正式计时和统计未审。",
    "服务 716GiB pool、store.log、monitor、guard、release、accept 均未实际执行；所有 runtime_conditions 均未通过。"
  ]
}
```

**最终建议**：先修 N01（改为可验证的 pool 证据）、N02/N03（探针退出状态 + 证据归档）、N04（accept 自绑定和池证据复检）、N07（窗口余量），并补充 N05/N06/N08；然后重新提交绑定新 commit/manifest 的 R1b 复审。R2 和正式计时仍未获准。