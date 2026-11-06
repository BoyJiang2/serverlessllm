# 四条件审阅 — 收口记录（2026-11-06 深夜）

## 当前进行中的正式运行（只读观测，未干预）

```
host            qjhs003 (qjhs3), HEAD 26b79272f4e80a12ed58f552b912d265a748d5d6, tracked clean
unit            kimi-four-v1-b01-retry02-formal-controller.service           running
unit            kimi-four-v1-b01-safetensors_disk_hot-retry02-controller.service running
container head  kimi-four-v1-b01-safetensors_disk_hot-retry02-head
container worker kimi-four-v1-b01-safetensors_disk_hot-retry02-worker
GPU             8 x 411 MiB each — 仅上下文量级占用，尚未确认权重加载阶段
runs dir        /data/brick1/boyjiang/kimi-k2.6/performance-runs/kimi-four-v1-b01-safetensors_disk_hot-retry02
```

**运行与释放观察期间：不改代码、不做 Git、不做同步。** 以上仅 read-only 记录。

## 本轮审阅的最终定级

| 项 | 定级 | 状态 |
|---|---|---|
| F7-1 `.replace()` 误伤嵌套 `--property=RemainAfterExit=yes` | 确定缺陷，b02 直接原因 | 已由 `26b7927` 最小修复 |
| 清理段断言无消息 | 可诊断性不足（非功能错误） | 待补，见下 |
| F2-1 池驻留证据充分性 | 待核验 | 先查现有回执/加载事件/读取证据，不预设新接口 |
| F1-1 清理竞态 | 待验证假设 | 不预设加宽限 |
| F1-2 create_end 顺延 / F2-2 跨条件污染 / F4-3 空 pinned_identities / F5-1 预算耦合 / F6-2 提前返回 | 待验证假设 | 先用现有回执或 CPU 测试排除 |
| F4-1 GPU 字典相等 | 脆弱性（P3） | 现状可用 |
| F6-1 成功率分母 | 统计口径 | 报告需列计划/启动/合格/未启动四数 |
| F7-2 链式替换 / F8-1 回归测试 | 已过时或已缓解 | `26b7927` 已加"实际执行 BLOCK_BODY 且内嵌代码字节不变"测试 |

已撤回的错误结论：F3-1/F3-2（终点含 IPC / 含进程创建）、F3-3（默认 True 会误判通过）。

## 补消息的候选位置（生成链尚未追清）

以下只是**候选位置**，不是已确认的直接来源。`s4a_root_controller.py` 里存在形态相似的断言代码，但四条件控制器是否由它直接生成、还是经由其他模板或中间产物，尚未沿生成链确认；不能因为"找到了相似代码"就对其做修改，否则可能改到其他探针。

候选文件：`sllm_store/tests/s4a_root_controller.py`

```
305  assert final['status'] == 'KERNEL_CLEANUP_RELEASE_PASS' and not final['failures'] and final['quiet_seconds'] >= 60
425  assert gpu() == {'used_mib':0,'processes':[]}
524  同 305（另一分支）
525-530 observer 覆盖率与 monitor 字段
531-532 unit MainPID 归零
534  assert released == {'used_mib':0,'processes':[]}
537  assert not pathlib.Path('/sys/fs/cgroup/system.slice/docker-'+cid+'.scope').exists()
538-541 pinned_identities + observer_identity 的 startticks 校验
```

执行体与候选文件中的同名断言**形态相似，但变量名与空格不同**（`final`→`result`、`== 'X'`→`=='X'`）。这**不足以证明**存在 minify 步骤，也不足以证明 `s4a_root_controller.py` 就是四条件控制器的直接来源——需要沿生成链（谁写出子单元正文、谁压缩、谁改写变量名）逐段确认。

**SHA 失效范围必须按实际模板确定**，不能据一处检查外推：先在生成链上确认该断言属于哪一段模板/角色正文，再重新生成受影响的哈希与执行产物。若最终确认它属于 role body，则 `prepare_kimi_four_condition.py:render()` 中

```python
assert hashlib.sha256(body.encode()).hexdigest() == spec["role_body_sha256"][name]
```

会使 `role_body_sha256` 变化，进而 spec / command / bundle 的 sha 逐一失效——但**具体失效哪几个，取决于实际改动落在哪一段**，不做全局推断。

## 本轮结束后的动作顺序

1. 取 retry02 回执（controller-final / controller-failed、sample-validity、cleanup-result、guard/observer final）。
2. 若仍为空 `AssertionError`：按里程碑补消息 → 重新生成 spec/command/bundle → CPU 测试 → 独审 → 版本核验。
3. 沿生成链确认断言所在模板后，**只重新生成受影响的哈希与执行产物**（范围以实际模板为准，不假定全部失效）；修订记录写明：只加消息，判据/期限/失败停止不变；旧 bundle 与历史失败保留不覆盖。
4. 不增加宽限、不放宽释放标准、不重写生成器。

## F2-1 待查清单（只读，不预设新接口）

- `construct_cache()` 的 `loaded` 来源（`load_into_cpu` 返回？日志解析？）
- Store 侧是否有 `wait_until_in_cpu` 式确认回执（b02 公开副本无 `pool-confirmations.json`，需查 root-only 运行目录）
- 池驻留是否只靠日志黑名单，还是另有计数
- 页缓存 mincore 证据不适用于池驻留，不能互相顶替
