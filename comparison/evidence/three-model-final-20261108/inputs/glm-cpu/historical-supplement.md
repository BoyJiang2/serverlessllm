# GLM历史监控与日志：CPU补充复算

本次无GPU查询、模型启动、Store池或权重读取；仅低优先级流式读取历史监控。原40项35成功/5失败不变，历史失败与C23主配对不替换。

## 可补齐的已有证据

- 原监控1070633372字节留在qjhs003，没有下载到本机。精确路径、整文件SHA、执行函数SHA及52个窗口见historical-monitor.json。
- 9647/9647条有八卡显存值，缺失0、外来GPU进程标记0；最大相邻采样间隔3.689146886秒，与当时审计逐项一致。
- 全批每卡NVML采样高水位99293 MiB；自有slice的memory.current采样峰1553848774656字节（1447.134 GiB），含文件缓存，不是进程RSS或连续精确峰。逐卡峰非同时值，不相加成整机同时峰。

| 条件 | C23描述n | 准备开始→记录完成均值/s | 完整B5–B10 n=6同区间/s | 该尝试区间内逐卡峰范围/MiB | 自有cgroup峰/GiB |
|---|---:|---:|---:|---:|---:|
| safetensors_disk_cold | 6 | 575.095693 | 575.095693 | 99291–99293 | 1437.952 |
| safetensors_disk_hot | 6 | 594.722176 | 594.722176 | 99291–99293 | 1447.134 |
| store_disk_cold | 7 | 279.157305 | 276.402988 | 99223–99225 | 1447.013 |
| store_pool_hit | 7 | 281.920955 | 281.715133 | 99225–99225 | 1447.049 |

以上区间严格为complete_ns−preparation_begin_ns，包含缓存准备、三道请求、删除及至少60秒观察，排除之前公共环境/建池，也不保证包含complete_ns之后的持久化、退出或父进程清理。**不能直接填成与Kimi/V4同定义的完整controller elapsed**；原三模型图该格继续缺测，可另加本表。GPU/cgroup按该尝试窗口选样；另有startup_A窗口原始摘要供核查。

本批共享worker supervisor区间为11204.882661秒，head supervisor为11217.540113秒，起止与同boot身份已核验。它们跨越本批26项且相互重叠，不按条件均摊、不相加，也不冒充逐项controller成本。

## 转换成本：已有观测而非全部未知

历史rawexport容器StartedAt→FinishedAt：**505.467441929秒**；派发→八rank最后预热结束：**403.180093388秒**。两者重叠，不能相加。
八rank事件嵌入字节全部匹配原SHA，容器身份与Docker退出记录相符，Docker UTC区间独立复算容差1微秒。覆盖加载、导出、后处理、预热与退出；不是纯save、纯I/O或GPU内存积分。旧容器成功不追认任何历史失败。

## 确实仍需新增测量的项目

| 项目 | 旧证据限制 | 如要补齐 |
|---|---|---|
| fixed32独立客户端HTTP耗时 | 原选定记录没有独立begin/end；后端事件不能冒充客户端耗时 | 新受审小批请求计时，独立新ID，不替换旧40项 |
| 与Kimi完全同定义的f7包络 | GLM已有八rankphase，但起止函数定义不同 | 如必须同口径，重新布点采样；否则保留GLM既有phase独立展示 |
| 纯Store保存/纯填池及资源积分 | 505.467秒是整容器，不能剥离缺失分量 | 专门边界采样；优先复用现有资产，不为补表重复复制Store |
| N>1持续驻留摊销 | 原试验逐条件复位，无连续复用轨迹 | 新协议、预算、守护和审阅，不能从混杂Prep推回本次数 |
| 与其他模型相同的完整controller边界 | 本次仅补回helper记录区间 | 若统一定义所需端点未记录，未来新批加入；不得改名填旧空格 |

新增实验另行授权与准入，本次没有执行。缺同口径不等于历史完全没做。

## 复算与身份

```text
python -m unittest discover -s glm/tools -p test_final_costs.py
python glm/tools/historical_costs.py --c23-supplement
```

历史流式采集只调用final_costs.historical_monitor；historical-monitor.json绑定函数/源文件与监控SHA。每条结果按冻结ledger的result_sha256核验。supplement JSON列出全部26项原值和输入SHA；代码不覆写原成本报告。
