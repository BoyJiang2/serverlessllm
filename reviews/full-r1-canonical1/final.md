# 完整 R1 结论（只读静态审阅）

**结论：`approved`，但仅限 `gate="R1"` / `scope="full-runtime"` 的静态源码与输入边界；不批准 G1，不批准 R2，不批准任何正式 ID。**  
本结论不代替实际 guard/probe、monitor、真实 cache、真实 716 GiB pool、32 rank 原生生命周期或 R2；这些仍是运行条件。已有路径专项结论只批准路径/输入边界，本报告未用它代替完整 R1，也未恢复 PB02 的旧错误 finding。

绑定：`code_commit=5b01d7dc7f2f0e7d1fa1c774dba475312b232eb8`，`manifest_sha256=cfa76d7d0d0b51eb1c6225c9a72b3eb0faeac5b994f2080c6e286a4fef9a40f4`。审阅对象为 `runtime/active/` 的 54 文件运行包、模板、测试、`run-manifest.json`、`control-input-manifest.json` 及必要的冻结参考证据。无 shell/SSH/资源命令，未改文件，未读凭据，未派代理。

---

## 一、上一轮 R1e 阻断项的逐项核验

| 上轮 finding | 本轮结论 | 实际证据 |
|---|---|---|
| **R1e-B01** `allow_engine=True` 允许同 run engine 在 delete 后 59 秒保留 | **已解决** | `gpu_identity13.py:14-18` 的 `require_helper_only` 已无 `allow_engine` 参数，唯一条件为 `set(snapshot["gpu_pids"]) <= {helper["host_pid"]}`；`validation_control13.py:143` 和 `accept_control13.py:143` 对全部 61 行调用；`test_evidence13.py:41-44` 覆盖 second 0/59，`:38-40` 覆盖末行；`test_gpu_identity13.py:16-18` 拒绝同 run 的 9002 engine。 |
| **R1e-B02** native idle 的 raw NVML 未与 host monitor/gpu_pids 交叉 | **已解决** | `monitor_control13.py:106-109` 的 ready 直接携带同一条 `gpu`/`gpu_processes`/`sample_begin_ns`；`validation_control13.py:142-149` 直接引用并写出；`accept_control13.py:144-146` 比较字符串并解析 PID；`monitor_summary` `:30-52` 以 `last_sample_ns` 独立关联 raw host monitor 行，并重新派生 `store_identity`。`test_evidence13.py:45-56`、`test_gpu_identity13.py:35-45` 覆盖不一致拒绝。 |
| **R1e-B03** ancestor `memory.swap.max` 只读不校验；root 缺失接口被无条件放过 | **已解决** | `preflight_control13.py:14-23` 现逐层校验 `memory.max`、`memory.high`、`memory.swap.max`；自有 slice 强制 `swap=="0"`；非根缺失 swap 拒绝；仅根允许缺失。`test_evidence13.py:16-27` 覆盖 root 缺失、非根缺失、own swap `max/1/-1` 拒绝、祖先 `max` 允许。官方 cgroup-v2 文档明确 `memory.max/high/swap.max` 是**non-root cgroups** 接口，根缺失是 ABI 语义，不是缺失限值：<https://docs.kernel.org/admin-guide/cgroup-v2.html>。 |
| **PB02** “bootstrap 缺 env” | **不恢复** | 该 finding 已被不可变原始 docker_args 证伪并撤回；当前 bootstrap/容器模板的 `SLLM_CONTROL_WORKSPACE=/experiment` 存在，不重复列为问题。路径专项的 `workspace.verify_input_layout`、host-only `historical_path`/`accept`、8rank/80part、软链拒绝、输出创建前验收、`require_full_r1(gate=R1,scope=full-runtime)` 均保留。 |

---

## 二、本轮发现（按实际文件/函数）

### F01（低/中，非阻断）服务容器 `memory=1700g` 与父 slice `MemoryMax=1700G` 相同
- **位置**：`runtime/active/service13a-template.json:4`（`"memory": "1700g"`）；`install_guard13.sh:19`（`MemoryMax=1700G`）；`preflight_control13.py:59`（`1825361100800`）；`headservice13a.json:5`（`8g`）；`monitor13-template.json:10`（`2g`）。
- **事实**：服务容器、head、monitor 都通过 `--cgroup-parent=run.slice` 进入同一个 slice；子限值之和 `1700g+8g+2g > 1700G`。父 slice 会先于服务容器自身 limit 生效，服务有效上限约少 10 GiB。
- **影响**：不是假通过；若服务真的逼近 1700g，父 slice 会 fail-closed OOM/回收。按冻结配置，Store pool 716 GiB + 引擎/Ray 常驻远低于该值，checkpoint 页缓存可回收，实际计划峰值约 1.5 TiB，低于 1.66 TiB。故**不阻断 R1**，但“服务 1700g”不是可独立执行的声明。
- **最小修正/验证**：把服务 `memory` 调为 `1690g`，或把 slice `MemoryMax` 提高到 `1710G` 并同步 `preflight`/测试；或在运行条件中明确采样 `service memory.current + head + monitor < slice MemoryMax`。G1 的 `accept` 已记录 `memory.current`/`memory.events`，可加一条运行期断言。

### F02（低，非阻断）两层准入的 elapsed-time 边界
- **位置**：`launch_control13.py:43`、`supervise_control13.py:13`、`bootstrap_control13.py:79`；`budget_control13.py:2-8`。
- **事实**：launch/supervisor 用 `remaining>=budget+180` 准入；bootstrap 在数秒后再次用同一阈值检查。若外层恰好在 3600/510 秒边界通过，bootstrap 看到 3599.x/509.x 会拒绝。
- **影响**：**只会误拒，不会放宽**；实际施工应在远大于最小窗口时启动。`test_watchdog13.py:37-46` 用固定 remaining 测试了 3600/3720/3721，未模拟 `Popen`/容器启动耗时。
- **最小修正**：外层准入改为 `budget+180+startup_slack`，或把 supervisor 的 remaining 传给 child、取消 child 的重复阈值；增加“经过 1-3 秒后仍按同一预算”的测试。

### F03（低，非阻断）release 基线来自 `prepare_host`，不保证 clean
- **位置**：`prepare_host13.py:37` 记录 `gpu_baseline_mib`；`accept_control13.py:218-219` 读取该基线；`accept_control13.py:69-70` 允许 `used<=baseline+16`。
- **事实**：`prepare_host` 调用的 `preflight(monitor_required=False)` 只校验 UUID/身份/内存/窗口/guard，不校验 `nvidia-smi --query-compute-apps` 为空；空 GPU 检查在 `launch_control13.py:52-53`。若 preparation 时有外部 GPU 进程，基线会偏高。
- **影响**：`release60-allstopped` 仍严格要求 61 秒无 GPU process（`:38`、`:65`），所以不会把“仍在运行的 engine”放过；但无进程的残留/外部显存可被高基线掩盖，内存保留检查偏宽。
- **最小修正**：在 `prepare_host`/`launch` 中显式记录“空 compute-apps 后”的 clean baseline，并用它做 release；或在 accept 中另取一次零进程基线。

### F04（低/中，非阻断）`accept` 对业务金标和权重等值的独立复核不足
- **位置**：`accept_control13.py:184-205` 只校验 `model-result.json` 的 `status/cycles/weight_format`、16 条 coverage 的计数与 contract 计数；`validation_control13.py:99-121` 才实际比较 `token_ids`/`text`/`finish_reason` 和 `weight_state` 等值。
- **事实**：response 文件和 `runtime-rankN.json` 都保留在运行目录，但 accept 没有重新读取它们；如果 validation 的 `model-result` 被错误写出，accept 不会独立发现。
- **影响**：不是当前源码的假通过（validation 是受审源码，且所有断言失败时不会写 passed），但“最终验收”的独立性弱于 cache/monitor/release 的复核。
- **最小修正**：accept 读取 hash 冻结的 `native-gold.json`，重读 `cycleN-<case>.json` 的 `token_ids/text/finish_reason`，并对 32 个 rank 文件用 `weight_state` 重新比较（或至少记录每个 audit 文件的 SHA）；应保留原始文件，不覆盖。

### F05（运行条件）timer 的人可读时间比较依赖 host 本地时区
- **位置**：`preflight_control13.py:58`、`launch_control13.py:49-51`。
- **事实**：`cleanup_deadline` 从 `+08:00` 解析后 `strftime("%Y-%m-%d %H:%M:%S")`，与 `systemctl show NextElapseUSecRealtime` 的**本地时区**字符串比较。host 为 Asia/Shanghai 时正确；若 host TZ 为 UTC，正确的 09:50 UTC timer 会显示 09:50 而不是 17:50，导致误拒。
- **最小修正**：改为 epoch 比较（`systemctl show ... --timestamp=unix` 或解析时间戳），或在运行条件中强制核验 `timedatectl` 与 timer 实际 next elapse。

### F06（低，文档）`runtime/active/README.md` 的测试数/容器案例已过期
- **位置**：`runtime/active/README.md:30-33` 仍写“Twenty-two CPU tests and fifteen isolated no-GPU container cases”；当前 `control-input-manifest.json:300-304` 与 `reviews/full-r1-canonical1/cpu-tests.log:95` 为 **92 CPU tests**；15 个容器案例属于路径专项 v2 证据，不是当前完整 R1 运行。
- **影响**：仅文档；不改变源码行为。建议部署前更新 README，避免把 92 tests 与 15 cases 混为同一次完整 R1 运行。

### F07（运行条件）分阶段 release 需要人工按序停止 head/monitor/observers；证据文件是 one-shot
- **位置**：`release_control13.py:33-46`；`accept_control13.py:219`；`cleanup_guard13.sh:19-35`；`launch_control13.py:89/108`、`supervise_control13.py:27`。
- **事实**：`release60-ray13a/ray13b` 只允许 `run-monitor13` 运行，head 容器必须先停；`release60-allstopped` 要求 `pids.current==0`，monitor 和两个 observer 必须已停。代码没有自动 stop helper；所有 release/dispatch/identity 文件以 `open('x')` 写入，失败后同一 run 目录不能重跑该阶段。
- **影响**：顺序错误会 fail-closed，需新 run；不会假通过。应在 runbook 中明确 stop 顺序，或在 17:50 guard 后做最终 allstopped；不要删除/覆盖失败证据。

### F08（运行条件）`sllm_store.proto`/grpc/`.venv` 是 54 文件包之外的外部依赖
- **位置**：`bootstrap_control13.py:105-108`；`container_mounts`/`service13a-template.json:19`（`.venv`、`Store-source-v7`、`serving-deps`）。
- **事实**：bootstrap 还在服务进程内导入 `grpc` 与 `sllm_store.proto.storage_pb2`；54 文件 manifest 只覆盖 `runtime/active`，这些依赖由 1,142 父审计与 `.venv`/Store-source-v7 绑定。
- **影响**：若 `.venv`/proto 缺失或 hash 不符，G1 会在 Store 就绪检查阶段失败；属运行前置，不是本轮静态源码缺陷。建议在 G1 前实际 import 并记录版本/路径。

---

## 三、FP8 / scale / 混精语义

- `validation_control13.py:32-38` 从 `gold["engine_config"]` 取配置，只 `pop("model")`、`pop("load_format")`，再设置 `pretrained_model_name_or_path="/experiment/checkpoint"`、`torch_dtype="bfloat16"`；safetensors 格式显式 `load_format="safetensors"`，serverless_llm 格式由 `sllm/backends/vllm_backend.py:181-197` 使用 `STORAGE_PATH/vllm/model + load_format="serverless_llm"`。
- **`torch_dtype=bfloat16` 是官方混精计算 dtype，不是把官方 FP8 权重反量化后保存为 BF16**。模型路径、revision `f396cf...`、原 FP8 权重/scale、`config.json`、`num_gpu_blocks_override=256`、`TP8`、`gpu_memory_utilization=0.9`、`max_model_len=2048`、`max_num_seqs=1`、`MTP off`、`eager` 均未改。
- 权重审计引用为每 rank **5814** 个 weight/scale/derived references，排除 **234** 个 DSA top-k scratch 和 **156** 个固定 KV 运行态；`weight_state` 比较 `tensors`，`accept` 比较三类计数与 `native-weight-contract.json`。派生 W_UK/W_UV 等后处理在 `weight_contract` 的 tensors 等值检查内，不由单独规则绕过。
- **实际权重等值与两格式×两周期×8 rank 的 32 条记录仍未运行**，这是 G1 要求，不是 R1 静态通过。

---

## 四、时间/资源组合核对

| 项 | 代码/证据 | 结论 |
|---|---|---|
| Ray 2 次 | `budget_control13.py:3` 330；`supervise_control13.py:13`；`ray_control13.py:33` alarm 240；`:66-74` 61×2s=120s stability | 预算 330；实际控制上限 240；稳定窗 120s，均低于预算。 |
| Service 3420 | `budget_control13.py:4`；`control_watchdog13.py:6-7` 25s TERM/KILL；`bootstrap_control13.py:80` alarm 3300 | 3420 总预算、25s reserve、3300 alarm、外层 180s 准入余量；F02 是边界误拒，不是超时假通过。 |
| Cache 600 / 8 / 64 GiB / 2 repairs / 148 | `bootstrap:132` timeout 600；`cache_prepare_v6.py:9-10`；`cache_prepare_v8.py:11`；`cache_repair_v9.py:34`；`cache_state_control13.py:77-130` | 完整 148 输入；初始 `resident_pages==0`；末次全量 100%；8 reader；64 GiB margin；最多 2 repair；read-idle。 |
| 删除后 60s | `validation_control13.py:139-164`；`accept_control13.py:138-154` | 61 行、≥60s；每行 helper-only；删除 ack 后最多 5s 等“sample_begin>ack”；下一 lifecycle 前再次验收。 |
| 独立最终释放 | `release_control13.py:16-47`；`accept_control13.py:219` | 3×61 行、≥60s；ray 阶段只允许 monitor；allstopped 要求 0 pids/0 容器/无 GPU process/GPU 基线+16 MiB。 |
| 716 GiB pool | `bootstrap:37-43`；`run_store_server13.py:28-47`；restored `768799145984/536870912` | 公共 pool 同批保留；pool-ready/GetServerConfig/VmRSS/listener/affinity 均有验收。 |
| 8×H20、TP8、0.9、2048、1、MTP off、eager、256 | `gold engine_config`；`run-manifest.json:1458-1465` | 均未改。 |
| 容器资源 | `service13a-template.json` 32 CPU/1700g；head 4/8g；monitor 1/2g | 不套旧 2C/2G；F01 是父/子 limit 的数值组合问题，非旧模板。 |
| Kernel cache | `prepare_host13.py:22-35` | 仅 inductor/vllm/triton；逐文件 SHA；<64 MiB；复制后复验；不复制全环境/模型/Store。 |

---

## 五、运行前置条件（全部未通过，不能写成已通过）

1. **17:50 guard/probe 尚未安装**；`install_guard13.sh` 的 timer、slice、probe、sentinel、root-private evidence 均未执行。
2. **observer/monitor 尚未启动**；无 `observer-registration13.json`、`monitor-v3-ready.json`、`resource-monitor-v12.jsonl` 的实际证据。
3. **真实 148/756 GB cache 未运行**；8 readers/600s/64 GiB/2 repairs/read-idle 未实际验证。
4. **真实 716 GiB pool 与 8 H20 原生 lifecycle 未运行**；无 32 rank 记录、PSS、四次 60s、四格式生命周期。
5. **无 `release60-ray13a/ray13b/allstopped`、无 `root-evidence-index13.json`、无 `G1-acceptance.json`**。
6. **G1/R2 未运行**；本 R1 通过不等于 G1、R2 或正式准入。
7. 运行前必须重新 refresh：booking `bk-20261026T0248-fd6bea51` 10–18、17:50 deadline；8 GPU UUID/143771 MiB；同一 boot/machine-id；NUMA node0/node1；`MemAvailable`；`/data` 空间；host TZ；docker 访问；`.venv`/`Store-source-v7` proto/grpc；`/sys/block/nvme1n1` 存在。
8. 新 guard 只保护独立 G1 身份；原始正式 run 仍需自己的新 guard/gate。
9. 历史 ledger 不变：5 success / 4 failed / 31 unstarted；下一 `store_pool_hit_03` 必须先 G1→R2→正式准入；不重试/替换终态。
10. 历史 exit137 仅已知 dockerd stop 超时升级强制退出；上游请求者/原因未知；不归因 Ray/OOM，不宣称已解决。
11. 不需要新增全局 Docker/BPF 探针；若未来 G1 出现 exit137，再做 target TGID/cgroup 过滤、独立截止、untimed 的有界探针。

---

## 六、局限

- 只读审阅；无 shell，无法独立重算全部 SHA-256；绑定的是调用给出的 `5b01d7d...` 与 `cfa76...`，并交叉核对了 manifest/证据/源码行。
- 未读取 host 的 `INDEX.json`、`protocol/historical-review-manifest-v11.json` 全文、756 GB 权重/Store 载荷；未运行 G1/R2；未访问 qjhs003。
- 未重新复制/下载模型；`store-restore-validation`、`native-repeat7/8` 等 host 参考来自冻结证据与 1,142 父审计，运行前仍需实际挂载验证。
- 路径专项 `approved_for_path_boundary` 仍是 scoped 结论，不能作为本完整 R1 或部署批准。
- 本报告不声称外部人工/机构审批。

---

```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "5b01d7dc7f2f0e7d1fa1c774dba475312b232eb8",
  "manifest_sha256": "cfa76d7d0d0b51eb1c6225c9a72b3eb0faeac5b994f2080c6e286a4fef9a40f4",
  "static_source_approved": true,
  "g1_status": "not_run_not_approved",
  "r2_status": "not_reviewed_not_approved",
  "path_boundary_review": "scoped only; not used as full R1 or deployment approval",
  "prior_findings": {
    "R1e-B01": "resolved: require_helper_only has no allow_engine; all 61 rows helper-only; second 0/59/60 negative tests",
    "R1e-B02": "resolved: host ready carries raw GPU/process/sample_begin; native idle references it; accept parses PID and joins raw monitor rows",
    "R1e-B03": "resolved: per-layer swap checked; own slice swap must be 0; root non-root-only interfaces allowed absent; non-root absence rejected; tests and official cgroup-v2 ABI",
    "PB02": "retracted false positive; not restored",
    "path_boundary_PB01_PB03_PB04_PB05": "preserved; not a full-R1 substitute"
  },
  "findings": [
    {
      "id": "R1-full-F01",
      "severity": "low_medium",
      "status": "non_blocking_runtime_condition",
      "blocking_for_r1": false,
      "title": "service container memory=1700g equals parent slice MemoryMax=1700G while head 8g and monitor 2g share the slice",
      "location": "runtime/active/service13a-template.json:4; runtime/active/install_guard13.sh:19; runtime/active/preflight_control13.py:59; runtime/active/headservice13a.json:5; runtime/active/monitor13-template.json:10",
      "evidence": "service 32 CPU/1700g; slice MemoryMax=1700G=1825361100800; head and monitor also use --cgroup-parent=run.slice. Sum of child limits exceeds parent.",
      "impact": "parent slice enforces before the service reaches its declared limit; effective service ceiling about 1690 GiB. Planned anonymous peak (716 GiB pool plus engine/Ray) is below; page cache is reclaimable. No false pass; high peak OOMs fail-closed.",
      "minimal_fix": "set service memory to 1690g or raise slice MemoryMax and update preflight/tests; alternatively require a sampled service peak plus head plus monitor below slice MemoryMax."
    },
    {
      "id": "R1-full-F02",
      "severity": "low",
      "status": "non_blocking_fail_closed_boundary",
      "blocking_for_r1": false,
      "title": "double admission with elapsed-time drift at exact 3600/510 second boundary",
      "location": "runtime/active/launch_control13.py:43; runtime/active/supervise_control13.py:13; runtime/active/bootstrap_control13.py:79; runtime/active/test_watchdog13.py:37-46",
      "evidence": "outer check admits remaining>=budget+180; bootstrap rechecks a few seconds later with the same threshold; a launch exactly at the minimum is rejected by the child.",
      "impact": "false rejection only; no unsafe admission; operator normally has hours of margin.",
      "minimal_fix": "require budget+180+startup_slack at the outer check, or pass the supervisor's remaining to the child/remove the duplicate check; add a test with simulated elapsed seconds."
    },
    {
      "id": "R1-full-F03",
      "severity": "low",
      "status": "non_blocking_runtime_condition",
      "blocking_for_r1": false,
      "title": "release GPU baseline comes from prepare_host and is not guaranteed clean",
      "location": "runtime/active/prepare_host13.py:37; runtime/active/accept_control13.py:218-219; runtime/active/accept_control13.py:69-70",
      "evidence": "prepare_host calls preflight(monitor_required=False), which does not assert empty nvidia-smi compute apps; launch does that later. A foreign GPU process during preparation raises the baseline.",
      "impact": "the 61-second no-GPU-process check remains strict; only the memory-retention tolerance can be widened by a high baseline.",
      "minimal_fix": "record the baseline after an explicit empty compute-apps check at prepare/launch and use that clean baseline; record its source."
    },
    {
      "id": "R1-full-F04",
      "severity": "low_medium",
      "status": "non_blocking_verification_hardening",
      "blocking_for_r1": false,
      "title": "accept trusts model-result for business gold and weight equality instead of re-reading raw response and audit files",
      "location": "runtime/active/accept_control13.py:184-205; runtime/active/validation_control13.py:99-121",
      "evidence": "accept checks status/cycles/weight_format/16 coverage/contract counts/helper/PSS, but does not re-read cycleN-case.json or current/runtime-rankN.json; validation performs the equality.",
      "impact": "independence gap; a false or incorrectly written model-result would not be caught by accept, although the raw evidence is retained.",
      "minimal_fix": "accept re-reads the hash-pinned gold and rechecks three responses and the 32 rank files via weight_state (or records their SHA); retain raw evidence."
    },
    {
      "id": "R1-full-F05",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "human-readable timer comparison depends on host timezone",
      "location": "runtime/active/preflight_control13.py:58; runtime/active/launch_control13.py:49-51",
      "evidence": "deadline from +08:00 is formatted without conversion and compared to systemctl NextElapseUSecRealtime, which is host-local. Asia/Shanghai works; UTC host would falsely reject.",
      "impact": "false rejection only; no unsafe admission.",
      "minimal_fix": "compare epoch via systemctl --timestamp=unix, or verify timedatectl and timer actual next elapse before launch."
    },
    {
      "id": "R1-full-F06",
      "severity": "low_documentation",
      "status": "non_blocking",
      "blocking_for_r1": false,
      "title": "README still states 22 CPU tests and 15 container cases",
      "location": "runtime/active/README.md:30-33; runtime/active/control-input-manifest.json:300-304; reviews/full-r1-canonical1/cpu-tests.log:95",
      "evidence": "current local validation is 92 CPU tests; the 15 container cases are path-boundary v2 evidence, not the current full R1 run.",
      "impact": "documentation only; no runtime.",
      "minimal_fix": "update README to 92 CPU tests and current scope before deployment; keep path-boundary evidence separate."
    },
    {
      "id": "R1-full-F07",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "staged release requires manual stop order and one-shot evidence files",
      "location": "runtime/active/release_control13.py:33-46; runtime/active/accept_control13.py:219; runtime/active/cleanup_guard13.sh:19-35",
      "evidence": "ray release stages allow only run-monitor13; allstopped requires pids.current 0; monitor and both observers must be stopped. No automatic stop helper; open('x') prevents rerunning a failed stage in the same run directory.",
      "impact": "wrong order fails closed and needs a new run; no false pass.",
      "minimal_fix": "document and execute the exact stop order; optionally add a bounded stop helper; never overwrite failed evidence."
    },
    {
      "id": "R1-full-F08",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "sllm_store.proto/grpc/.venv are external to the 54-file package",
      "location": "runtime/active/bootstrap_control13.py:105-108; runtime/active/service13a-template.json:19; runtime/active/workspace.py:52-64",
      "evidence": "bootstrap imports grpc and sllm_store.proto in the service process; those are bound by the 1,142 parent audit and .venv/Store-source-v7, not by the 54-file control manifest.",
      "impact": "if the venv/proto is absent or mismatched, G1 fails at control-plane readiness; not a static source defect.",
      "minimal_fix": "at G1 preflight, import the proto/grpc and record versions/paths; verify the venv and Store-source-v7 proto."
    }
  ],
  "fp8_semantics": {
    "model_revision": "f396cf805182f4ca10fa675e1a99815b3ca384db unchanged",
    "official_weights_and_scales": "unchanged; no dtype conversion or dequantization-to-BF16 code",
    "torch_dtype": "bfloat16 is the official mixed-precision compute dtype, not a saved weight conversion",
    "load_paths": "serverless_llm uses STORAGE_PATH/vllm/model; safetensors uses load_format=safetensors with /experiment/checkpoint",
    "audit_counts": "5814 weight/scale/derived references per rank; 234 DSA top-k scratch excluded; 156 KV runtime excluded",
    "g1_requirement": "actual 32 rank equality and business/gold results are not run and remain G1"
  },
  "budget_composition": {
    "ray": "2 controls, 330 s budget each, supervisor 25 s reserve, child alarm 240 s, 61x2 s stability",
    "service": "3420 s total, 25 s TERM/KILL reserve, bootstrap alarm 3300 s, 180 s admission margin",
    "cache": "600 s, 8 readers, 64 GiB NUMA margin, max 2 repairs, 148 inputs, initial 0, final full mincore, read-idle",
    "release": "three independent 61-sample 60 s releases; each delete retains 60 s",
    "pool": "716 GiB public pool; 8 H20; TP8; utilization 0.9; context 2048; seq 1; MTP off; eager; 256 KV blocks"
  },
  "store_binding": {
    "receipt": "run_store_server13.py verifies four imported modules by path and SHA before pool creation and records torch/vllm/CUDA versions",
    "canonical_path": "/experiment/code/Store-source-v7/sllm_store; bundled ServerlessLLM extension is not substituted",
    "checkpoint_extension_sha256": "259ca21a1b34f5a50667f70d2df4571d0f51572affbc982fec9267f2dfe7b096",
    "scope": "Store process only; not per-Ray-worker import proof; 8 rank weights/business audits remain G1"
  },
  "historical": {
    "success": 5,
    "failed": 4,
    "unstarted": 31,
    "next_id": "store_pool_hit_03",
    "formal_ids_consumed_by_this_review": 0,
    "exit137": "dockerd stop timeout/forced exit mechanism known; upstream requester/reason unknown; not attributed to Ray/OOM; not declared resolved"
  },
  "runtime_conditions": [
    "17:50 guard/probe, slice, sentinel and root-private evidence are not installed or executed",
    "observer registration, monitor-v3-ready and resource-monitor-v12.jsonl are not produced for this run",
    "real 148-input/756 GB cache with 8 readers, 600 s, 64 GiB margin and up to 2 repairs is not run",
    "real 716 GiB pool and four native lifecycles with 32 rank records, PSS and 61-row idle60 are not run",
    "release60-ray13a, release60-ray13b, release60-allstopped, root-evidence-index13.json and G1-acceptance.json do not exist",
    "G1 and R2 are not run; this R1 approval does not imply either",
    "refresh booking bk-20261026T0248-fd6bea51 10-18, 17:50 cleanup, GPU UUID/boot/machine-id, NUMA node0/1, MemAvailable, /data space, host timezone, docker access, .venv and proto/grpc",
    "new guard protects the independent G1 identity only; the original formal run still needs its own new guard/gate",
    "formal ledger remains 5 success / 4 failed / 31 unstarted; store_pool_hit_03 only after G1 then R2 then formal admission",
    "historical exit137 remains failed; no retry/replacement and no attribution to Ray or OOM",
    "no new global Docker/BPF probe is required; any future probe must be target-TGID/cgroup filtered, untimed and independently bounded"
  ],
  "limitations": [
    "read-only review; no shell, SSH, resource command, file modification, credential read or subagent",
    "SHA-256 values were compared from supplied/precomputed manifests and evidence; no independent full recomputation",
    "host INDEX.json, protocol/historical-review-manifest-v11.json full content, 756 GB weights/Store payloads and host validation copies were not read",
    "no actual guard, monitor, cache, pool, native lifecycle, release or G1/R2 was run",
    "path-boundary approval remains scoped and is not a substitute for this full R1",
    "the actual remote git commit/manifest binding is as supplied by the caller and was not independently verified by git",
    "no external human or institutional approval is claimed"
  ]
}
```