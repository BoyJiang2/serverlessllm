# R1e 独立只读交叉审阅判定

**结论：`changes_required`（不批准 R1e）。**

不是 `blocked`：当前 `bk-20261026T0248-fd6bea51`（10–18，17:50 guard）仍在有效预约内，父资产、候选包和 1,142 文件审计仍在；问题可在本地修正后复审。也不是 `approved`：新 guard/probe、monitor、真实 cache、G1、8 rank 审计均未执行，且实际 GPU helper/60s 验收仍有静态代码缺口。

绑定：

- `code_commit`: `ebc42c805b439c2b14a880b0c8e2d34ebaedc773`
- `control-input-manifest` SHA256: `706c0ce691eaf83911f79c354f0f7948c5b5c6ff89b9e7d3a0420a517a99a99a`
- 审阅对象：`controls/candidate13e`；`13c` 和归档 `13d` 保留，未作为本版审批。
- 方式：只读实际源码、diff、模板、测试和证据；未执行 shell/SSH/资源控制，未修改文件，未读凭据，未派代理。无 shell 不能独立重算全部 SHA；关键一致性来自实际文件、清单、`current-*` 证据和父审计。

---

## 一、R1c B01–B12 逐项核验

| Finding | R1e 结论 | 实际依据 |
|---|---|---|
| B01 deploy `__pycache__` | **已解决** | `package_inventory13.py:2-11` 的 `reviewed_files` 明确跳过 `__pycache__`；`deploy_control13.py:20-22` 使用它；`:42-50` 只通过 staging 复制 `allowed`；本地 `__pycache__` 不再进入 `actual`/SCP。`test_evidence13.py:8-15` 有 bytecode 排除和 unexpected source 拒绝测试。 |
| B02 service 预算 3600/3720 | **已解决** | `bootstrap_control13.py:75-77` 改为 `require_budget(label,deadline-time.time())` 和 `signal.alarm(3300)`，与 `budget_control13.py:2-8`、`launch:43-44`、`supervise:11-13` 同一契约；`control_watchdog13.py:4-34` 另设 25s TERM/KILL reserve；`test_watchdog13.py:37-46` 用 AST 抽取 bootstrap 的实际 `require_budget` 调用，3600/3720/3721 均通过、3599 拒绝；`:29-36` 覆盖 late exit、term delay、ignore term/kill、不可杀。 |
| B03 cache NUMA/Store node | **已解决** | `cache_state_control13.py:42-47` 读取 `store-numa-launch.json`，断言 `store_placement['node']=='node0'`，从 `numa_before['node_meminfo']` 排除 node0，只把 node1 交给 `prepare_hot_placement`；`:45-47` 断言 `placement['node']=='node1'`、reader CPUs 与 Store target CPUs 不相交，并写 `excluded_store_cpu_node`；`accept_control13.py:82-85` 校验 placement、node1、node0、CPU 不相交、required/margin/capacity。 |
| B04 cache accept initial/repair/read-idle | **已解决** | `accept_control13.py:70-133` 现在校验 `initial-scan.json`、`preparation.jsonl`/`initial-reads.json`、`hot-placement-final.json`、每轮 `repair_N/selection.json`/`reads.json`/`summary.json`、`read_idle_receipt`、final full vectors、eviction 0；`cache_state_control13.py:48-53,80-99` 记录 `eviction_resident_pages`、initial bytes、final placement。`test_cache_control13.py` 有 initial short、repair chain、read-idle 非零等负/正测试。 |
| B05 monitor gap/begin-end | **已解决** | `monitor_control13.py:33,82,99` 记录 `sample_begin_ns`/`sample_end_ns`；`accept_control13.py:37-47` 要求 `begin<=now<=end`、`end-begin<=5s`、begin-to-begin<=5s、max_gap<=5；`test_accept_control13.py:34-36` 有 slow sample 拒绝。 |
| B06 ancestor root/high/swap | **部分解决** | `preflight_control13.py:56-63` 现在包含 root，读取 `memory.max`/`high`/`swap.max`/`events`；`validate_ancestor_values` 对 `memory.max`/`high` 断言，root 缺接口显式允许。**但 `memory.swap.max` 只读不校验**，见 R1e-B03。 |
| B07 overlay 时间 | **已解决** | `seal_candidate13.py:13-17` 保留 `overlay_initial_draft_created_utc`，设置 `overlay_created_utc=overlay_sealed_utc=now`；实际 `resource-window-extension-v3.json:6-7` 为 `03:13:44Z`，run-manifest 为 `03:12:33Z`，control manifest 为 `03:13:58Z`；`original_manifest_sha256` 与 run-manifest 当前 `0241c8...` 一致。F14 不再回退。 |
| B08 cache initial bytes/hot-placement | **已解决** | `cache_state_control13.py:48-53` 断言 `bytes_read==size`；`:48-49` 在 `prepare_hot_placement` 返回后写 `hot-placement-final.json`，其中包含 `eviction_resident_pages`；`accept:82-85` 用 final 文件。 |
| B09 cache LFS 双证据 | **已解决** | `cache_contract13.py:10-19` 读 `evidence/run-manifest.json` 与 `evidence/checkpoint-VERIFIED.json`，对 142 个权重比较 official `size/lfs.sha256` 与 historical `[bytes,sha,True]`；`cache-inputs13.json` 的 `source_manifest_sha256`/`verified_manifest_sha256` 钉住。 |
| B10 native idle60 61 行 | **已解决** | `validation_control13.py:132-145` 写 61 行 `cycle{1,2}-idle60.jsonl`，含 `host_gpu_snapshot`、`gpu`、`gpu_processes`、`pids_current`、`memory_stat`、`cpu_stat`；`accept_control13.py:135-147` 的 `validate_native_idle` 校验 61 行、60s、helper、资源字段、final GPU baseline。 |
| B11 release RSS/PSS/locked/shared | **已解决** | `release_control13.py:16-34` 记录 processes/status/stat/smaps、cgroup memory.stat；`accept_control13.py:52-68` 校验 61/60s、`processes` 类型、`anon/file/shmem/unevictable`、`VmRSS/VmLck/Threads` 与 `smaps_rollup`/`PSS_unavailable`、无 GPU 进程、allstopped 零容器/pids/GPU baseline。 |
| B12 deploy origin/branch | **运行期条件** | `deploy_control13.py:7-9` 仍要求 origin `...fp8.git` 和精确 experiment branch；实际 remote/branch 必须在部署前核对。不是静态必败，见运行条件。 |

---

## 二、R1e 剩余真实代码缺口

### R1e-B01（high）`allow_engine=True` 允许任意同 run engine 在 delete 后 59 秒保留

- **位置**：`gpu_identity13.py:14-18`；`validation_control13.py:135-147`（`:139`）；`accept_control13.py:135-147`（`:139`）；`test_gpu_identity13.py:16-18`；`test_evidence13.py:25-31`。
- **实际证据**：
  - `require_helper_only(..., allow_engine=False)` 才要求 `set(snapshot['gpu_pids']) <= {helper['host_pid']}`；`allow_engine=True` 时该检查被跳过，只保留 `foreign_gpu_pids` 为空和 helper 注册。
  - `validation_control13.py:139` 和 `accept_control13.py:139` 都调用 `validate_native_idle`，其中 `allow_engine=row['second']<60`：**第 0–59 秒允许任何同 run GPU engine**，只有第 60 秒才要求仅 helper。
  - `test_gpu_identity13.py:16-18` 只测默认 `allow_engine=False`；`test_evidence13.py:29-31` 只把 engine 放在最后一行；没有 `allow_engine=True` 且同 run engine 的负测试。
  - `persistent-helper-counterexample.json` 明确只证明“历史 helper 形状”，不证明新 runtime；当前代码却把任意同 run engine 当作允许项。
- **影响**：实例 delete 后的 60s 观察中，模型 engine 可以在前 59 秒继续持有 GPU context；accept 仍可能通过。这与用户“**仅允许当前已登记 helper**、不准把任意同 run engine 当 helper”直接冲突；whole-control exit 的 `release60-allstopped` 仍要求空 GPU PID，但 per-cycle 的 60s 证明被削弱。
- **最小修正/验证**：
  1. 把 `validate_native_idle` 的 `allow_engine` 设为 **False for all 61 rows**；若确实需要 engine drain，另立有界 transition receipt，明确 engine 身份、deadline 和“最终必须 helper-only”，不能复用 `allow_engine` 静默放行。
  2. 增加测试：`second=0` 和 `second=59` 放入同 run engine，`validate_native_idle` 必须失败；`allow_engine=True` 的同 run engine 也必须失败（如果保留该参数，至少不得用于 60s admission）。

### R1e-B02（high/medium）native idle 行的原始 `gpu_processes` 未与 host monitor/`gpu_pids` 交叉绑定

- **位置**：`validation_control13.py:132-145`；`accept_control13.py:135-147`；`test_evidence13.py:25`；`test_gpu_identity13.py:35-45`。
- **实际证据**：
  - `validation_control13.py:135-145` 的 idle 行同时写 `gpu_processes`（本进程直接 `nvidia-smi --query-compute-apps`）和 `host_gpu_snapshot`（monitor ready 的 `gpu_pids`/helper）。
  - `validate_native_idle` 只用 `row['host_gpu_snapshot']` 调 `require_helper_only`，**完全不解析/比较 `row['gpu_processes']`**；`accept_control13.py` 也没有把 idle 行 raw NVML PIDs 与 `host_gpu_snapshot['gpu_pids']` 对照。
  - `test_evidence13.py:25` 的 `rows()` 写 `gpu_processes='1124, python, 322 MiB'`，而 `host_gpu_snapshot` 的 `gpu_pids=[9001]`；`test_valid_persistent_helper` 仍通过，证明当前验收允许“raw NVML 显示 1124、monitor 快照只显示 9001”的不一致。
  - `test_gpu_identity13.py:35-45` 的 `monitor_summary` 只交叉校验 **host monitor 行** 的 `gpu_processes` 与 `gpu_pids`；native idle 行的第二份 raw NVML 没有同等校验。
- **影响**：`host_gpu_snapshot` 可以 helper-only，而同一 idle 行的直接 `gpu_processes` 显示 engine；accept 仍通过。用户要求“accept需独立关联 host monitor 原始行”，当前只对 monitor 行成立，对 validation idle 行不成立。这不是 whole-exit 失败，但 per-cycle 60s 的 GPU 释放证明不完整。
- **最小修正/验证**：
  1. 在 `validate_native_idle` 中解析 `row['gpu_processes']` 的 PIDs，要求 `set(pids) == set(row['host_gpu_snapshot']['gpu_pids'])`（或明确允许的 helper 子集），并拒绝任何非 helper PID；对 final 行要求空/仅 helper。
  2. 更新 `test_evidence13.py` fixture，使 `gpu_processes` 与 `gpu_pids` 一致；增加“raw NVML 有 engine、snapshot helper-only”的负测试。
  3. accept 端可把该 raw PIDs 与 host monitor 行按 `last_sample_ns` 关联，或至少记录/校验两者一致性。

### R1e-B03（low/medium）ancestor `memory.swap.max` 只读不校验；root 缺接口被显式放过

- **位置**：`preflight_control13.py:13-17,56-63`；`test_evidence13.py:16-20`。
- **实际证据**：`values` 读取 `memory.max`/`memory.high`/`memory.swap.max`/`memory.events`；`validate_ancestor_values` 只遍历 `('memory.max','memory.high')`，**不校验 `memory.swap.max`**。`validate_ancestor_values` 对 root 的 `None` 显式 `require(is_root,...)`，即 root 缺 `memory.max`/`high` 也通过；`test_ancestor_root_missing_interface_is_explicit` 固化了这一行为。用户要求“全部祖先 memory/high/swap”，当前 swap 和 root 缺失未被作为 fail-closed 条件。
- **影响**：祖先 swap 限制或 root 接口缺失不会阻止 preflight；当前 host `SwapTotal=0`、slice `MemorySwapMax=0` 降低了实际风险，但“全部祖先 memory/high/swap”的声明不完全成立。
- **最小修正/验证**：`validate_ancestor_values` 增加 `memory.swap.max`：若 host `SwapTotal>0` 则要求 0；若 `SwapTotal=0` 允许 `max` 但记录；root 的 `memory.max`/`high` 缺失应要求显式 fallback（例如 `SwapTotal=0` + 记录），不能静默通过。增加 `memory.swap.max>0` 和 root 缺接口的负测试。

### R1e-B04（runtime）deploy origin/branch 与实际 remote/branch 需一致

- **位置**：`deploy_control13.py:7-9`。
- **实际证据**：代码要求 origin `https://github.com/BoyJiang2/serverlessllm-glm51-fp8.git` 和 branch `experiment/glm51-fp8-takeover-20261025`。`USER-OBJECTIVE` 的 URL 写为无 `.git` 形式；实际 `git remote get-url origin`/`git branch --show-current` 必须匹配，否则 fail-closed。
- **影响**：不是错误通过，而是部署可能被误阻塞；属于运行前置条件。
- **最小验证**：部署前记录 `git remote get-url origin`、`git branch --show-current`、`git rev-parse HEAD`；如实际 URL 无 `.git`，显式规范化或放宽为等价 URL。

### R1e-B05（runtime）current-* 快照时间早于 sealed candidate，运行前必须刷新

- **位置**：`evidence/current-resource.json`（`02:51:50Z`）；`evidence/current-source-audit.json`（`02:56:01Z`）；`evidence/current-booking.json`（`02:51:51Z`）；`run-manifest.json:1472`（`03:12:33Z`）；`resource-window-extension-v3.json:6`（`03:13:44Z`）。
- **实际证据**：`current-*` 是 sealing 前的快照；`run-manifest`/overlay 在其后。`current-source-audit` 1,142 文件、9 terminal/31 unstarted、`old_guard_service` 只证明旧查询，不证明新 guard。`current-resource` 的 `utc 02:51` 不能作为运行时 GPU/NUMA/内存/booking 的通过证明。
- **影响**：静态审阅可接受快照；实际 G1 前必须重新核验 GPU 占用/UUID、NUMA、内存、booking/time、guard。不是代码必然失败。
- **最小验证**：运行前重新采集 `current-*`，确认 8×143771MiB、同 boot/UUID、10–18 booking、17:50 guard、NUMA node0/node1、`SwapTotal=0`、`MemAvailable` 和 `/data` 可用空间；过期快照不得作为 G1 准入。

### R1e-B06（low）`static-tests-wip.log` 是额外本地 WIP 日志，不是 58 测试最终证据

- **位置**：`static-tests.log`（58 tests）；`static-tests-wip.log`（53 tests）；`seal_candidate13.py:30-32`；`control-input-manifest` 同时收录两者。
- **实际证据**：最终 `static-tests.log` 为 58 tests OK；`static-tests-wip.log` 为 53 tests OK。两者都是本地 CPU 合成测试，不是 G1/R1 运行证据。WIP 日志进入 manifest 不影响 deploy（`package_inventory` 包含 manifest 行），但审阅包中保留两个不同测试数可能造成混淆。
- **影响**：低；不能把 58/53 测试当作 G1 或 R2 证据。用户已明确 58 CPU 测试不是运行证据。
- **最小修正/验证**：可把 WIP 日志移出最终包或注明 superseded；以 `static-tests.log` 为最终本地检查记录。

---

## 三、GPU helper 身份要求的独立结论

**已实现且正确的部分：**

- `gpu_identity13.store_identity` 用 `process['pid']`（host PID）+ `status.NSpid[-1]`（container PID）+ `pid_namespace` + `start_ticks` + `cgroup==service.cgroup` + `Uid=2147` + `boot_id` 匹配 Store helper；`matches` 多于 1 个时 `ambiguous` 失败。
- `store_numa_exec_v13.py:59-63` 在 exec 前记录 `pid_namespace`/`start_ticks`/`boot_id`/node；`store-numa-launch.json` 原子写入；Store helper 的 host PID/NSpid 可由 monitor 的 `/proc` 行派生。
- `require_helper_binding` 比较 helper 与 `launch`/`service` 的 namespace、start、boot、container_id、cgroup、uid。
- `accept` 的 `monitor_summary` 用 host monitor 原始行重新派生 `store_identity`，并比较 `registered_store_helper`、`gpu_pids`、`foreign_gpu_pids` 与 validation 的 ready 快照；`found_gpu==expected_gpu` 要求每个 validation 快照都在 host monitor 中存在。
- `persistent-helper-counterexample.json` 明确没有 host PID 映射，不作为 runtime 证据；当前代码不使用历史 1243 字面量。

**未满足的用户要求：**

1. `allow_engine=True` 使第 0–59 秒不要求 helper-only；见 R1e-B01。
2. idle 行原始 `gpu_processes` 未与 host monitor/`gpu_pids` 交叉；见 R1e-B02。
3. whole-control exit 的 `release60-allstopped` 仍要求无 GPU 进程，这一点是好的；但 per-cycle 60s 的“仅 helper”不成立。

---

## 四、cache 控制独立结论

- **148 输入**：`cache-inputs13.json` 142 weights + 6 metadata；`cache_contract13.py` 检查 148 唯一、142 与 `model-*.safetensors` 闭包、大小、6 metadata 哈希，以及 142 个 LFS `expected_sha256` 与 `evidence/run-manifest.json`、`checkpoint-VERIFIED.json` 的双证据。**B09 已解决。**
- **初始 0 / final full**：`cache_state_control13.py:42-47` 在 Store node0 之外只允许 node1；`prepare_hot_placement` 断言初始全 148 `resident_pages==0`；`read_hot` 8 线程；`read_idle`；final 全 148 mincore 100%；`repair_hot` 最多 2 次，总 600s；`accept` 对 initial/repair/final/eviction 全向量、SHA、pages、bytes、read-idle、placement 逐项校验。**B03/B04/B08 已解决。**
- **边界**：`summary` 明确只证明 Linux host page cache 的 148 文件，不证明后续 native lifecycle 缓存等价；PLAN 也明确 cache 控制不代替四次 lifecycle。`pgsteal` 仅诊断，无准入阈值；`placement_scope` 明确“reader CPU placement only; no physical-page NUMA guarantee”。这些边界诚实。
- **未执行**：真实 148 文件/756GB cache、8 readers、600s、64GiB、2 repairs 未运行；58 CPU 测试只验证合成闭包和 accept 逻辑；真实 cache 仍是运行条件。

---

## 五、历史终止证据与 R1 准入

- **实际记录**：`CURRENT-BOUNDARY.md`、`target-journal-20261025T0456-0505.json` 的 6 条正确 boot/CID 记录，dockerd 在 `05:03:15.681605979Z` 记录目标容器 signal15 后 90s 未退出、使用 force；`FinishedAt 05:03:16.289423094Z`；`kernel-v3` 用 `-k` exit0、32 retained、0 target/pressure。旧 `--kernel` exit1 是 collector flag 错误，不是权限/无 OOM 证明。
- **结论边界**：机制层是 Docker stop/force；上游 stop 请求人、请求时间、原因仍未知；旧 137/ID/结果不改判；不归因 Ray、OOM、其他用户或平台；不能写“external SIGKILL/OOM 已排除”。
- **是否新增最小信号探针**：**R1e 不需要新增 signal/BPF/global Docker 探针。** 现有 dockerd 记录已把机制层推进到 stop/force；缺口是历史上游 caller/原因，新探针无法回溯该 caller，且会引入 root/global trace 和用户禁止的改动。G1 需要的是 own Docker events、kernel `-k`、cgroup memory.events、NVML/monitor、guard probe；这些已在候选中。若未来 G1 再现 exit137，再在 target TGID/cgroup 过滤、独立 deadline、untimed 范围内论证。
- **current-* 快照**：`current-source-audit` 1,142 文件无 mismatch、9 terminal/31 unstarted、旧 guard 查询 `Result=success` 但不替代新 guard；`current-booking` 10–18；`current-resource` 02:51 快照。实际运行前必须刷新，不能把快照当 G1 通过。

---

## 六、R1e 准入结论与运行前置条件

- **R1e 不批准**：R1e-B01（allow_engine 59s）、R1e-B02（idle raw GPU PID 未绑定）是 GPU 释放/helper-only 的实质缺口；R1e-B03（ancestor swap/root 接口）是低/中缺口；R1e-B04/B05 是运行期条件；R1e-B06 是本地测试记录问题。
- **不是 blocked**：当前 booking 10–18、17:50 guard 窗口、qjhs003 身份/chassis、1,142 文件审计、9/31 ledger 均在；代码可在本地修正。GPU 实际占用/资源必须在运行前刷新，不得假设。
- **不签 R2**：FP8 正确性、`torch_dtype=bfloat16`、`placement_config.memory_pool=[]`、8 rank 权重审计、正式计时/统计/完整 gate 均未审。
- **不重置**：9 terminal（5 success/4 failure）、31 unstarted、金标、旧失败不变；下一正式 `store_pool_hit_03` 仅在真实 G1 → R2 → 正式 gate 后。
- **无 Node8 等待/共享锁**：`R1-RESPONSE` 明确 Node8 coordination 不再是前置；未部署共享锁。
- **legacy 137**：仅 dockerd 机制证据；上游 unknown；不改判、不归因 Ray。

**运行前置条件（均未通过）：**

1. 新 guard/probe/sentinel、root-private 证据、`snapshot_root13` 未执行；17:50 timer 未安装；旧 guard 查询不替代。
2. 新 `install_observers13`/monitor 未执行；无 `observer-registration13.json`、`monitor-v3-ready.json`、`resource-monitor-v12.jsonl`。
3. 真实 cache 未执行；无 `cache_g1hot13/summary.json`、initial/eviction/repair/read-idle；756GB/8/600s/64GiB/2 repairs 未验证。
4. 四次 native lifecycle、PSS、61 行 idle60、16 rank 记录、8 rank 审计未执行；R2 未通过。
5. `release60-ray13a/ray13b/allstopped`、`root-evidence-index13.json`、`G1-acceptance.json` 未产生。
6. 58 CPU 测试、Python compile、3 个 `bash -n` 仅是本地检查。
7. 运行前刷新 GPU 占用/UUID、NUMA、内存、booking/time、17:50 guard、`/data` 空间；当前 `current-*` 为 02:51/02:56 快照。
8. 历史 137 仍 failed；dockerd stop/force 已知；上游 unknown；不写 OOM 绝对排除。
9. 9 terminal/31 unstarted 不变；zero formal consumed；`store_pool_hit_03` 仍需 G1/R2/gate。
10. GPU 实际占用/资源释放后，才能进入 G1；不假设 02:50 或旧窗口。

---

## 七、局限

- 无 shell，不能独立重算全部 SHA256；绑定调用给出的 `code_commit=ebc42c...` 与 `manifest_sha256=706c0c...`，交叉核验了实际文件、清单、`current-*`、父 manifest 和历史证据。
- 读取了 candidate13e 的受影响源码、diff、模板、测试和证据；未逐行复算 1,142 父文件，未重哈希 756GB 权重，未运行 G1/R2。
- `current-resource` 为单行大 JSON，读取界面截断；GPU/NUMA/磁盘的完整值需在运行前重新采集，不把截断快照当通过。
- 未访问 qjhs003 或其他节点；未读凭据；未修改文件；未派代理。
- 历史证据读取了 `CURRENT-BOUNDARY`、两个 target-journal、joint receipt、daemon final/addendum/report；未重跑调查。
- 58 本地测试与 3 个 `bash -n` 不是 G1/R2 证据；GPU helper/60s 的真实行为未执行。
- R2/正式计时/统计/完整 gate 未审；R1e 通过不等于 R2 或正式 G1。

---

## 八、机器可读 JSON

```json
{
  "stage": "R1e",
  "verdict": "changes_required",
  "code_commit": "ebc42c805b439c2b14a880b0c8e2d34ebaedc773",
  "manifest_sha256": "706c0ce691eaf83911f79c354f0f7948c5b5c6ff89b9e7d3a0420a517a99a99a",
  "scope": "R1e static review of window guard, cleanup/forensics, cache control, native 60s/PSS/release and GPU helper identity; R2 and formal G1 not reviewed",
  "r2_status": "not_reviewed_not_approved",
  "g1_status": "not_run_not_approved",
  "blocking_findings": ["R1e-B01", "R1e-B02"],
  "prior_findings": {
    "R1c-B01": "resolved: package_inventory excludes __pycache__; deploy stages only allowed files",
    "R1c-B02": "resolved: bootstrap uses require_budget 330/3420+180; control_watchdog 25s reserve; boundary tests",
    "R1c-B03": "resolved: cache excludes Store node0, requires node1 and CPU disjoint; accept checks",
    "R1c-B04": "resolved: accept verifies initial scan, initial reads, repair selection/reads/summary, read-idle, final vectors",
    "R1c-B05": "resolved: monitor begin/end, duration and begin-to-begin enforced",
    "R1c-B06": "partial: root and memory.high included; memory.swap.max and root missing interface not fail-closed",
    "R1c-B07": "resolved: overlay_created/sealed after run-manifest; hashes match",
    "R1c-B08": "resolved: initial bytes and hot-placement-final checked",
    "R1c-B09": "resolved: LFS double evidence official+verified checked",
    "R1c-B10": "resolved: validate_native_idle reads 61 rows per cycle",
    "R1c-B11": "resolved: release processes/memory/VmRSS/VmLck/Threads/PSS checked",
    "R1c-B12": "runtime: exact origin .git and branch must be confirmed"
  },
  "findings": [
    {"id": "R1e-B01", "severity": "high", "status": "changes_required", "title": "allow_engine permits any same-run GPU engine for 59s after delete", "location": "gpu_identity13.py:14-18; validation_control13.py:135-147; accept_control13.py:135-147; test_gpu_identity13.py:16-18; test_evidence13.py:25-31", "evidence": "require_helper_only skips the gpu_pids subset check when allow_engine=True; validate_native_idle passes allow_engine=second<60; only second=60 is helper-only; tests only cover final row", "impact": "per-cycle 60s observation can pass with a model engine retaining GPU context for 59s, contrary to only-registered-helper requirement", "verification": "set allow_engine=False for all rows or define explicit bounded engine transition; add engine at second 0 and 59 negative tests"},
    {"id": "R1e-B02", "severity": "high_medium", "status": "changes_required", "title": "native idle raw gpu_processes not cross-bound to host monitor/gpu_pids", "location": "validation_control13.py:132-145; accept_control13.py:135-147; test_evidence13.py:25", "evidence": "idle rows write raw gpu_processes and host_gpu_snapshot; validate_native_idle only checks host_gpu_snapshot and ignores raw gpu_processes; test fixture has gpu_processes=1124 while gpu_pids=[9001] and passes", "impact": "raw NVML can show an engine while host monitor ready is helper-only; accept can pass an inconsistent per-cycle GPU receipt", "verification": "parse idle gpu_processes PIDs and require equality/subset with host_gpu_snapshot.gpu_pids and helper; update fixture and add negative test"},
    {"id": "R1e-B03", "severity": "low_medium", "status": "changes_required", "title": "ancestor memory.swap.max not validated; root missing interface allowed", "location": "preflight_control13.py:13-17,56-63; test_evidence13.py:16-20", "evidence": "values include memory.swap.max but validate_ancestor_values only loops memory.max/high; root None is explicitly accepted", "impact": "ancestor swap or root interface absence does not fail preflight; current SwapTotal=0 lowers risk but the all-ancestor swap claim is incomplete", "verification": "validate memory.swap.max and root interfaces; add tests for swap>0 and missing root interface"},
    {"id": "R1e-B04", "severity": "runtime_condition", "status": "runtime_condition", "title": "deploy origin .git and exact branch must match", "location": "deploy_control13.py:7-9", "evidence": "origin requires .git URL; branch exact experiment/glm51-fp8-takeover-20261025", "impact": "actual remote/branch mismatch fails closed; user URL is written without .git", "verification": "record git remote get-url origin and branch before deploy; normalize if needed"},
    {"id": "R1e-B05", "severity": "runtime_condition", "status": "runtime_condition", "title": "current-* snapshots predate sealed candidate and must be refreshed", "location": "evidence/current-resource.json; evidence/current-source-audit.json; evidence/current-booking.json; run-manifest.json:1472; resource-window-extension-v3.json:6", "evidence": "current-resource 02:51:50Z, current-source 02:56:01Z, current-booking 02:51:51Z; run-manifest 03:12:33Z, overlay 03:13:44Z", "impact": "snapshot-only evidence cannot be G1 admission; GPU/NUMA/memory/booking must be refreshed", "verification": "recollect current GPU/UUID/NUMA/MemAvailable/disk/booking/time before runtime"},
    {"id": "R1e-B06", "severity": "low", "status": "runtime_condition", "title": "static-tests-wip.log is an extra local WIP log", "location": "static-tests.log; static-tests-wip.log; seal_candidate13.py:30-32", "evidence": "final log 58 tests; WIP log 53 tests; both are CPU synthetic, not G1", "impact": "no runtime approval; possible confusion about final test count", "verification": "treat static-tests.log as final local evidence; remove or mark WIP superseded"}
  ],
  "runtime_conditions": [
    "New guard/probe/sentinel, root-private evidence and snapshot_root13 not executed; 17:50 timer not installed.",
    "New install_observers13/monitor not executed; no observer-registration13.json, monitor-v3-ready.json or resource-monitor-v12.jsonl.",
    "Real cache 148/756GB, initial 0, final 100%, 8 readers, 600s, 64GiB, 2 repairs not executed.",
    "Four native lifecycles, PSS, 61-row idle60, 16 rank records and 8-rank audit not executed; R2 not passed.",
    "release60-ray13a/ray13b/allstopped, root-evidence-index13.json and G1-acceptance.json not produced.",
    "58 CPU tests, Python compile and 3 bash -n are local checks only, not G1.",
    "Current GPU/NUMA/memory/booking/time must be refreshed; current-* are 02:51/02:56 snapshots; 10-18 booking and 17:50 guard not yet deployed.",
    "Historical exit137 remains failed; dockerd stop/force known; upstream caller/reason unknown; no absolute OOM exclusion; no Ray attribution.",
    "9 terminal and 31 unstarted formal IDs unchanged; zero formal consumed; store_pool_hit_03 after G1/R2/gate.",
    "No Node8 wait/shared lock; no new signal/BPF/global Docker probe required for R1e."
  ],
  "limitations": [
    "No shell; cannot recompute all SHA256; bound to code_commit ebc42c... and manifest_sha256 706c0c...; cross-checked actual files, manifest, current-* and parent audit.",
    "Read key candidate13e source, diffs, templates, tests and evidence; did not rehash all 1,142 parent files or 756GB weights; did not run G1/R2.",
    "current-resource is a single large JSON line; full GPU/NUMA/disk values must be refreshed at runtime.",
    "Did not access qjhs003 or other nodes; no remote commands; no file modification; no credentials; no agents.",
    "Historical evidence read from CURRENT-BOUNDARY, target-journal files, joint receipt and daemon reports; did not re-run investigation.",
    "R2/FP8 correctness, bfloat16 semantics, memory_pool=[] lifecycle semantics, formal timing and statistics not reviewed.",
    "R1e changes_required does not approve G1 or R2; G1 success would not solve historical exit137.",
    "GPU occupancy/resource status is runtime, not a code verdict; revalidate after actual release."
  ],
  "signal_probe_recommendation": "No new signal/BPF/global Docker probe for R1e. The historical dockerd stop/force is recorded; a new probe cannot recover the upstream caller. Any future probe should be target-TGID/cgroup-filtered, untimed, independently deadline-bounded, and only after R1 controls.",
  "global_gap_impact": "Global Docker daemon/audit journals are not continuously exported; bounded own-cgroup/GPU observation is possible, but host-global actor/OOM exclusion is not. No external-SIGKILL/OOM-exclusion or historical-root-cause-solved claim is allowed."
}
```
