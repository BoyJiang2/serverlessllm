# R1c 独立只读交叉审阅判定

**结论：`changes_required`（不批准 R1c）。**

不是 `blocked`：预约/直接授权、父资产和候选包仍在，多数问题可在 R1c 内本地修正后复审；当前 GPU 被其他作业占用是运行期资源限制，不是代码不可修。也不是 `approved`：新 guard/probe、monitor、真实 cache、G1、8 rank 审计均未执行，且静态审查仍发现会使 deploy 失败、service 预算不自洽、cache 证明不足的实质问题。**R2、FP8 正确性、正式计时与统计未审、未通过。**

绑定：

- `code_commit`: `b5b48807fe04251777c324d52f9e015c5d05aad5`
- `control-input-manifest` SHA256: `2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56`
- 审阅对象：`controls/candidate13c`。`candidate13b` 及其 `changes_required` verdict 保留原状，未作为本版审批。
- 方式：只读实际源码、diff、模板、证据；未执行 shell/远端/资源控制，未修改文件，未读凭据，未派子代理。无 shell 不能独立重算全部 SHA；关键交叉一致性来自实际文件、清单、父 manifest、审计与已读证据。

---

## 一、先纠正上轮被实际证据反证的结论

这四点作者反证成立，我独立核验后**撤回/修正**上轮结论，不机械沿用：

### 1. 上轮 N01（pool-created log 不可满足）— 撤回

- **实际证据**：`evidence/logging-source/store-log-routing.json` 列出 21 个历史 `store.log` 前缀，**18 个包含** `I... checkpoint_store.cpp:83] Memory pool created with 716GB`，包含 716GiB pool；3 个缺失（formal6、attempt2、ctl11b）都是 pool 尚未完成即被终止的日志。
- **实际源码**：`checkpoint_store.cpp:80-83` 在 `PinnedMemoryPool` 构造后写该 marker；`server.py:222-245` 在 listen 前构造 `CheckpointStore`；`run_store_server13.py` 与历史 `run_store_server.py` 字节一致。`logger.py` 只配置 Python logging 到 stdout，不解释 C++ glog，但实际 `store.log` 的前缀包含该 C++ 行；`REPORT-TO-MAIN.md:18` 还记录 formal6 的 `WARNING: Logging before InitGoogleLogging() ... STDERR`。
- **结论**：上轮“默认 glog INFO 不一定进入 store.log”的推测被实际历史日志证伪；`bootstrap_control13.py:110` 保留 `Memory pool created with 716GB` 断言是**有实证支持**的。3 个缺失前缀对应 pool 未完成，而 `GetServerConfig`/`VmRSS` 检查也会失败，因此不会假通过。**N01 撤回，不作为 R1c finding。** 残余仅是 marker 在 pool 完成后才出现，当前检查顺序（`store_ready` → gRPC → RSS → log）合理。

### 2. 上轮 N10（Store 应绑 127.0.0.1）— 撤回

- **实际源码**：`evidence/logging-source/source/ServerlessLLM/sllm/store_manager.py:239-275,371-384` 从 `get_worker_nodes()` 取 `node_address`，并创建 `SllmStoreClient(f"{node_address}:8073")`。StoreManager 使用 **worker 地址**，不是 loopback。
- **结论**：若把 Store 改为 `127.0.0.1:8073`，跨容器原生 Store 路由会断。当前 `bootstrap_control13.py:95-96` 的 `--host 0.0.0.0` 是该原生路径所需；`launch_control13.py:55-64` 未发布 host 端口，`listener_control13.py` 仍强制 child PID socket inode。**N10 撤回。** 残余只是同一 bridge 上的可达面，属原生路由要求，不是 readiness 假通过。

### 3. 上轮 N06（prepare_host 父树写入）— 事实澄清，非“写权重”

- **实际源码**：`prepare_host13.py:32-35` 创建 `asset/controls/<run_id>`，但只要求**空目录**，并记录 `parent_empty_mountpoint`；`launch_control13.py:62` 将新控制目录挂载到 `/experiment/controls/run`，该空目录是嵌套 readonly bind 的 mount target。
- **结论**：这不是写冻结 payload；是嵌套只读挂载所需的唯一空子目录。**N06 修正为“允许的唯一空 mountpoint，需保持为空且无 frozen 文件”**；仍需运行期检查 `find`/`stat`。若该目录非空，`prepare_host:34` 会失败。

### 4. 上轮 N08（logprob 应精确比较）— 撤回

- **实际契约**：`USER-OBJECTIVE.md:110` 要求 32 个原始 token 与金标一致、自然 EOS 业务有限/非退化；没有 float logprob 精确要求。`PLAN.md:18` 现在明确写 “Frozen exact token/text/finish and finite-logprob checks”。
- **结论**：`validation_control13.py:79-85` 对 token/text/finish 精确、对 logprobs 只检查长度和 `isfinite`，符合原契约。**N08 撤回**；不应临时增加 float 精确阈值。

---

## 二、上轮 N02–N14 复核

| Finding | R1c 结论 | 实际依据 |
|---|---|---|
| N02 cleanup 探针 | **已解决** | `install_guard13.sh:34-46` 加入非容器 sentinel（`systemd-run --slice=target.slice /bin/sleep 120`），并检查 `Result=success`、`ExecMainStatus=0`、`SubState=exited`、probe slice `pids.current=0`、`cleanup_finished ... failed=0`；`install_guard13.sh:61-71` 重新计算 `probe_passed` 并在 `:76-78` 归档 proof。探针逻辑已实质修复。 |
| N03 guard/cleanup/kernel 持久化 | **已解决（代码层）** | `cleanup_guard13.sh:12-16` 写 `$base/root-evidence-private13`；`install_observers13.sh:12` 写私有 kernel 日志；`install_guard13.sh:76-78` 归档 `guard-verification.json`；`snapshot_root13.py` 生成 root-only index；`accept_control13.py:125-131` 校验 index。实际执行仍未发生。 |
| N04 accept 自绑定/复检 | **已解决（代码层）** | `accept_control13.py:83-89` 重哈希 manifest/rows、读 deployment/code/boot；`:95-97` 校验 head/worker dispatch；`:99-119` 校验 contract、PSS、cache、pool、listener、RSS、affinity；`:120-124` release/monitor；`:125-131` root index。仅剩下述 B04/B05/B10/B11 的字段级缺口。 |
| N05 deploy commit/origin/prefix | **已解决（代码层）** | `deploy_control13.py:7-13` 校验 origin、experiment branch、reviewed commit ancestor、`git diff --exit-code reviewed_commit HEAD -- controls/candidate13c`；`:44` 写入 `code_commit=reviewed_commit`、`repository_metadata_commit=head`；`launch/prepare/accept` 都要求 reviewed commit。残余是 origin 精确字符串 `.git` 与 branch 名的运行期一致性。 |
| N06 父树空 mountpoint | **事实澄清** | 见上节；空目录是嵌套 readonly mount 的 target；`prepare_host:34` 要求为空；无冻结 payload 写入。 |
| N07 全预算 | **部分解决，仍有实质不一致** | launcher/supervisor 已用 `budget_control13.require_budget`（330/3420 + 180），但 `bootstrap_control13.py:75-77` 仍 `budget=deadline-now-120; assert budget>3600`，实际要求剩余 >3720；见 B02。 |
| N09 monitor 日志/accept 流式 | **已解决** | `monitor_control13.py:82-86` 4GiB producer cap；`accept_control13.py:15-35` 4MiB row 上限、逐行流式，不整文件 `read_bytes().splitlines()`。 |
| N11 `-O`/负测试 | **已解决** | `bootstrap/monitor/store_numa_exec_v13/run_store_server13/collect_pss13/cache_state_control13/snapshot` 等均有顶置检查；`test_control13.py:37-42` 覆盖 16 个入口；`seal_candidate13.py` 也有。 |
| N12 head 自比较测试 | **已解决** | `test_control13.py:33-36` 改为修改 `headray13a.json` argv 并期望 `validate_spec` 拒绝；`expected_spec` 仍返回原文件，但 mutate 后比较失败，测试不再是 tautology。 |
| N13 硬编码 5814/234 | **已解决** | `accept_control13.py:99,105-107` 从 `evidence/native-weight-contract.json` 读取每个 rank 的 5,814/234/156。 |
| N14 copy 目标 hash | **已解决** | `prepare_host13.py:29-31` 对 `native-home` 每个文件重新哈希并与 source 比较，记录 `cache_copy_verified=True`。 |

**F14 时间线：部分回退，见 B07。** `seal_candidate13.py:10-14` 在 17:23 更新 `run-manifest` 的 status/candidate_revision，并把 `resource-window` 的 `original_manifest_sha256` 改为 `15a0e5...`；但 `resource-window-extension-v3.json:6` 的 `overlay_created_utc` 仍是 `15:48:45Z`。即 overlay 文件在 17:23 被重写，却声称 15:48 创建，且绑定 17:23 才产生的 run-manifest 字节。该时间声明不自洽。

---

## 三、R1c 剩余 finding（含新增 cache 控制）

### B01（high，阻断 deploy）`__pycache__` 使 deploy 的 `actual != allowed`

- **位置**：`seal_candidate13.py:37-38`；`deploy_control13.py:20-22`；`control-input-manifest.json`；实际 `controls/candidate13c/__pycache__/*.pyc`。
- **实际证据**：`control-input-manifest` 有 **178** 个文件行；实际目录经 glob 有 **188** 个文件，其中 **8 个** `__pycache__/*.cpython-313.pyc`。`seal_candidate13.py:37-38` 在生成清单时排除 `__pycache__`，但 `deploy_control13.py:21` 的 `actual={p... for p in package.rglob('*') if p.is_file()}` **未排除**。`allowed` 为 178 rows + `control-input-manifest.json` + `REVIEW-PROMPT.md` = 180；`actual` = 188；`deploy:22` 的 `if actual!=allowed: raise` 必然触发。
- **影响**：本地 25 个测试/Python compile 已生成 `__pycache__`；按当前代码，`deploy_control13.py` 在复制前直接失败，G1 无法部署。这是**操作路径的阻断**。
- **最小验证**：`find controls/candidate13c -type d -name __pycache__`；`python deploy_control13.py 2feefa... b5b488...` 预期在 `actual!=allowed` 失败。修正：deploy 的 actual 排除 `__pycache__`/`*.pyc`，或 seal 在生成清单后删除 `__pycache__`；两者取一并测试。

### B02（medium/high）service 预算 3600 与 bootstrap >3720 不一致

- **位置**：`budget_control13.py:2-8`；`launch_control13.py:43-44`；`supervise_control13.py:14-15`；`bootstrap_control13.py:75-77`；`test_accept_control13.py:34-38`。
- **实际证据**：`require_budget('service13a', 3600)` 返回 3420（测试 `test_accept:38` 也确认 3600 通过）；但 bootstrap 计算 `budget=int(deadline-time.time()-120)`，再 `assert budget>3600`，等价于要求 `deadline-now>3720`。因此剩余 3600–3719 秒时，launch/supervisor 的“全预算”检查通过，container 创建后 bootstrap 才失败。
- **影响**：service13a 在计划边界附近会被“全预算已检查”误导；属于可预防的失败控制，也说明 launcher/supervisor 的预算不是 bootstrap 实际使用的预算。
- **最小验证**：对 3600、3719、3720 三点做纯函数测试；对齐为 `assert budget>3300`（inner alarm）或把 `require_budget` service 提高到 3720。确保 `test_accept` 的边界与 bootstrap 一致。

### B03（medium）cache NUMA 容量代理可能选到 Store 已占的 node0

- **位置**：`cache_prepare_v8.py:23-33,63-80`；`cache_state_control13.py:40-45`；`store_numa_exec_v13.py:52-60`；`accept_control13.py:116-119`。
- **实际证据**：`choose_node` 用 `MemFree+FilePages` 作 capacity proxy；`store_numa_exec_v13.py` 把 716GiB Store 固定在 node0。cache 输入 756,177,645,648B（142 shards）加 6 metadata，约 704GiB；加 64GiB margin 约 768GiB。`MemFree+FilePages` 会把“将被 DONTNEED 的同一 checkpoint page cache”计入容量；若 node0 的 FilePages 较高，`max(candidates)` 可能选 node0。node0 同时容纳 716GiB pinned Store + 704GiB cache 会超过单 node 物理内存；mincore 最终可能不全 resident 或触发回收/OOM。`validate_cache` 和 `accept` 只校验 cache 自身，不比较 `cache_g1hot13/summary.json` 的 `hot_placement.node` 与 `store-numa-launch.json` 的 `node`。
- **影响**：cache 控制可能因选错 NUMA node 失败，或与 716GiB Store 叠加造成主机压力；即使最终 mincore 失败也不是假通过，但会造成可预防的 G1 失败/资源风险。
- **最小验证**：启动前 `numactl -H`/`node_meminfo`；真实 cache 后检查 `hot_placement.node != store-numa-launch.node`；否则 fail/重选。修正：capacity 用 `MemAvailable` 或排除 Store 节点，或在 `prepare_hot_placement` 后强制不同节点/重试。`accept` 增加该字段检查。

### B04（medium）cache accept 只验最终/初始 eviction，未验 initial-scan/repair/read-idle

- **位置**：`accept_control13.py:49-73`；`cache_state_control13.py:62-118`；`cache_repair_v9.py:13-66`。
- **实际证据**：`validate_cache` 校验 `summary['files']`（最终 full scan）和 `eviction-mincore.jsonl`（初始 0），检查 148、SHA、pages、resident；也检查 `repair_passes<=2` 和总 600s。但它**没有**读取 `initial-scan.json`、`repair_history` 中的 `reads`/`files`/`selection`、`preparation.jsonl` 的 `bytes_read`、`read_idle_receipt`。`cache_state` 写入了这些文件。
- **影响**：最终 full 和初始 0 成立时，中间 repair 是否只读白名单文件、是否真的完成、read-idle 是否成立，accept 不独立证明。PLAN 说 “mincore vectors, SHA, lengths, input identity and page counts are checked by accept”，当前只覆盖 final/eviction。
- **最小验证**：accept 逐项读取 `initial-scan.json`、每个 `repair_N/reads.json`/`selection.json`/`summary.json`，确认 repair 仅白名单、`bytes_read==size`、`read_idle_receipt` 最后连续 2 次 0；或明确把 accept 的 cache 证明范围降为 final+eviction，并在报告中写明。

### B05（medium）monitor 的 5s gap 仍用 `monotonic_ns`，未用 begin/end 或 sample duration

- **位置**：`monitor_control13.py:33,69-81`；`accept_control13.py:15-35`；`CURRENT-BOUNDARY.md:7`。
- **实际证据**：monitor 在 `:33` 记 `sample_begin_ns`，在 `:69` 记 `monotonic_ns`（重活/进程扫描之后），在 `:80` 记 `sample_end_ns`；`accept` 的 `monitor_summary` 只取 `row['monotonic_ns']` 计算相邻 gap，未检查 `sample_end-sample_begin`，也未检查 begin-to-begin。历史上的 33.8s 混合时间 gap 正是慢读/非原子；新代码移除了 fast row 的 smaps（PSS 独立），但 accept 的 gap 指标仍可能低估“某一慢 sample 自身耗时”，尤其首/末 sample。
- **影响**：fast heartbeat 的连续性证明弱于“fast rows carry begin/end”的声明；不能据此断言任何单 sample 的读取都 ≤5s。
- **最小验证**：accept 同时检查 `sample_end_ns-sample_begin_ns<=5s` 和 begin-to-begin；对首/末 sample 单独覆盖；在真实 monitor 运行时用合成慢 sample 做负测试。

### B06（low/medium）preflight 祖先 cgroup 检查不含 root，且只检查 memory.max

- **位置**：`preflight_control13.py:50-56`。
- **实际证据**：循环从 run slice 的 cgroup 向上，`while current != /sys/fs/cgroup`，因此 top-level `run.slice` 只检查自身，**不检查 root cgroup**；同时读取了 `memory.high`/`memory.events` 但只对 `memory.max` 做断言。若 root 或父级 `memory.max` 低于 1700G，或 `memory.high` 造成节流，preflight 不会发现。
- **影响**：1700GiB cgroup 预算的“祖先确认”不完整；真实运行时可能被外层限制。
- **最小验证**：在真实 host 读取 `/sys/fs/cgroup/memory.max`、`/sys/fs/cgroup/memory.high`、run slice 及父级；把 root 纳入，并对 `memory.high`（非 max 时）和 `memory.max` 同时断言。记录 `memory.swap.max`。

### B07（low）seal 重写 window 但 overlay_created_utc 未更新（F14 回退）

- **位置**：`seal_candidate13.py:10-14`；`run-manifest.json:1462,1472,1493`；`resource-window-extension-v3.json:5-6`。
- **实际证据**：seal 在 17:23 更新 `run-manifest` 的 status/candidate_revision，并重写 `resource-window` 的 `original_manifest_sha256` 为 `15a0e5...`；但 `overlay_created_utc` 仍是 `2026-10-25T15:48:45Z`。overlay 绑定的是 17:23 才产生的 run-manifest 字节，却声称 15:48 创建。`run-manifest.created_utc` 14:54 与 overlay 15:48 的旧比较通过，但“final bytes”已变。
- **影响**：时间线再次不自洽，削弱 overlay 与最终 manifest 的绑定可信度；属于 low，但不应在 R1c 保留旧 F14 结论。
- **最小验证**：seal 在重写 window 时把 `overlay_created_utc` 设为当前时间；或先冻结 run-manifest、最后生成 window；在 control manifest 中保留顺序证据。

### B08（low）cache 初始 `read_hot` 未断言 bytes_read；`hot-placement.json` 缺 eviction 结果

- **位置**：`cache_state_control13.py:43-46,80-87`；`cache_prepare_v8.py:75-80,98-99`。
- **实际证据**：`cache_repair_v9.py:52` 对 repair reads 断言 `bytes_read==size`；初始 `read_hot` 的 `parallel_rows` 没有对应断言，`preparation.jsonl` 的 `bytes_read` 不被 `accept` 检查。`prepare_hot_placement` 先写 `hot-placement.json`，之后才设置 `record['eviction_resident_pages']`，文件内容不包含该最终字段（summary 中有）。
- **影响**：初始 read 的完整性主要靠后续 full mincore；若文件在 read 中被截断/替换，最终 full 可能仍被其他路径填满，或 bytes_read 与 size 不一致未被明确记录。low。
- **最小验证**：`cache_state` 对初始 `parallel_rows` 断言 `bytes_read==size`；accept 检查 `preparation.jsonl`；`hot-placement.json` 在 eviction 后重写或把结果只放 summary。

### B09（low）cache 的 142 个 LFS `expected_sha256` 未被交叉校验

- **位置**：`cache_contract13.py:11-19`；`cache-inputs13.json` 权重行；`run-manifest.json`/`checkpoint/VERIFIED.json`。
- **实际证据**：`cache-inputs13.json` 权重行有 `expected_sha256` 和 “official LFS identity; no fresh payload hash in this control”；`cache_contract` 对权重只看 closure/size，不比较 `expected_sha256` 与 `run-manifest`/`checkpoint VERIFIED` 的 LFS 身份。`cache-inputs13.json` 被 control manifest 哈希钉住，所以不是安全漏洞；但“冻结 LFS 身份”与官方清单的静态一致性没有在代码中核对。
- **影响**：如果 `cache-inputs13.json` 生成时与官方 LFS 列表不一致，cache 控制仍会通过 closure/size；后续 8 rank 审计是独立检查。low。
- **最小验证**：在 `cache_contract` 或 seal 中把 142 个 `expected_sha256` 与 `checkpoint/VERIFIED.json` 的 LFS SHA256 对表；不重哈希 payload。

### B10（medium）accept 未读取 per-cycle `idle60` 的 61 条

- **位置**：`validation_control13.py:108-129`；`accept_control13.py:100-110`。
- **实际证据**：validation 每个 format/cycle 写 `cycle{cycle}-idle60.jsonl`（61 行，含 GPU、GPU 进程、cgroup memory/current、cpu.stat、/dev/shm、memory.stat），并写 `cycle{cycle}-pss.json`。accept 只检查 PSS 和 `model-result`，**没有读取/校验 idle60** 的 61 行、60s、GPU 回退、pids、shared/locked。最终 `release60` 有独立校验，但 per-cycle 删除后 60s 观察不在 accept 中。
- **影响**：PLAN 第 4/6 条的“每次删除后 60s 观察”在最终验收中没有逐字段证明；即使 `model-result` 写 cycles=2，idle60 文件可缺失/无效而 accept 仍通过。
- **最小验证**：accept 对每个 fmt/cycle 读取 `idle60.jsonl`，要求 61 行、`second` 0–60、≥60s、末行 GPU/进程/ pids；检查 memory/shm/cpu；或用单独 `validate_idle60` 函数并加负测试。

### B11（low）accept 的 release 校验未覆盖 RSS/PSS/locked/shared

- **位置**：`accept_control13.py:37-47`；`release_control13.py` 记录 `processes`/`cgroup`。
- **实际证据**：`release_control13.py` 记录每 sample 的 GPU、own containers、cgroup memory/cpu/pids、processes（uid2147 的 status/stat/smaps）。`validate_release` 只校验 61/60s、无 GPU 进程、own running、pids0、GPU baseline；不检查 processes/RSS/PSS/locked/shared。用户要求的释放观测覆盖“RSS/PSS/锁页/共享内存/池/服务/线程/CPU”，其中部分由 monitor/idle60 覆盖，release 的字段未被 accept 逐项核对。
- **影响**：释放证据的字段级完整度低于声明；low。
- **最小验证**：accept 对 release 的 `processes`/`cgroup` 至少断言无残留 uid2147 进程、`memory.stat` 的 locked/shmem 合理；或明确把 RSS/PSS 归入 monitor/PSS，release 只做零资源。

### B12（low/runtime）deploy 的 origin 精确 `.git` 与 branch 名需实际一致

- **位置**：`deploy_control13.py:7-12`。
- **实际证据**：代码要求 `origin == https://github.com/BoyJiang2/serverlessllm-glm51-fp8.git`，而 `USER-OBJECTIVE` 写的是无 `.git` 的 URL；branch 要求精确 `experiment/glm51-fp8-takeover-20261025`。若实际 `git remote get-url origin`/branch 不同，deploy 会 fail-closed。
- **影响**：不是错误通过，而是运行期可能误阻塞；需在启动前记录实际 origin/branch。
- **最小验证**：`git remote get-url origin`、`git branch --show-current`；与代码/任务约定对比；如需兼容无 `.git`，显式规范化。

---

## 四、新 cache 控制的独立结论

**已核验的合理部分：**

- **148 输入**：`cache-inputs13.json` 142 official shards + 6 metadata（config、generation_config、tokenizer_config、tokenizer.json、chat_template.jinja、model.safetensors.index.json）；`cache_contract13.py:7-19` 检查 148 唯一、142 weights 与 `base.glob('model-*.safetensors')` 闭包、非 symlink、大小、6 metadata 的 sha256/git_blob_sha1。权重 `expected_sha256` 明确标为 LFS 身份，不是 fresh payload hash。
- **byte-exact helper**：`cache_prepare_v6.py`/`v8.py`/`cache_repair_v9.py` 的哈希与父 manifest/`runtime/historical-v11` 一致；`seal_candidate13.py:16-20` 记录 provenance。
- **控制参数**：`cache_state_control13.py:25-26` 只允许 `hot/safetensors_inputs/g1hot13`；初始 `prepare_hot_placement` 断言全 148 文件 `resident_pages==0`；`read_hot` 8 workers；`wait_read_idle`；最终 full 148 mincore 100%；`repair_hot` 最多 2 次；`cache_state` 的 total 与 bootstrap `timeout=600`；`MARGIN=64GiB`；`pgsteal` 仅 `numa_receipt`/summary 诊断，无阈值。
- **边界诚实**：`cache_state` summary `scope='Linux host page cache for exactly listed files; not hardware/storage/CUDA/JIT caches'`；PLAN 明确 cache 控制不代替四次 native lifecycle，也不声称之后 lifecycle 缓存等价。这一点成立。

**仍需修正：** B03（NUMA proxy/Store node）、B04（accept 未验 initial/repair/read-idle）、B08（初始 bytes/eviction 字段）、B09（LFS 身份未交叉核对）。此外 **B10** 的 per-cycle idle60 是删除后 60s 的验收缺口，和 cache 控制同属 G1 证据完整性。

---

## 五、历史终止证据与覆盖缺口

- **实际记录**：`CURRENT-BOUNDARY.md`；`target-journal-20261025T0456-0505.json` 的 6 条正确 boot/CID 记录，其中 dockerd PID 6247 在 `05:03:15.681605979Z` 记录目标容器 “failed to exit within 1m30s of signal 15 - using the force”，`FinishedAt 05:03:16.289423094Z`；`shim disconnected` 晚 13ms。`kernel-v3` 用 `-k` 成功，exit0、32 retained、0 target/pressure；旧的 `--kernel` exit1 是 collector flag 错误，不是权限/无 OOM 证明。
- **机制层**：Docker stop/force 已有直接 daemon 记录；**上游 stop requester、请求时间、原因仍未知**。旧 137/ID/结果不改判；**不归因 Ray、OOM、其他用户或平台**。`REPORT-TO-MAIN` 仍保留 leaf/祖先/全局覆盖缺口；`OOMKilled=false` 不能单独排除 OOM。
- **对 R1 准入的影响**：这足以支持 bounded G1 的观测，但**不足以**在任何报告中写“external SIGKILL/OOM 已排除”或“historical 137 root cause solved”。`CURRENT-BOUNDARY` 的表述（Docker stop/force observed; upstream unknown）是正确边界。
- **是否需要新的最小独立非计时信号探针**：**R1c 不需要新增 signal/BPF/global Docker 探针。** 现有 dockerd 记录已把机制层推进到 stop/force；缺口是历史 API caller/原因，新探针无法回溯该 caller，且会引入 root/global trace 风险和用户禁止的改动。G1 需要的 own Docker events、kernel `-k` observer、cgroup memory.events、NVML/monitor 已在候选中；guard probe 是清理准入探针，不是信号探针。若未来实际 G1 再现 exit137，可在 **target TGID/cgroup 过滤、独立 deadline、最小 untimed** 范围内再论证 signal_generate/wait-status，但不属于当前 R1c 准入条件。

---

## 六、R1c 准入结论与当前资源/授权

- **R1c 不批准**：B01（deploy pyc）是操作路径阻断；B02（service 预算）是预算不自洽；B03/B04 是 cache 控制；B05/B10 是 monitor/idle60 的验收完整性。修正后可重新提交 R1c 静态复审。
- **不是 blocked**：直接授权和三条预约仍在；当前 GPU 被其他作业占用是运行期事实，不是代码不可修。旧 02:50 v6 guard 保留；新 08:50 guard 尚未安装；在真实释放后必须重新核验 GPU/NUMA/窗口/预算，不假设 02:50 释放，不终止他人。
- **不签 R2**：`torch_dtype=bfloat16`、`placement_config.memory_pool=[]`、FP8/scale/8rank、cache 对后续 lifecycles 的等价性、正式计时/统计均未审。下一正式项仍应是 `store_pool_hit_03`，但需 G1/R2/gate 后。
- **不重置**：40 IDs、9 终态（5 success/4 failed）、31 unstarted、金标、旧失败均不变；`accept` 的 9 terminal 哈希/31 unstarted 检查保留。

---

## 七、运行前置条件（均未通过，不得伪造）

1. 当前 GPU 被其他作业占用；仅 CPU 工作；不运行 Ray/大池/全量 cache；不假设 02:50 释放；不终止他人。真实释放后重新核验 GPU UUID/占用、NUMA、窗口、预算。
2. 新 `install_guard13.sh` 未安装/未执行；旧 v6 02:50 仍在，新 08:50 独立 guard 未安装；不允许遗留无守护进程；正式原 run 仍需另行守护交接。
3. 新 probe/sentinel/Result/ExecMainStatus/probe_empty/private proof 未实际执行；`snapshot_root13` 未执行。
4. 新 `install_observers13.sh`/monitor 未执行；无 `observer-registration13.json`、`monitor-v3-ready.json`、`resource-monitor-v12.jsonl`。
5. cache 控制未执行；无 `cache_g1hot13/summary.json`、initial/eviction/repair 证据；真实 148 文件/756GB cache、8 readers、600s、64GiB、2 repairs 未验证。
6. 四次 native lifecycle、PSS、60s idle、16 rank 记录、8 rank 权重审计未执行；R2 未通过。
7. `release60-ray13a/ray13b/allstopped`、`root-evidence-index13.json`、`G1-acceptance.json` 未产生。
8. 历史 137 仍是 failed；dockerd stop/force 是历史机制证据，不解释未来 G1；上游 caller 仍 unknown；不写“OOM 绝对排除”。
9. 9 terminal/31 unstarted 未变；zero formal consumed；下一项 `store_pool_hit_03` 仍需 G1/R2/gate。
10. 25 本地测试、Python compile、3 个 `bash -n` 仅证明本地静态检查；不证明 guard/probe/G1/8rank/R2。

---

## 八、局限

- 无 shell，不能独立重算全部 SHA256；绑定调用给出的 `code_commit=b5b488...` 与 `manifest_sha256=2feefa...`，以实际文件/清单/父 manifest/审计交叉核验。
- 实际读取了 candidate13c 的关键源码、diff、模板、测试和证据；未逐行复算 1,142 父文件，未重哈希 756GB 权重，未运行 cache/G1/8rank/R2。
- `control-input-manifest` 为 178 文件行；实际目录有 188 文件，其中 8 个 `__pycache__/*.pyc` 是 B01 的直接证据。
- 未访问 qjhs003 或其他节点；未读凭据；未修改文件；未派子代理。
- 历史证据读取了 `CURRENT-BOUNDARY`、两个 target-journal、joint-review receipt、daemon final/addendum/report；未重跑调查。
- R2/FP8/正式计时/统计/完整 gate 未审；R1c 通过不等于 R2/正式准入。

---

## 九、机器可读 JSON

```json
{
  "stage": "R1c",
  "verdict": "changes_required",
  "code_commit": "b5b48807fe04251777c324d52f9e015c5d05aad5",
  "manifest_sha256": "2feefa1e9d74412084fd71cb5b412f9a9f95d6181ab983d95d5c29550e31eb56",
  "scope": "R1c static review of stability, window guard, identity, cleanup, forensics, cache control and historical termination evidence; R2 and formal timing not reviewed",
  "r2_status": "not_reviewed_not_approved",
  "formal_timing_status": "not_reviewed_not_approved",
  "blocking_findings": ["R1c-B01", "R1c-B02", "R1c-B03", "R1c-B04", "R1c-B05", "R1c-B10"],
  "prior_findings": {
    "R1c-N01": "withdrawn: actual 18/21 store.log prefixes contain the 716GB marker; 3 are aborted before pool completion; bootstrap assertion is supported",
    "R1c-N02": "resolved: sentinel, Result, ExecMainStatus, SubState, probe_empty, failed=0, archived proof",
    "R1c-N03": "resolved: root-evidence-private13, snapshot_root13 index, accept root-index binding",
    "R1c-N04": "resolved: accept rehashes inputs and binds deployment/code/boot/pool/affinity/release/monitor/root index",
    "R1c-N05": "resolved: deploy verifies origin, experiment branch, reviewed commit ancestor and candidate13c source diff",
    "R1c-N06": "clarified: empty nested bind mountpoint is required; no frozen payload write; must remain empty",
    "R1c-N07": "partial: 120s margin fixed in launcher/supervisor, but bootstrap budget check is inconsistent; see B02",
    "R1c-N08": "withdrawn: original contract is exact token/text/finish plus finite logprobs; no float magnitude equality",
    "R1c-N09": "resolved: 4GiB producer cap and 4MiB streaming row cap; no full-log in-memory parse",
    "R1c-N10": "withdrawn: StoreManager connects worker-address:8073; loopback would break native cross-container routing",
    "R1c-N11": "resolved: optimize checks on operational entries; 16-entry negative test",
    "R1c-N12": "resolved: mutated-head negative test replaces self-comparison",
    "R1c-N13": "resolved: rank counts read from pinned native-weight-contract",
    "R1c-N14": "resolved: copied native-home destination hashes match source"
  },
  "findings": [
    {"id": "R1c-B01", "severity": "high", "status": "changes_required", "title": "deploy actual includes __pycache__; actual!=allowed", "location": "seal_candidate13.py:37-38; deploy_control13.py:20-22; controls/candidate13c/__pycache__", "evidence": "control-input-manifest has 178 rows; actual directory has 188 files; 8 are __pycache__/*.cpython-313.pyc; deploy actual includes them but allowed does not", "impact": "deploy fails before copying; G1 cannot deploy", "verification": "find controls/candidate13c -type d -name __pycache__; run deploy; exclude pycache or remove it"},
    {"id": "R1c-B02", "severity": "medium_high", "status": "changes_required", "title": "service budget 3600 vs bootstrap >3720", "location": "budget_control13.py:2-8; launch_control13.py:43-44; supervise_control13.py:14-15; bootstrap_control13.py:75-77; test_accept_control13.py:34-38", "evidence": "require_budget service13a 3600 returns 3420; bootstrap budget=deadline-now-120 and assert budget>3600 requires remaining>3720", "impact": "service start in [3600,3720) passes launcher/supervisor but fails bootstrap after container creation", "verification": "test 3600/3719/3720; align bootstrap to budget>3300 or require 3720"},
    {"id": "R1c-B03", "severity": "medium", "status": "changes_required", "title": "cache NUMA capacity proxy may choose Store-pool node", "location": "cache_prepare_v8.py:23-33,63-80; cache_state_control13.py:40-45; store_numa_exec_v13.py:52-60; accept_control13.py:116-119", "evidence": "choose_node uses MemFree+FilePages; Store 716GiB pinned on node0; cache about 704GiB plus 64GiB margin; accept does not compare cache hot_placement.node with store-numa node", "impact": "cache may be placed on node0 with the 716GiB Store, risking incomplete mincore/OOM; fail-closed but preventable", "verification": "inspect hot-placement.node vs store-numa-launch.node; require different or use MemAvailable/exclude Store node"},
    {"id": "R1c-B04", "severity": "medium", "status": "changes_required", "title": "cache accept does not verify initial-scan/repair/read-idle", "location": "accept_control13.py:49-73; cache_state_control13.py:62-118; cache_repair_v9.py:13-66", "evidence": "validate_cache checks final summary files and eviction only; repair_history, initial-scan, preparation.jsonl bytes_read and read_idle_receipt are written but not accepted", "impact": "repair correctness and initial/read-idle proof are not independently verified", "verification": "accept each repair selection/reads/summary and initial-scan; verify bytes_read and read-idle"},
    {"id": "R1c-B05", "severity": "medium", "status": "changes_required", "title": "monitor gap uses monotonic_ns, not begin/end", "location": "monitor_control13.py:33,69-81; accept_control13.py:15-35; CURRENT-BOUNDARY.md:7", "evidence": "rows carry sample_begin_ns/monotonic_ns/sample_end_ns; accept computes max gap only from monotonic_ns and does not check sample duration or begin-to-begin", "impact": "a slow sample's own duration may not be bounded; historical 33.8s gap motivation is not fully closed by the metric", "verification": "accept sample_end-sample_begin and begin-to-begin; negative slow-sample test"},
    {"id": "R1c-B06", "severity": "low_medium", "status": "changes_required", "title": "ancestor cgroup check excludes root and ignores memory.high", "location": "preflight_control13.py:50-56", "evidence": "loop stops before /sys/fs/cgroup; memory.high and memory.events read but only memory.max asserted", "impact": "outer root/high limit could constrain the 1700G budget without preflight failure", "verification": "include root, assert memory.max and numeric memory.high; record swap.max"},
    {"id": "R1c-B07", "severity": "low", "status": "changes_required", "title": "seal rewrites window but leaves overlay_created_utc stale", "location": "seal_candidate13.py:10-14; run-manifest.json:1462,1472,1493; resource-window-extension-v3.json:5-6", "evidence": "seal updates run-manifest status and window original hash to 15a0e5... at 17:23 but overlay_created_utc remains 15:48:45Z", "impact": "overlay claims a creation time before the manifest bytes it binds; F14 re-opened", "verification": "set overlay_created_utc at window write; or freeze manifest before window"},
    {"id": "R1c-B08", "severity": "low", "status": "changes_required", "title": "cache initial read bytes and hot-placement final field not asserted", "location": "cache_state_control13.py:43-46,80-87; cache_prepare_v8.py:75-80,98-99", "evidence": "repair asserts bytes_read==size but initial read_hot does not; hot-placement.json written before eviction_resident_pages is set", "impact": "initial read completeness relies only on later mincore; hot-placement file lacks final eviction field", "verification": "assert initial bytes_read==size; write hot-placement after eviction or rely only on summary"},
    {"id": "R1c-B09", "severity": "low", "status": "changes_required", "title": "cache LFS expected_sha256 not cross-checked", "location": "cache_contract13.py:11-19; cache-inputs13.json; run-manifest.json; checkpoint/VERIFIED.json", "evidence": "weight expected_sha256 are frozen LFS identity but cache_contract checks only closure/size; manifest is hash-pinned", "impact": "static consistency with official LFS list is not independently checked; later 8rank audit is separate", "verification": "cross-check 142 expected_sha256 against checkpoint VERIFIED; no fresh payload hash"},
    {"id": "R1c-B10", "severity": "medium", "status": "changes_required", "title": "accept does not read per-cycle idle60 rows", "location": "validation_control13.py:108-129; accept_control13.py:100-110", "evidence": "validation writes cycle{1,2}-idle60.jsonl 61 rows; accept checks PSS and model-result but not idle60", "impact": "per-cycle delete 60s observation is not accepted field-by-field", "verification": "accept 61 rows/60s/GPU/pids/memory/shm per cycle; negative test"},
    {"id": "R1c-B11", "severity": "low", "status": "changes_required", "title": "release content RSS/PSS/locked/shared not accepted", "location": "accept_control13.py:37-47; release_control13.py", "evidence": "release records processes/status/smaps and cgroup memory/cpu, but validate_release checks only 61/60s, own containers, pids, GPU", "impact": "release field-level completeness is weaker than claimed", "verification": "assert no residual uid2147 processes and locked/shmem; or explicitly scope RSS/PSS to monitor/PSS"},
    {"id": "R1c-B12", "severity": "low", "status": "runtime_condition", "title": "deploy origin exact .git and branch must match", "location": "deploy_control13.py:7-12", "evidence": "origin must equal .git URL; branch must equal experiment/glm51-fp8-takeover-20261025", "impact": "wrong actual remote/branch fails closed; user URL is written without .git", "verification": "git remote get-url origin; git branch --show-current; compare/normalize"}
  ],
  "runtime_conditions": [
    "Current GPUs are occupied by another job; only CPU work; no Ray/pool/full-cache starting; do not assume 02:50 release; do not terminate others.",
    "New install_guard13/probe not executed; old v6 guard remains 02:50; new 08:50 independent guard not installed; no unguarded process; original-run handover still separate.",
    "New sentinel/probe Result/ExecMainStatus/SubState/probe_empty/private proof and snapshot_root13 not executed.",
    "New install_observers13/monitor not executed; no observer-registration13.json, monitor-v3-ready.json or resource-monitor-v12.jsonl.",
    "Real cache control not executed; no cache_g1hot13 summary/initial/eviction/repair; 148 inputs/756GB/8 readers/600s/64GiB/2 repairs unverified.",
    "Four native Lifecycles, PSS, 16 rank records and 8-rank weight audit not executed; R2 not passed.",
    "release60-ray13a/ray13b/allstopped, root-evidence-index13.json and G1-acceptance.json not produced.",
    "Historical exit137 remains failed; dockerd stop/force is historical mechanism evidence; upstream caller/reason unknown; no absolute OOM exclusion; no Ray attribution.",
    "9 terminal and 31 unstarted formal IDs unchanged; zero formal consumed; store_pool_hit_03 remains next after G1/R2/gate.",
    "25 local tests, Python compile and 3 bash -n only local static checks; not guard/probe/G1/8-rank/R2."
  ],
  "limitations": [
    "No shell; cannot recompute all SHA256; bound to code_commit b5b488... and manifest_sha256 2feefa...; cross-checked actual files, manifest, parent manifest and audit.",
    "Read key candidate13c source, diffs, templates, tests and evidence; did not rehash all 1,142 parent files or 756GB weights; did not run cache/G1/8-rank/R2.",
    "control-input-manifest has 178 rows; actual directory has 188 files including 8 __pycache__ pyc files.",
    "Did not access qjhs003 or other nodes; no remote commands; no file modification; no credentials; no subagents.",
    "Historical evidence read from CURRENT-BOUNDARY, two target-journal files, joint receipt and daemon reports; did not re-run investigation.",
    "R2/FP8 correctness, bfloat16 semantics, memory_pool=[] lifecycle semantics, formal timing and statistics not reviewed.",
    "R1c changes_required does not approve R2 or formal G1; G1 success would not retroactively solve historical exit137.",
    "GPU occupancy is a runtime hold, not a code verdict; revalidate occupancy/NUMA/window/budget after actual release."
  ],
  "global_gap_impact": "Global Docker daemon/audit journals are not continuously exported; this permits bounded own-cgroup/GPU observation but does not exclude a host-global actor or OOM. It blocks any external-SIGKILL/OOM-exclusion or historical-root-cause-solved claim; it does not block a bounded G1 report that keeps cause unknown, provided own kernel/Docker/cgroup evidence is archived.",
  "signal_probe_recommendation": "No new signal/BPF/global Docker probe is required for R1c. The historical dockerd stop/force is already recorded and a new signal probe cannot recover the upstream caller. Any future probe should be target-TGID/cgroup-filtered, untimed, independently deadline-bounded, and only after the R1 controls."
}
```
