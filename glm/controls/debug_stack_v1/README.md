# 调试栈 v1（只用于调试，永远不产生正式数据）

一条命令起停 head + 8 卡 worker 的原生 ServerlessLLM 栈，用于看服务、排查问题、开发计量。设计依据见
`deepseek-harness/default-workspace/P1-DESIGN-DRAFT.md`（方案 B）。

## 与正式路径的关系

- spec 模板、挂载规则、`docker create` 参数都来自冻结的 C23 源码：`debug_plan.py` 复用
  `service_smoke_v1/docker_args.py`，测试把挂载和 spec 与 `workspace.container_mounts()`、
  `materialize_control13.expected_spec()` 逐字节比对。正式侧的回归见 `tools/c23_regression.py`：从 C23 commit 重建的
  冻结代码复现记录的 4 份 dispatch spec 和 formal13 容器的 36 条挂载，当前工作树的 spec、挂载、Docker 参数与冻结版一致。
- **容器 spec 层**只允许以下差异（测试锁定）：run 编号 `sllm-debug-…`、标签 `sllm.purpose=DEBUG-UNREVIEWED`、
  持有者令牌与守护截止时间、`--pull=never`、argv 外层的 `timeout`、worker 改跑 `debug_service.py`、
  去掉 formal13 同样会去掉的 7 个开关、加两个调试开关（权重格式、是否发金标请求）、代码树与可写 `/r` 指向 `debug/<run>/`。
  这只是 spec 层的差异，**运行配置的差异更大，见下一节**。
- worker 不跑 `supervise_control13.py`：它要求 root 守护和监控心跳。`debug_service.py` 用与
  `bootstrap_control13.py` 相同的命令起 Ray worker、Store（只把 Store 绑到 node0 CPU）和 sllm server，
  注册配置和请求与 `validation_control13.run` 一致（测试从其 AST 取出原语句比对）。
- 调试源码树不包含 `run-manifest.json`，任何调试证据都不会带上正式 run 编号。
- 容器里跑的代码和 head/worker 模板都来自同一个 commit：`up --source-tree` 只接受 `tools/deploy_tree.py push`
  推送的 `code/source-debug-<commit12>-<树哈希12>/`，先按其收据逐文件核验（名字、种类、文件清单哈希都要对上，
  不许多文件、符号链接或 `run-manifest.json`），再复制到 `debug/<run>/source/` 并复核；`stack.json` 记录 commit 和树哈希。
- 正式与调试互斥由代码校验：调试侧 `up` 在有正式 run 容器时拒绝；正式侧 `runtime/active/launch_control13.py`
  在有任何运行中的 `DEBUG-UNREVIEWED` 容器时拒绝启动（候选改动：`control-input-manifest.json` 尚未重新生成，
  正式启动在重新生成清单并通过 R1 审阅前会按设计拒绝；C23 回归证明这处改动没有改变 spec、挂载或 Docker 参数）。

## 与正式配置的差异（调试结果不能与正式测量比较）

调试栈不是"只换了身份和目录的正式配置"。每次 `up` 把实际配置及其与 C23 的差异写进 `stack.json` 的 `configuration`
（`comparable_to_formal_measurements` 恒为 `false`）：

- 身份与目录：`sllm-debug-…`、`DEBUG-UNREVIEWED`、`debug/<run>/` 代码树与 `/r`。
- worker 入口：`debug_service.py`，不是 `supervise_control13.py` + `bootstrap_control13.py`；没有监控心跳、准入门、预算阶段和预注册的 trial 流程。
- 守护：调试宿主守护（见下）加容器内 `timeout`，不是正式的 root 截止守护。
- 开关：去掉 formal13 同样去掉的 7 个开关，加 `SLLM_DEBUG_WEIGHT_FORMAT`、`SLLM_DEBUG_QUERY`。
- Store 池：默认 safetensors 只建 64 MiB 池，而 C23 全程常驻一个 716 GB 共享池（SF 条件也在其旁运行）；
  `serverless_llm` 的 716 GB 池由本次 run 新建，不是 C23 跨 trial 共享的那个。小池或 SF-only 都是不同的调试配置。
- 请求：最多一次金标请求，不是正式的条件、顺序和重复。

## 独立宿主守护（模型启动的前提）

容器内的 `timeout` 只是第一层，不能代替宿主守护。`debug_guard.py` 由 root 的 systemd 定时器每分钟运行一次，
在被守护的容器之外、与 SSH 会话无关：只处理带 `DEBUG-UNREVIEWED` 且 run 编号为 `sllm-debug-…` 的容器，
过了 `sllm.deadline_unix`（寿命 + 600 秒）就 `docker stop -t 320` 后删除；没有截止标签的按最长寿命加余量处理；
正式容器和其他容器永远不碰，每次动作写入 `/var/log/sllm-debug-guard.jsonl`。每次扫描完成后写心跳
`/var/lib/sllm-debug-guard/heartbeat.json`；`docker ps` 失败时服务以失败退出、不写心跳，不会把"连不上 Docker"当成"没有到期容器"。

一次性安装（需要 root，在工具目录里）：`sudo bash controls/debug_stack_v1/install_debug_guard.sh`，脚本最后同步跑一次扫描并打印心跳。
GPU 模式的 `up` 要求定时器处于 active、服务运行的正是 `/usr/local/lib/sllm-debug-guard/debug_guard.py`、
该文件及目录属 root 且不可被组/他人写、内容与本工具版本完全一致、心跳属 root 且在最近 180 秒内，否则拒绝启动。
`--head-only` 不加载模型，只记录守护状态。`python3 debug_guard.py --dry-run` 可以用普通账号查看守护此刻会处理哪些容器（不写心跳）。

## 用法

```bash
# 本机：把要跑的 commit 推成源码树（打印目标目录），再部署工具（都不启动任何东西）
python tools/deploy_tree.py push --commit <commit>
python controls/debug_stack_v1/deploy_debug_stack.py
# qjhs003：只起 CPU 上的 Ray head（无需预订）
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py up --head-only --seconds 300 --source-tree <源码树目录>
# qjhs003：完整 GPU 栈（需要你在预订系统里的有效预订）
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py up --seconds 3600 --source-tree <源码树目录> \
    --booking-id <预订ID> --booking-end 2026-10-29T14:00:00+08:00 [--weight-format serverless_llm] [--no-query]
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py status --run-id <run_id>
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py down --run-id <run_id>
```

`up` 依次检查：UID 2147、没有正式 run 容器、没有别的调试栈、（GPU 模式）GPU 空闲且寿命加 10 分钟能放进预订、
源码树完好、（GPU 模式）宿主守护就绪；然后复制源码树，起 head、读 IP、起 worker。每次 `docker create` 之前都核对镜像 ID，
缺失时从存档恢复（`tools/restore_original_image.py`，head 与 worker 各留一份收据）。这里不假设运行中的容器能保住镜像，
也不假设节点按固定周期清理：有确切时间的清理事件只观察到两次（2026-10-28 19:50:01、20:20:01），之后多次在 create 前
发现镜像已被删除（包括 2026-10-29 01:10:58 到 01:22:18 之间，以及 05:43 之前），来源和规律都未确定。
`down` 只删除持有者令牌一致的容器，并确认 GPU 进程清空。

默认权重格式是 safetensors（64 MiB 池）；`serverless_llm` 会建 716 GB 的锁页池，请只在需要时使用。两者都是调试配置，见上一节。

## 已验证

- 本地 23 项 CPU 测试：挂载、spec、formal13 去掉的开关、注册配置和金标请求都与冻结的正式源码逐项一致；
  拒绝条件、预订窗口、持有者核对、源码树不带正式编号、命令行入口。
- 2026-10-28 21:39–21:42，qjhs003，`up --head-only`（工具目录 `code/debug-stack-7bfac31f5d8a`，
  run `sllm-debug-20261028T133926Z-9b025bf8`）：镜像被清理后从存档恢复并核对 ID，head 立刻启动，19 条挂载全部存在，
  调试 cgroup slice 无需 root 即可创建，`Ray runtime started.`；`status` 核对归属；`down` 后无残留。

- 2026-10-28 23:06–23:17，qjhs003，`up --borrow`（无预订借用，依据仓库所有者的指示；run
  `sllm-debug-20261028T150631Z-fe3868b3`）：head 就绪后 worker 加入 Ray，Store 绑 node0、sllm 健康，safetensors 注册，
  `business_arithmetic` 与金标逐 token 一致（首个请求含加载 514 秒，服务中每卡约 97.9 GB），`down` 后无残留、无 GPU 进程。
- 第一次 GPU 冒烟（run `sllm-debug-20261028T145947Z-47f0c788`）失败并安全清理：worker 在 head 容器启动 2 秒后加入，
  两边共用 Ray 会话目录（非 head 节点从 GCS 取 temp_dir，`/r` 又是同一宿主目录），抢同一个 plasma 套接字名，
  worker raylet `bind: Address already in use` 后中止。现在 `up` 先等 head 打印 `Ray runtime started.` 再起 worker。

- 上一次冒烟里 `docker stop` 的 TERM 被 `timeout` 发给了整个进程组，Store 被 TERM 直接终止（退出码 −15），没有走正式的
  SIGINT 宽限。改用 `timeout --foreground` 后复验（23:19–23:20，工具目录 `code/debug-stack-989759af4420`，run
  `sllm-debug-20261028T151917Z-7ee0d862`，`--no-query`）：控制面健康后 `down` 6 秒完成，Store 收到 SIGINT 后正常退出（0），
  没有强杀，无残留、无 GPU 进程。加载模型的路径在这两个版本之间没有改动。
- 2026-10-29 00:54–00:56，qjhs003，`--source-tree` 接入（工具目录 `code/debug-stack-3472f1d05e0e`）：传入工具目录被拒、
  没有创建任何 run 目录；用 `2aec168` 推送的 `code/source-debug-2aec16862725-ad882e22a5cc` 起 `--head-only`
  （run `sllm-debug-20261028T165425Z-945abe15`）：`stack.json` 记录 commit 与树哈希，`debug/<run>/source/` 与推送树
  `diff -r` 完全一致、无 `run-manifest.json`，镜像被清理后恢复，head 4.2 秒就绪，`down` 后无残留。本地测试 29 项通过。
- 2026-10-29 01:10–01:11，qjhs003，宿主守护接入（工具目录 `code/debug-stack-0916485e90ae`）：普通账号 `debug_guard.py --dry-run`
  返回空；未安装守护时 GPU 模式 `up --borrow` 被拒绝并列出缺失项，没有创建 run 目录；`--head-only`
  （run `sllm-debug-20261028T171050Z-bfeeba12`）的容器带 `sllm.deadline_unix=1793208350`（启动时刻 + 300 秒寿命 + 600 秒余量），
  `stack.json` 记录守护未就绪及原因、`comparable_to_formal_measurements=false` 与 5 项配置差异；运行中 dry-run 仍为空；`down` 后无残留。
- 2026-10-29 01:22–01:24，加入心跳后复验（工具目录 `code/debug-stack-4061b0c838ce`，run `sllm-debug-20261028T172218Z-1f5e99af`）：
  GPU 模式的拒绝原因多出心跳两项，仍不建 run 目录；`install_debug_guard.sh` 通过 `bash -n`；镜像在 01:10 之后又被删除，
  create 前从存档恢复并核对 ID（收据 `backup restored and exact image verified`）；截止标签 1793209038 同样等于启动时刻 + 900 秒；
  `down` 后无残留。本地测试：`debug_stack_v1` 40 项、`service_smoke_v1` 6 项、`runtime/active` 263 项、`tools` 下
  `test_deploy_tree` 12 项与 `test_c23_regression` 6 项全部通过；`tools` 整体发现时另有 21 项在干净的 HEAD 导出上同样失败
  （`test_control_transport13` 17 项找不到 `runtime/active` 的模块，`test_execution_contract` 4 项仍按旧的 G1-only scope 校验
  `run-manifest.json`），与本次改动无关。
- 2026-10-29 02:09，仓库所有者用 sudo 安装了宿主守护：定时器 active/enabled，安装时的同步扫描成功并写了心跳；已安装的脚本与
  工具目录 `code/debug-stack-4061b0c838ce` 里的 `debug_guard.py` 哈希一致（`3d70fc4a…`），`guard_state()` 没有问题项。
  05:44 用一个截止时间已过的 CPU 测试容器验证（调试标签、无 GPU、无网络，run `sllm-debug-20261028T214451Z-64330926`）：
  守护在创建后约 36 秒 `docker stop`（返回 0）并删除，`/var/log/sllm-debug-guard.jsonl` 记录原因 `deadline label`，
  心跳记下 1 次动作，之后没有调试容器残留；创建前镜像又一次不在，已从存档恢复。同一天本地对 commit `ad5b0b6`
  `deploy_tree build`：调试树可以构建（83 个文件）；正式树被拒绝，原因是清单里的 `launch_control13.py` 仍是 C23 的哈希
  （它是 C23 以来 `runtime/active` 唯一改动的文件）。

## 还没验证的

- `--weight-format serverless_llm`（716 GB 锁页池）尚未在调试栈上跑过。
- 预订只核对你给的 ID 和结束时间，没有调用预订 API；`--borrow` 只做空闲检查，不是预订。
- 装好守护之后还没跑过 GPU 模式的调试栈；2026-10-28 的两次 GPU 冒烟发生在守护实现之前，当时只有容器内 `timeout`。
- 两侧互斥都是启动前检查，没有跨两侧的锁：两边几乎同时启动时，检查与 `docker create` 之间仍有窗口。
- 正式侧的互斥检查只在本地测试和 C23 回归里验证过，还没有在正式启动中运行；正式使用前需要重新生成清单并通过 R1 审阅。
