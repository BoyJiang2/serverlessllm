# canonical2 受影响差异复审结论

**结论：`approved`（仅 `gate="R1"` / `scope="full-runtime"` 的静态源码与输入边界）。**  
本次只审 canonical2 的实际差异；未变的 accept/bootstrap/validation/monitor/cache/store/weight-contract/guard 共用逻辑沿用 canonical1 已完成 full R1 的实际审阅结果。**G1、R2 均未通过；不得把本结论写成 G1/R2 通过。**

绑定：`code_commit=ef53e19273137d7ae98e0105c5d2893539aafff6`，`control-input-manifest SHA256=a1dc0959486cb1274a1258850d3f6e660eff97e2889d43baf6e811f4dc1dd43c`。旧 `reviews/full-r1-canonical1/verdict.json` 的 approved 只绑定 `5b01d7d/cfa76...`，**不能用于本新字节**；本报告不返回 `approved_for_path_boundary`。

---

## 一、实际差异核验

| 差异 | 实际文件/证据 | 结论 |
|---|---|---|
| 容器内唯一可写挂载改为 `/r` | `workspace.py:9` `RUN_ALIAS=/r`；`:23-25` `run_root()` 在 `/experiment` 下返回 `/r`，host 仍返回 `workspace()/validation/controls/<run>`；`:54-67` `container_mounts()` 只追加一个 `source=host run dir, destination=/r, readonly=False` 的可写挂载；`:11-15` workspace 仍只允许 canonical host 或 `/experiment` | 正确；host 路径不变，容器唯一可写；readonly 输入仍在 `/experiment/...`。 |
| 所有写入路径使用 `/r` | `headray13a.json:8,22-25,42`；`headray13b.json:8,22-25,42`；`headservice13a.json:8,22-25,42`；`ray13a/ray13b-template.json:8,22-27`；`service13a-template.json:8,22-32`；`test_workspace.py:55-63` 要求 HOME/HF_HOME/cache/event/audit 前缀 `/r/` | 已覆盖 HOME、HF_HOME、inductor/triton/vllm/cuda cache、Ray temp、SLLM event、weight audit；没有残留旧 run 可写路径。 |
| Ray Unix socket 长度 | `test_workspace.py:17-21` 用 `/r/ray-head-<role>/session_.../sockets/plasma_store` 断言 `<=107` 字节；`headray13a.json:42`、`headray13b.json:42`、`headservice13a.json:42` 的实际 `--temp-dir` 均为 `/r/ray-head-...`；worker 的 `RAY_TMPDIR` 由 `bootstrap_control13.py:45`、`ray_control13.py:25` 生成 `/r/ray-<label>` | 旧失败路径 154/158 字节；新 head/worker 路径均显著低于 107。测试覆盖 head 的最长形态（`ray-head-service13a` + session + `plasma_store`），worker 更短。实际容器 socket 尚未创建，仍是 G1 运行条件。 |
| 缓存/事件/审计/PSS/Store 输出 | `cache_state_control13.py` 使用 `run_root()` `/r`；`service13a-template.json:27,32` `/r/events-service13a`、`/r/weight-audits-service13a`；`validation_control13.py:49-53` `/r`；`collect_pss13.py` `run_root()`；`run_store_server13.py:25-36` `/r` | 均落入唯一 `/r` 可写挂载；没有第二个 host 可写资产。 |
| phase-trace 输出 confinement | `launch_control13.py:75-79` 要求 `SLLM_EVENT_DIR` 是 `PurePosixPath`、`is_relative_to(RUN_ALIAS)` 且不含 `..`；`test_workspace.py:7-16` 用 AST 执行实际 `if spec.get('phase_trace')` 分支，`/r/events` 通过，`/experiment/events`、`/r/../etc`、`/r2/events` 拒绝 | 正确；phase-trace 只能写 `/r`。 |
| 新 run / 新窗口 / 新 guard | `run-manifest.json:2` 新 `run_id=sllm-glm51fp8-20261026T075854Z-e71cc109`；`:8-9` `21:00`/`20:50`；`:1472-1475` 两个 booking；`resource-window-extension-v3.json:10-27` 两段连续 10–18、18–21，`guard_timer` 新；`install_guard13.sh:3,8-9,28` 新 run、`20:50`、`12:50 UTC` timer；`install_observers13.sh:4,10`、`cleanup_guard13.sh:4`、`snapshot_root13.py:4` 全部新 run | 绑定正确；旧 17:50 guard 不在新 slice/timer。 |
| 两段 booking 逻辑 | `resource_window_control13.py:20-29`：`rows[0].start<=now<rows[-1].end`；ID 列表两段；user/resource；连续；`deadline==end-600`；`guard_timer==run_id+...`；`test_window13.py:21-30` 新 start/end | 正确；18:00 是两段接缝；21:00 精确拒绝。 |
| 旧失败 run 与新 run 隔离 | `STATUS.md:175-179`：旧 G1 Ray head 因 AF_UNIX>107 失败；无 Store/model/cache；root cleanup + 61 样本 75.822s 释放；旧执行源码保留；新 run 独立；旧 17:50 guard inactive | 正确；没有重试/覆写旧失败，没有复旧凭证。 |
| 94 CPU 测试 | `reviews/full-r1-canonical2/cpu-tests.log:84,94-97` 94 tests OK；`control-input-manifest.json:300-304` `cpu_tests=94`；`shell-syntax.json` 3 项 | 本地 CPU 检查通过；不是 G1/R2。 |

---

## 二、本次新发现（均非阻断）

### C2-F01（low，新证据/元数据）新两段窗口仍保留旧 overlay 创建/snapshot 时间
- **位置**：`resource-window-extension-v3.json:6-7,29-31`：`overlay_created_utc=2026-10-26T03:13:44Z`、`snapshot_file_mtime_utc=2026-10-26T03:00:41Z`、`overlay_initial_draft_created_utc=2026-10-26T03:00:41Z`；`:10-24` 第二段 `bk-20261026T0607-546b6bd8`；`:30` `overlay_sealed_utc=2026-10-26T08:08:34Z`；`run-manifest.json:2` 新 run `07:58:54Z`；`STATUS.md:169` 实际 14:46:58+08 查询两段 booking。
- **事实**：`snapshot_file_mtime`/`overlay_created` 早于第二段 booking 的创建和 14:46+08 查询；`bookings` 两段和 `overlay_sealed` 是新值。`load_window` 只校验 booking ID/user/resource/连续性/deadline/timer，不读取这些时间戳。
- **影响**：运行期 `load_window` 接受的是冻结文件；实际 booking API 的 14:46 查询证据在 STATUS，而不是 window 文件的 snapshot 时间。不会造成假通过，但证据链的“新窗口已刷新”不够直接。
- **最小修正/验证**：把 `snapshot_file_mtime_utc` 更新为实际查询时间，或增加 `booking_verified_utc`（如 2026-10-26T06:46Z / 14:46+08）并在 `load_window`/`preflight` 中要求其存在且不早于文件创建；G1 前仍按运行条件重新查询 booking。

### C2-F02（low，文档）README/CANONICAL 仍是旧 22/15、17:50、单 booking
- **位置**：`runtime/active/README.md:30-33`（22 CPU tests/15 container cases）；`protocol/CANONICAL-WORKSPACE.md:25-26`（10–18、17:50 guard）；对比 `run-manifest.json:8-9`（21:00/20:50）、`resource-window-extension-v3.json:10-27`（两段）、`control-input-manifest.json:300-304`（94）。
- **影响**：只影响文档/审阅可读性；运行时以 `run-manifest`/`resource-window` 为准。旧 README 的 22/15 属于路径专项，不是当前 94。
- **最小修正**：部署前更新 README 为 94 和当前 `/r`/两段窗口；CANONICAL 增加“当前 run 以 run-manifest/window 为准，17:50 是旧 canonical1 状态”的说明。不要改运行时源码来做文档美化。

### C2-F03（low，验证覆盖）socket 回归只测 head/plasma_store，未显式测 worker `RAY_TMPDIR`/`raylet`
- **位置**：`test_workspace.py:17-21`；实际 `head*` 的 `--temp-dir` `/r/ray-head-*`；`bootstrap_control13.py:45`、`ray_control13.py:25` 的 worker `RAY_TMPDIR=/r/ray-*`；Ray session 下还可能使用 `sockets/raylet`、`gcs_server`。
- **事实**：测试覆盖了最长的 head 形态（`ray-head-service13a` + session + `plasma_store`，<=107）；worker 路径更短，因此当前测试对最大长度是充分的。但没有显式覆盖 worker 和 `raylet`/`gcs_server` 名称。
- **影响**：不是代码 bug；实际 G1 仍必须看到新 head/worker 真正启动。若未来 Ray session 命名变长，测试字符串不能代表实际最大。
- **最小修正**：在测试中同时枚举 head/worker 的 `RAY_TMPDIR` 和 `sockets/raylet`、`sockets/gcs_server`、`sockets/plasma_store`，用实际模板的 `--temp-dir`/`RAY_TMPDIR` 生成路径；G1 记录实际 head 的 `docker inspect` 和端口 ready。

### C2-F04（runtime）head 的私有 GCS TCP 端口 ready 仍是操作者检查
- **位置**：`launch_control13.py:54-108` 启动 head 并记录 `container_id`/`pid`/`cgroup`；`materialize_control13.py:23-29` 只 `docker inspect` Running/labels/IP；`ray_control13.py:56` `ray.init` 会连接；没有内置的 GCS TCP 26530/26531/26532 检查。
- **事实**：本次真实失败是 head 的 Unix socket 路径；`/r` 已修复。操作顺序要求“head 启动后核验 Running 且 GCS TCP ready，再派发 worker”。代码层面没有自动 port probe，只有 operator 检查；如果过早派发，worker 会在 `ray.init` 失败。
- **影响**：不会假通过；失败会停在 Ray 控制，cleanup/60s/root index 仍必须执行。属运行前置。
- **最小修正/验证**：可在 `launch_control13` 对 head 名称增加一个有界 TCP connect（`127.0.0.1:<port>` 或 head 私网 IP），只记录不全局改动；或保持 operator 检查并在 runbook 明示“Running + GCS ready + 只按记录 CID 停止”。不需要全局 Docker/BPF 探针。

### C2-F05（low，元数据）`run-manifest.booking_id` 仍是第一段，`booking_ids` 是两段
- **位置**：`run-manifest.json:7` `booking_id=bk-20261026T0248-fd6bea51`；`:1472-1475` `booking_ids` 两段；`resource-window-extension-v3.json:10-24` 两段。无 Python 代码读取 `booking_id`；`load_window` 使用 `bookings`。
- **影响**：只是元数据单/复数不一致；运行窗口以 `booking_ids`/`resource-window` 为准。
- **最小修正**：明确 `booking_id` 为 primary、`booking_ids` 为完整窗口，或统一为数组；不改运行逻辑。

### C2-F06（low，carried hardening）`/r` 可写 run 目录仍包含控制元数据
- **位置**：`workspace.py:66` 把整个 host run dir 映射 `/r` 可写；`deploy_control13.py:69-71` 把 `run-manifest.json`/`resource-window`/`cache-inputs`/`control-input-manifest` 写进该 run dir；`accept_control13.py:165-169` 校验 run-dir control manifest 和 `deployment`，但用 run-dir `run-manifest.json` 的字段；`preflight_control13.py:26` 在每个 launch 前比较 run-dir 与 SOURCE run-manifest。
- **事实**：这是 canonical1 已有的“唯一可写 run 目录”语义；`/r` 只是短别名，没有新增可写 host 资产。容器 uid 2147 可写 run-manifest/control 副本；`accept` 没有在最后再次比较 run-dir run-manifest 与 SOURCE。
- **影响**：低；preflight 会在下一次 launch 前发现漂移，accept 的 control manifest/deployment 有 hash；不是本次 `/r` 新增。若要求严格独立，可让 accept 使用 `workspace.manifest()`（SOURCE）或再次比较 run-dir run-manifest 与 control manifest 行。
- **最小修正**：在 accept 中从 `SOURCE/run-manifest.json` 读取不可变字段，或断言 run-dir 副本 hash 等于 control-input-manifest 的 `run-manifest.json` 行；不改变 `/r` 设计。

### C2-F07（carried，非阻断）canonical1 已列的非阻断运行条件不变
- 父 slice 1700G 与 service 1700g 共享 head/monitor；精确 3600/510 秒双准入边界；`prepare_host` baseline 非 clean；host TZ 的 `NextElapse` 字符串比较；分阶段 release 的 stop 顺序；`.venv`/proto 外部依赖；accept 对 model-result 的独立复核。  
- 本 canonical2 只改路径别名/窗口/run/guard，没有改变这些条件；沿用 canonical1 的 nonblocking 结论，不在本次重新判为阻断。运行条件中保留。

---

## 三、实际运行条件（全部未完成）

1. **新 20:50 guard/probe 尚未安装**；旧 failed run 的 17:50 guard 已 inactive；新 run 不得复用旧凭证。
2. **新两段 booking** 的 `resource-window` 已绑定；执行前仍需重新查询 booking API；`load_window` 只验证冻结文件，不替代实时查询。
3. **新 run 的 monitor/observers** 尚未启动；无 `observer-registration13.json`、`monitor-v3-ready.json`、`resource-monitor-v12.jsonl`。
4. **新 head/worker** 尚未实际执行；`/r` 的 socket 修复只有 CPU 回归，真实 Ray session 仍须 G1 验证。
5. **每个 head 启动后必须由操作者核验** Running 且对应私有 GCS TCP 端口 ready，再派发 worker；只按记录 CID 停止自有 head。
6. **两个 Ray 控制**须分别成功并完成各自 60s 释放，之后才派发真实服务批次。
7. **真实 716 GiB pool、cache 148/756GB、四次 native lifecycle、32 rank、PSS、61 行 idle60、release60×3、root index** 均未运行。
8. **G1/R2 未运行**；本 R1 通过不等于 G1/R2；正式 5 success/4 failed/31 unstarted 不变，下一 `store_pool_hit_03` 仅在 G1→R2→正式准入后。
9. 任何失败立即停止新分配，清理本 run，最终 60s 观察/root index；不覆写失败、不重试正式 ID。
10. 历史 exit137 仍为 dockerd stop 超时升级强制退出；上游请求者/原因未知；不归因 Ray/OOM，不宣称解决。
11. 其他运行前置：host TZ（Asia/Shanghai）、1700G/1700g 组合、精确准入边界、clean GPU baseline、release stop 顺序、`.venv`/proto/grpc、实际 `/r` socket、旧失败证据不可重试。

---

## 四、局限

- 只读审阅；无 shell，不能独立重算全部 SHA-256；绑定调用给出的 `ef53e192...` 与 `a1dc0959...`，交叉核对实际文件、manifest、window、模板、测试与 STATUS。
- 未读取 host 的 `INDEX.json`、完整 1,142 父 manifest、756GB 权重/Store 载荷；未运行 G1/R2；未访问 qjhs003。
- 未实际创建新 head/worker 的 Unix socket；`/r` 的 107 字节结论来自实际模板路径与 CPU 回归，真实 G1 仍需验证。
- 未重新审阅未变的 accept/bootstrap/validation/monitor/cache/store/weight-contract/guard 共用逻辑；沿用 canonical1 full R1 的实际 approved，本报告只绑定 canonical2 新字节。
- README/CANONICAL 仍保留旧 22/15、17:50、单 booking 描述；运行时以新 `run-manifest`/`resource-window` 为准。
- 新窗口的 `snapshot_file_mtime`/`overlay_created` 仍是旧 draft 时间；实际 booking 查询在 STATUS，不在 window 文件。
- 不声称外部人工/机构审批。

---

```json
{
  "gate": "R1",
  "scope": "full-runtime",
  "verdict": "approved",
  "code_commit": "ef53e19273137d7ae98e0105c5d2893539aafff6",
  "manifest_sha256": "a1dc0959486cb1274a1258850d3f6e660eff97e2889d43baf6e811f4dc1dd43c",
  "static_source_approved": true,
  "g1_status": "not_run_not_approved",
  "r2_status": "not_reviewed_not_approved",
  "prior_canonical1_binding": {
    "code_commit": "5b01d7dc7f2f0e7d1fa1c774dba475312b232eb8",
    "manifest_sha256": "cfa76d7d0d0b51eb1c6225c9a72b3eb0faeac5b994f2080c6e286a4fef9a40f4",
    "status": "full R1 approved; does not approve canonical2 bytes"
  },
  "affected_diff": {
    "run_alias": "/r",
    "host_run_root": "/data/brick1/boyjiang/glm5.1-fp8/validation/controls/sllm-glm51fp8-20261026T075854Z-e71cc109",
    "container_run_root": "/r",
    "readonly_inputs": "/experiment/... unchanged",
    "new_run_id": "sllm-glm51fp8-20261026T075854Z-e71cc109",
    "new_window": "10-18 + 18-21 contiguous; cleanup 20:50; timer 12:50 UTC",
    "socket_regression": "old 154/158 bytes; new head/worker paths below 107; actual G1 still required",
    "old_failed_run": "Ray head AF_UNIX>107; no Store/model/cache/formal; cleanup+75.822s release; source/evidence preserved; not retried"
  },
  "findings": [
    {
      "id": "C2-F01",
      "severity": "low",
      "status": "non_blocking_evidence",
      "blocking_for_r1": false,
      "title": "new two-booking window retains old overlay_created/snapshot timestamps",
      "location": "runtime/active/resource-window-extension-v3.json:6-7,10-24,29-31; runtime/active/run-manifest.json:2; STATUS.md:169",
      "evidence": "snapshot 2026-10-26T03:00:41Z and overlay_created 03:13:44Z predate the second booking bk-20261026T0607-546b6bd8 and the 14:46:58+08 query; bookings and overlay_sealed 08:08:34Z are new; load_window does not read these timestamps.",
      "impact": "frozen window is accepted; the actual booking refresh timestamp is outside the window file, so the evidence chain is weaker; no false pass.",
      "minimal_fix": "update snapshot/overlay_created or add booking_verified_utc (e.g. 2026-10-26T06:46Z) and require it in load_window/preflight; re-query booking immediately before dispatch."
    },
    {
      "id": "C2-F02",
      "severity": "low",
      "status": "non_blocking_documentation",
      "blocking_for_r1": false,
      "title": "README and CANONICAL still describe 22/15 tests, 17:50 guard and one booking",
      "location": "runtime/active/README.md:30-33; protocol/CANONICAL-WORKSPACE.md:25-26; runtime/active/run-manifest.json:8-9; runtime/active/resource-window-extension-v3.json:10-27; runtime/active/control-input-manifest.json:300-304",
      "evidence": "current local validation is 94 CPU tests and the run window is 10-18+18-21 with 20:50 cleanup; README/CANONICAL are unchanged from canonical1.",
      "impact": "documentation only; runtime uses run-manifest/resource-window.",
      "minimal_fix": "update README to 94 and current /r/two-booking scope; annotate CANONICAL that 17:50 is the old canonical1 state and the current run is defined by run-manifest/window."
    },
    {
      "id": "C2-F03",
      "severity": "low",
      "status": "non_blocking_verification",
      "blocking_for_r1": false,
      "title": "socket regression test covers head/plasma_store but not explicitly worker RAY_TMPDIR/raylet",
      "location": "runtime/active/test_workspace.py:17-21; runtime/active/headray13a.json:42; runtime/active/headray13b.json:42; runtime/active/headservice13a.json:42; runtime/active/bootstrap_control13.py:45; runtime/active/ray_control13.py:25",
      "evidence": "test path is /r/ray-head-<role>/session.../sockets/plasma_store <=107; worker RAY_TMPDIR=/r/ray-<label> is shorter; raylet/gcs_server are shorter than plasma_store.",
      "impact": "test covers the maximum head form; actual G1 must still create the real sockets.",
      "minimal_fix": "add worker RAY_TMPDIR and raylet/gcs_server to the test; use representative session names; record actual head/worker path lengths in G1."
    },
    {
      "id": "C2-F04",
      "severity": "runtime_condition",
      "status": "runtime_condition",
      "blocking_for_r1": false,
      "title": "head GCS TCP readiness and worker dispatch remain operator checks",
      "location": "runtime/active/launch_control13.py:54-108; runtime/active/materialize_control13.py:23-29; runtime/active/ray_control13.py:56",
      "evidence": "launch records container id/pid/cgroup; materialize inspects Running/labels/IP; no built-in GCS TCP 26530/26531/26532 check; ray.init fails if head not ready.",
      "impact": "a head that is Running but GCS not ready could be dispatched too early; failure remains fail-closed in the Ray control.",
      "minimal_fix": "add a bounded TCP connect to the head's GCS port after start, or keep operator verification and document exact order; no global Docker/BPF probe."
    },
    {
      "id": "C2-F05",
      "severity": "low",
      "status": "non_blocking_metadata",
      "blocking_for_r1": false,
      "title": "run-manifest booking_id is the first booking while booking_ids contains both",
      "location": "runtime/active/run-manifest.json:7,1472-1475; runtime/active/resource-window-extension-v3.json:10-24",
      "evidence": "no Python code reads booking_id; load_window uses bookings; booking_ids contains both IDs.",
      "impact": "metadata ambiguity only.",
      "minimal_fix": "document booking_id as primary and booking_ids as the full window, or unify to an array."
    },
    {
      "id": "C2-F06",
      "severity": "low",
      "status": "carried_non_blocking",
      "blocking_for_r1": false,
      "title": "writable /r run directory contains control metadata; accept uses the run-dir run-manifest",
      "location": "runtime/active/workspace.py:66; runtime/active/deploy_control13.py:69-71; runtime/active/accept_control13.py:165-169; runtime/active/preflight_control13.py:26",
      "evidence": "the single writable mount is the host run dir at /r; deploy copies run-manifest/control-input/window/cache-inputs there; preflight checks run-dir vs source before each launch, but accept does not re-check the run-dir run-manifest against the source.",
      "impact": "low hardening gap; preflight catches drift before launches; accept has control-manifest and deployment hashes; same as canonical1 and not worsened by /r.",
      "minimal_fix": "accept reads immutable fields from SOURCE/run-manifest or asserts the run-dir copy hash equals the control-input-manifest row."
    },
    {
      "id": "C2-F07",
      "severity": "carried_non_blocking",
      "status": "carried",
      "blocking_for_r1": false,
      "title": "canonical1 non-blocking runtime conditions remain unchanged",
      "location": "runtime/active/service13a-template.json:4; runtime/active/install_guard13.sh:19; runtime/active/launch_control13.py:43; runtime/active/supervise_control13.py:13; runtime/active/bootstrap_control13.py:79; runtime/active/accept_control13.py:184-205; runtime/active/release_control13.py:33-46",
      "evidence": "1700G slice vs 1700g service plus head/monitor; exact 3600/510 second double admission; prepare_host baseline not guaranteed clean; host-local NextElapse string compare; staged release stop order; .venv/proto external; accept model-result independence. None changed in canonical2.",
      "impact": "retained as runtime conditions, not re-opened as blocking; the prior canonical1 full R1 approved these as non-blocking.",
      "minimal_fix": "keep the prior runtime checks; if any is to be removed, make a separate reviewed revision."
    }
  ],
  "runtime_conditions": [
    "new 20:50 guard/probe is not installed; old failed-run 17:50 guard is inactive and must not be reused",
    "two bookings 10-18 and 18-21 are bound in resource-window; re-query the booking API immediately before dispatch; load_window only validates the frozen file",
    "new run observers/monitor are not started; no observer-registration13.json, monitor-v3-ready.json or resource-monitor-v12.jsonl",
    "new head/worker actual Unix sockets are not created; /r regression is CPU-only",
    "after each head start, operator must verify Running and private GCS TCP port ready before dispatching the worker; stop only the recorded CID",
    "two Ray controls must each pass and complete their own 60s release before the real service batch",
    "real 716 GiB pool, 148/756 GB cache, four native lifecycles, 32 rank records, PSS, 61-row idle60 and release60 x3 are not run",
    "root evidence index and final allstopped release are not produced for the new run",
    "G1 and R2 are not run; formal ledger remains 5 success / 4 failed / 31 unstarted; store_pool_hit_03 only after G1 then R2 then formal admission",
    "any failure stops new allocation, cleans this run, final 60s observation and root index; no overwrite and no formal retry",
    "historical exit137 remains failed; dockerd stop/force mechanism known; upstream requester/reason unknown; not attributed to Ray/OOM",
    "verify Asia/Shanghai host timezone, clean GPU baseline, 1700G/1700g composition, exact admission margins, staged release order, .venv/proto/grpc and actual /r paths",
    "old failed run source/evidence retained; active code directory must be free for approved replacement; no reintroduction of old long path or second writable host asset"
  ],
  "limitations": [
    "read-only review; no shell, SSH, resource command, file modification, credential read or subagent",
    "SHA-256 values were compared from supplied/computed manifests and evidence; no independent full recomputation",
    "host INDEX.json, full 1,142 parent manifest, 756 GB weights/Store payloads and host validation copies were not read",
    "no actual guard, monitor, head/worker socket, cache, pool, native lifecycle, G1 or R2 was run",
    "the actual socket limit conclusion is based on the real templates plus CPU regression, not a live Ray session",
    "the unchanged accept/bootstrap/validation/monitor/cache/store/weight-contract logic relies on the prior canonical1 full R1 approval; canonical2 only re-reviewed the affected diff",
    "README/CANONICAL remain stale for 22/15 and 17:50/one-booking; runtime uses run-manifest/resource-window",
    "the new window snapshot/overlay timestamps are old draft values; the actual booking query is recorded in STATUS, not in the window file",
    "the actual remote commit/manifest binding is as supplied by the caller and was not independently verified by git",
    "no external human or institutional approval is claimed"
  ]
}
```