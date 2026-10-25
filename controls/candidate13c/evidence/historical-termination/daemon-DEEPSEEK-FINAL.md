## 1. 绑定核对：6/6 条均指向同一目标

6 条记录的 `_BOOT_ID` 均为 `0145ca12ac6e40fc97605d0639290553`；`MESSAGE` 均含完整 CID `8ebd672e388d52410269e3c0de99a040274ecd0fa5268a9b26f86481eaf9ac24`。`__REALTIME_TIMESTAMP` 与 `MESSAGE` 时间一致，偏差均小于 0.2ms。

| # | unit / PID / comm | `__REALTIME_TIMESTAMP` → UTC | MESSAGE UTC | 内容 |
|---|---|---|---|---|
| 1 | containerd.service / 5515 / containerd | 04:58:20.057793 | 04:58:20.057721 | connecting to shim，目标 CID（第 14–20 行） |
| 2 | docker.service / 6247 / dockerd | 05:03:15.681689 | 05:03:15.681606 | 目标 CID：`failed to exit within 1m30s of signal 15 - using the force`（第 23–28 行） |
| 3 | containerd.service / 5515 / containerd | 05:03:16.302585 | 05:03:16.302539 | 目标 CID：`shim disconnected`（第 31–36 行） |
| 4 | containerd.service / 5515 / containerd | 05:03:16.302585 | 05:03:16.302584 | 目标 CID：`cleaning up after shim disconnected`（第 39–44 行） |
| 5 | containerd.service / 5515 / containerd | 05:03:16.302771 | 05:03:16.302592 | 目标 CID：`cleaning up dead shim`（第 47–52 行） |
| 6 | docker.service / 6247 / dockerd | 05:03:16.302797 | 05:03:16.302716 | 目标 CID：`ignoring event ... TaskDelete`（第 55–60 行） |

**结论：** 记录没有串到其他 CID/boot；dockerd 与 containerd 的 PID/unit 与消息内容相符。第 2 条是关键直接记录。

## 2. 新增已证明

1. **Docker 的 stop/强制终止路径有直接 daemon 记录。**  
   dockerd PID 6247 在 `05:03:15.681606Z` 对完全相同 CID 明确记录：容器在收到 signal 15 后 90 秒内没有退出，Docker 使用 force（第 23–28 行）。这直接证明终止不是“无记录的突然消失”，而是 **SIGTERM → 90 秒等待 → force escalation** 的 Docker stop 路径。

2. **时间与 exit 137/FinishedAt 紧密吻合。**  
   force 记录早于 `FinishedAt 05:03:16.289423094Z` 约 **0.608 秒**；containerd `shim disconnected` 晚于 FinishedAt 约 **13.1 毫秒**（第 31–36 行）。这与 force 终止后容器退出、shim 清理的顺序一致；`ExitCode=137`、`OOMKilled=false` 也与该路径相容。

3. **首个 record 只是启动时 containerd 连接 shim，不是 kill。**  
   第 14–20 行是 `04:58:20.0577Z` 的 shim connect；它确认 bind，不提供死因。

4. **不再能写“终止机制完全未知”。**  
   机制层至少到 Docker daemon：**Docker stop 的 SIGTERM + 90 秒超时 + force** 已被直接记录。仍未知的是更上游的请求者/触发原因。

## 3. 尚未证明

- **最上游 stop API 调用者、请求时间、授权/动机未知。** dockerd 日志只说明 Docker 执行了 stop/force，不含发起者或原始请求。
- **`05:01:45.6816Z` 只是 90 秒倒推的近似算术，不是已实证的请求时间。** 不能把该时刻写成“已记录 stop 时间”。
- **为何 PID1 在 SIGTERM 后 90 秒仍未退出，未知。** 没有 handler/信号投递/wait 状态记录。
- **OOM 仍不能绝对排除。** kernel 查询 exit 1（第 6–8 行），不能作“无 OOM”的否定推断；services 只保留 62 条、筛出 6 条，完整 raw stdout 只有 SHA256（第 64–65 行），不能宣称 journal 完整。`pressure_indicator_records_count=0`（第 63 行）也不等于历史无压力。
- **不能绝对排除并发其他机制。** 但新记录已给出一个足够解释退出时序的直接机制，OOM 不再是必要解释。

## 4. 先前 unknown 的分层更新

| 层 | 更新后状态 |
|---|---|
| 事件/状态层 | 已确认：CID 8ebd…，boot 0145…，exit 137，OOMKilled=false，FinishedAt 05:03:16.289423094Z |
| 终止机制层 | **已部分确认**：Docker stop：signal 15，等待 90 秒，force；不再是完全未知 |
| 信号执行/运行时层 | force 与 exit137、shim disconnect 紧密吻合；但仍无 wait/signal 的逐信号记录 |
| 上游 API/调用者层 | **仍未知**：谁发起 stop、何时发起、为何发起，均无原始请求日志 |
| 根因/动机层 | **仍未知**：为什么在 Store 池初始化期间被 stop，未定位 |
| OOM 层 | 仍是强反证/非必要解释，但因 kernel 查询失败，不能写绝对排除 |

**根因本身没有改变：仍是 unresolved。** 改变的是：不能再说“终止机制完全未知”；应改成“Docker stop/force 机制已知，上游 caller/cause 未知”。

## 5. 最小建议

1. **只更新分层结论，不改旧报告/manifest。** 新 supplement 独立保存；原报告与 manifest 保持不动。
2. **报告措辞固定为：** “Docker daemon 记录了对目标 CID 的 stop：signal 15 后 90 秒未退出，使用 force；与 137/FinishedAt 吻合。发起 stop 的 API caller 与根因未定位。”  
   不要写“机制完全未知”，也不要写“API caller/起因已定位”。
3. **保留 90 秒算术标注：** 约 `05:01:45.68Z` 仅是倒推，不是已实证请求时间。
4. **不再远端调查/新实验。** 现有证据足以冻结 `root cause unknown` 并修正机制层；旧 formal 失败仍计失败，不改变分母。本补充不是 R1/R2 批准，也未批准任何计算。