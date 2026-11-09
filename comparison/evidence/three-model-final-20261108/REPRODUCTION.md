# CPU重算与验收（2026-11-09修订）

只使用既有Python、Matplotlib和Numpy；不需要CUDA、Torch、vLLM、Ray、root、模型、Store池或权重。不要为复算创建推理环境。NEW_DIR必须不存在，脚本拒绝覆盖。

```bash
python reproduce.py /absolute/NEW_DIR
python -m unittest discover -s . -p test_harmonized_analysis.py -v
```

第一条先核验INPUTS.json全部原输入SHA；重做V4原40项/38合格/2原失败的review join和冻结正式统计并要求与旧输出完全相等，保留Kimi原n8、GLM C23 n6、V4同源展示n9与全部描述性样本数。历史正式V4各对n10/n9不改。

新增harmonized_analysis.py仅做端点算术及范围标注：

- GLM原192条Worker端点与phases交叉验证；模型内三个阶段各rank重算，不将最新C判定升级成统一边界。
- V4全部38合格尝试按原始phase事件匹配八rank、PID/startticks/boot/time namespace/clock；各包络必须等于旧metrics；主表仍选B2–B10的36项。
- Kimi两次独立新补采均按原rank端点重算，各自保留16观测和16缺测槽位；合格/失败/未开始分列，完整块n0不变。历史f7不替代八rank值。
- Prep只用真实端点拆分。V4三段里程碑逐次之和必须等于原Prep；纯分量无证据则null。Kimi环境/建池处于原Prep外，不加总冒充Controller。
- 随机12次来自独立已验收cohort，六个非自切换方向各2次；字节和块数优先。不能与历史样本拼接。
- 5项小测试验证all-rank包络与最大单rank不同、缺rank/缺端点/重复rank为null、反向端点报错。

产物包含原A/ECDF/120编号图、按97f0359原绘图代码恢复的图3 Prep+A和图6互斥事件分解、两张分模型阶段PNG/SVG/PDF、逐rank/逐attempt/阶段均值、Prep分项/里程碑、独立Kimi补采和随机方向缓存表。图3与图6已按用户指定版式恢复正文引用和生成代码；三模型Prep范围差异仍写入图注，图后保留数值表。混合图4/旧图5仅历史归档，不作为统一边界数据。

实际分模型绘图消费者额外检查（使用本目录derived，不触发模型）：

```bash
python figure5-render-check.py
```

此脚本执行reproduce.py中的实际绘图循环与save函数，校验每模型12个阶段柱、明确非共同加载标签、所有主集共同指标n0/null。它只更新两张分模型阶段图与自己的回执。

## 本轮必要校验

harmonized-reproduction.log保存成功CPU命令、5项测试和消费者检查；VALIDATION.json的harmonized_revision列明范围，旧验证历史保留。comparison.json的全部旧数组/主集/正式统计必须与重算相等；新增逐rank数据量1552行（GLM576、V4912、Kimi两个新补采各32槽），不能把它当实验次数。随机12行单独核六方向、阶段统计的n为attempt数。

MANIFEST.json绑定整个交付目录除自身及Python临时缓存外的路径/大小/SHA；INPUTS.json只绑定不可改的输入。跨平台PDF时间元数据/字体可以不同，数值、原输入SHA和资格不可不同。新代码/输入/输出发布后与服务器及远端提交独立核对。

## 原始取证与历史重审范围

本轮没有重复扫描GLM的1.07GB资源监控或重读模型/Store payload；优先复用已完成的loading-boundary、controller-history审计。V4新增读取的是38项小型事件/准备收据和固定源码，收集器副本在inputs/harmonized/sources/，原始路径与SHA在report-boundaries-20261109.json.sources。首次CPU收集因engine-src子目录定位失败停止、未写输出；修正已存在固定源码路径后成功，无模型失败或GPU重试。

本轮两个临时CPU重算失败分别为编辑前Windows默认编码读取失败、撤除旧绘图块后一个局部变量缺失；均在本机修正，正式成功重算使用独立新输出，没有覆盖原数据或GPU运行。旧/临时目录位于报告外，不作为实验样本。

原V4完整只读reviewer重放已完成，绑定执行源88cc71e；结果在inputs/v4/results/及原收据。原始归档/home/boyjiang/sllm-v4-pro/results/nfs40-raw-evidence-20261108.tar.gz为56,908,305字节，SHA见inputs/v4/raw-archive-receipt.json；不用为本轮补图重复运行reviewer或转换。此前collector-v1的gpu=null CPU错误、v2缺测处理、全部模型失败与独立生命周期证据均保留，不能改成新运行。

GLM原CPU分析来源447765a、后续loading-boundary/controller-history输入见METRIC-CROSSWALK.md；新补测候选不是历史Controller数据。原Kimi/GLM统计CLI的历史复算方法保留inputs/legacy/REPORT.md；本脚本不声称重执行其GPU验收或大监控扫描。

## 待补采

共同完整loader主指标的三个历史主集都未获统一功能边界资格。原Worker阶段不是零，但也不是共同指标；详见FOLLOWUP.md。任何新GPU任务先形成具体执行单、沿用已有授权及停止规则，占GPU前告知；报告发布不以补齐理想柱图为前提。
