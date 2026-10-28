# 调试栈 v1（只用于调试，永远不产生正式数据）

一条命令起停 head + 8 卡 worker 的原生 ServerlessLLM 栈，用于看服务、排查问题、开发计量。设计依据见
`deepseek-harness/default-workspace/P1-DESIGN-DRAFT.md`（方案 B）。

## 与正式路径的关系

- 不修改 `runtime/active`。spec 模板、挂载规则、`docker create` 参数都来自冻结的 C23 源码：
  `debug_plan.py` 复用 `service_smoke_v1/docker_args.py`，测试把挂载和 spec 与 `workspace.container_mounts()`、
  `materialize_control13.expected_spec()` 逐字节比对，并先核对这些文件与 C23 已执行源码的哈希一致。
- 只允许以下差异（测试锁定）：run 编号 `sllm-debug-…`、标签 `sllm.purpose=DEBUG-UNREVIEWED` 与持有者令牌、
  `--pull=never`、argv 外层的 `timeout`、worker 改跑 `debug_service.py`、去掉 formal13 同样会去掉的 7 个开关、
  加两个调试开关（权重格式、是否发金标请求）、代码树与可写 `/r` 指向 `debug/<run>/`。
- worker 不跑 `supervise_control13.py`：它要求 root 守护和监控心跳。`debug_service.py` 用与
  `bootstrap_control13.py` 相同的命令起 Ray worker、Store（只把 Store 绑到 node0 CPU）和 sllm server，
  注册配置和请求与 `validation_control13.run` 一致（测试从其 AST 取出原语句比对）。
- 调试源码树不包含 `run-manifest.json`，任何调试证据都不会带上正式 run 编号。

## 用法

```bash
# 本机：把工具部署成一个按哈希命名的目录（不启动任何东西）
python controls/debug_stack_v1/deploy_debug_stack.py
# qjhs003：只起 CPU 上的 Ray head（无需预订）
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py up --head-only --seconds 300
# qjhs003：完整 GPU 栈（需要你在预订系统里的有效预订）
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py up --seconds 3600 \
    --booking-id <预订ID> --booking-end 2026-10-29T14:00:00+08:00 [--weight-format serverless_llm] [--no-query]
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py status --run-id <run_id>
python3 <工具目录>/controls/debug_stack_v1/debug_stack.py down --run-id <run_id>
```

`up` 依次检查：UID 2147、没有正式 run 容器、没有别的调试栈、（GPU 模式）GPU 空闲且寿命加 10 分钟能放进预订；
然后部署源码树、从存档恢复镜像（`tools/restore_original_image.py`）、立刻起 head、读 IP、起 worker。
只有运行中的容器能挡住 qjhs3 的定时清理，所以恢复后马上启动。`down` 只删除持有者令牌一致的容器，并确认 GPU 进程清空。

默认权重格式是 safetensors（64 MiB 池）；`serverless_llm` 会建 716 GB 的锁页池，请只在需要时使用。

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

## 还没验证的

- `--weight-format serverless_llm`（716 GB 锁页池）尚未在调试栈上跑过。
- 预订只核对你给的 ID 和结束时间，没有调用预订 API；`--borrow` 只做空闲检查，不是预订。
- 不建 root 守护；寿命兜底靠容器里的 `timeout`（worker 300 秒强杀宽限，长于 Store 的 240 秒退出宽限）。
