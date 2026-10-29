# C23 回归：正式代码改动前后的 spec、挂载与 Docker 参数（`tools/c23_regression.py`）

任何对 `runtime/active` 的改动都要先通过这项回归。每棵源码树都在独立的 Python 进程里运行它**自己的**
`workspace` / `materialize_control13` / `launch_control13`，互不影响：

| 比对 | 依据 |
|---|---|
| 冻结树（`deploy_tree` 从 C23 commit `f176e05` 重建）复现记录的 4 份 dispatch spec | `reviews/actual-controls/<C23>/` 中按索引哈希核验过的 headformal13、formal13、headservice13a、service13a |
| 冻结树为 formal13 生成的 `--mount` 集合等于真实容器的挂载 | `tools/fixtures/c23-formal13-mounts.json`：从 qjhs003 上 `tools/env-shell.sh` 抽出的 36 条挂载，该文件由 2026-10-27 对真实 formal13 容器的 `docker inspect` 生成（只取挂载，不取环境变量；记录了源文件 sha256） |
| 记录的 gate 哈希与 `formal-admission.json` 一致 | 同一 packet |
| 当前工作树与冻结树的 spec、挂载、Docker argv 以及 argv 代码块（AST）完全一致 | 两次独立派生 |

`tools/test_c23_regression.py` 另有反例：改一个模板值或改 argv 代码块，回归必须失败。

## 2026-10-29 结果

全部通过：4 份 spec 一致，formal13 挂载 36/36（0 缺失、0 多余），gate 哈希一致，当前树与冻结树四项一致。
其中当前树已包含 `launch_control13.py` 的正式侧互斥检查（有运行中的 `DEBUG-UNREVIEWED` 容器时拒绝启动）；
该检查位于 argv 代码块之前，回归证明它没有改变 spec、挂载或 Docker 参数。

改动后 `runtime/active/control-input-manifest.json` 中 `launch_control13.py` 的哈希已不一致：在重新生成清单并通过 R1
审阅之前，正式启动会按其自身的清单校验拒绝运行，`deploy_tree build --kind formal` 也会拒绝这一状态。原先依赖工作树
哈希的测试（`service_smoke_v1/test_docker_args.py`、`debug_stack_v1/test_debug_plan.py`）改为从 C23 commit 取冻结基线。
