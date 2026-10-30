# 按 commit 构建与部署源码树（`tools/deploy_tree.py`）

一个 run 用到的源码树只由一个 git commit 决定：字节直接取自 git 对象（`ls-tree` + `cat-file`），
不经过工作区、未跟踪文件或 `.gitattributes` 转换，所以任何部署过的树都能从它的 commit 原样重建。

## 两种树

- `formal`：按冻结的 `package_inventory13.reviewed_files` 规则——`control-input-manifest.json` 的各行，
  加上该清单本身和 `REVIEW-PROMPT.md`，多一个少一个都拒绝。**这里只构建和比对，不推送**；
  正式部署仍走需要 R1 批准的 `runtime/active/deploy_control13.py`。
- `debug`：同一 commit 的 `runtime/active`，去掉 `run-manifest.json`（正式 run 身份），
  加上同一 commit 的 `controls/debug_stack_v1/debug_service.py`。推送到 qjhs003 的
  `code/source-debug-<commit12>-<树哈希12>/`：按内容命名、先写 `.partial` 再原子改名，
  目标已存在时只重新核验、不覆盖；推送后在服务器上逐文件核对哈希，并确认没有多余文件和符号链接。

## 用法

```bash
python tools/deploy_tree.py build --kind formal --commit f176e05     # 本地构建并打印收据（可加 --out 目录）
python tools/deploy_tree.py verify-receipt reviews/actual-controls/<run>/executed-source-archive.json
python tools/deploy_tree.py verify-snapshots                         # 只读：重建 qjhs003 上全部 executed-source 树并比对
python tools/deploy_tree.py push --commit HEAD                       # 只推 debug 树
```

## 历史回归（2026-10-29）

- 本地：C23（`f176e05`）的已执行源码收据 81 个文件逐字节重建一致，清单哈希一致；
  树选择规则与该 commit 自带的 `reviewed_files` 等价（含多余文件被拒的反例）。
- qjhs003：`validation/controls/` 下 17 个已归档的 executed-source 树（2026-10-26 至 C23）全部与各自
  `deployment.json` 记录的 commit 重建结果一致：共 1091 个受审文件逐字节相同，0 缺失、0 改动、
  清单哈希全部一致。唯一的差异是 161 个 `__pycache__/*.cpython-310.pyc`（宿主机 Python 导入时写入），
  与 `reviewed_files` 一样不计为源码。
- 推送：`2aec168` 的 debug 树部署为 `code/source-debug-2aec16862725-ad882e22a5cc`（83 个文件在服务器上核验）；
  再次推送返回 `existing` 并重新核验，没有覆盖。

## 调试栈接入

`controls/debug_stack_v1/debug_stack.py up --source-tree <code/source-debug-…>` 只接受本工具推送的树：
目录名、种类、文件清单哈希与收据一致，逐文件核验，不许多文件、符号链接或 `run-manifest.json`；
随后复制到 `debug/<run>/source/` 并复核，head/worker 模板也从这份副本读取。2026-10-29 在 qjhs003 用
`2aec168` 的树完成 CPU `--head-only` 验证（见 `controls/debug_stack_v1/README.md`）。

`tools/test_deploy_tree.py` 覆盖上述本地部分，以及：git 原始字节、构建确定性、拒绝符号链接与子模块、
正式树不可推送、debug 入口必须来自同一 commit、快照比对能发现缺失/多余/改动/符号链接/清单不符。
