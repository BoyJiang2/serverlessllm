# V11 — 三模型三路径切换实验（qjhs003）

本目录是 V11 实验的**可复现证据根**：冻结协议、原始 run 目录、以及从 run 数据生成的
`report.md`。所有结论都能在本目录内找到原生证据；未跑通的项目照实记为"未通过"。

## 环境（冻结）

| 项 | 值 |
|---|---|
| 机器 | qjhs003，8 × H20-3e 143771 MiB，~1.9 TiB RAM |
| 引擎 | vLLM 0.20.1 @ `132765e3560659ff63ebd236203672e991b70e08`，TP8，`enforce_eager` |
| 镜像 | `sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d` |
| 模型 | Kimi-K2.6 / GLM-5.1-FP8 / DeepSeek-V4-Pro（均 TF 冻结 payload + 原生 checkpoint） |
| 负载 | 三模型各自的 frozen request 集（算术 / 翻译 / fixed32 + 业务题 + added question），金标 + 逐 token 校验 + 8 rank 覆盖 |
| Host KV | LMCache 0.5.6 overlay `/data/brick1/boyjiang/lmcache-site`（0.5.7rc4 另装于 `lmcache057-site`） |

## 冻结产物

| 文件 | 内容 |
|---|---|
| `switch-sequences.json` | 9 条冻结切换序列（长度 7/8/10 各 3 条），每条覆盖全部 6 个非自方向；自切换 ≤1/4 且单独统计 |
| `protocol-hash.json` | `protocol_hash = d6b76f45…`（暂定：LMCache commit 未锁定，B1 未通过） |
| `env-check.json` | 环境核查：8 卡空闲、引擎/驱动/torch 版本、三模型 revision、TP8/2048 |
| `b1-preacceptance-part1.json` | LMCache MP HTTP API 合同：`/status` 200、`cache/clear l1` 200、`l2` 400、`num_l2_adapters=0` |

## 目录

```
runs/                每个 run 一个目录：qualification.json + switch-events.csv
                     + resource-samples.csv + store-ledger.csv + 逐段证据
report.md            由 make_report.py 从 runs/ 生成（不要手改）
make_report.py       报告生成器
v11_run.py           控制器：G1 基线 / A 组 / B1 组（同 Store 常驻）
v11_run_b2.py        控制器：B2 组（原生 Sleep，L1 与 L2 同组）
v11_backend.py       后端：从 Store 恢复 + 跑 frozen 请求 + 逐段落证据
v11_sleep_backend.py 后端：常驻 worker，命令文件驱动 run/sleep/wake/shutdown
```

## 复现

```bash
cd /home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11

# A 组（SLLM Store，同 Store 常驻，同一冻结序列）
python3 v11_run.py --group A --sequence seq-7-1 --rep 1

# G1 三模型基线门
python3 v11_run.py --group G1 --models KGD --rep 1

# B1 组（Store + Host KV；需要 8073/9100/5555 端口干净）
python3 v11_run.py --group B1 --sequence seq-7-1 --rep 1

# B2 组（原生 Sleep，L1 + L2 同组）
python3 v11_run_b2.py --sequence seq-7-1 --levels 1,2

# 报告
python3 make_report.py
```

## 判定口径

- **通过**要求同时满足：`qualification.json` 里 `passed: true`（段数齐全、无 failure）、8 rank 事件齐全、
  金标/逐 token 校验通过、最终 8 卡全 0 且观察 ≥60 s。
- **未通过**的组保留失败 run 目录、`failure.json`、后端与 server 日志，并在 `report.md` 写明根因；
  不删除、不重跑覆盖、不用"参数配好了"充当通过。
- 历史结果（2026-11-09 共享 Store 实验、`d3c10bda` 三模型报告）**不在本目录内、也未被改写**；
  本目录只新增 V11 的独立 run。

## 当前状态

见 `report.md` 开头的状态表（由 run 数据自动生成）。
