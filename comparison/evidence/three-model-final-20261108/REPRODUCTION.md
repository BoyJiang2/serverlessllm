# CPU 复算与证据路径

本目录不需要 CUDA、Torch、vLLM、Ray、root 或权重。生成图需要现有 Matplotlib/Numpy；统计 helper 使用标准库。不要为复算启动模型、重建环境或重读 Store payload。

## 本地可移植复算

在本目录运行，NEW_DIR 必须不存在（脚本拒绝覆盖）：

```bash
python reproduce.py /absolute/NEW_DIR
```

脚本先校验 INPUTS.json 中全部字节 SHA，按原40 ID从 raw-replay 读取，不直接相信已派生 success 字段；与38份原 runtime-review 及65ee修订中的两个失败声明进行身份/版本/指标 join，重算冻结 V4 正式统计并要求与原输出相等。它不在本机重新证明 GPU/释放真实性；该证明来自原始报告和服务器完整只读 reviewer 重放。

随后检查 Kimi/GLM 既有数组、完整块、每行互斥阶段和、V4同源B2–B10，导出 comparison.json/csv 和7组PNG/SVG/PDF。Kimi/GLM 的科学结果沿用原报告及其已保存独立复算回执，本脚本不声称再次执行它们的原统计CLI或重审大监控。原统计CLI复现方法在 inputs/legacy/REPORT.md 的链接中。

本次在Windows现有Python3.13.1运行成功，reproduction.log保存原输出。跨Python版本的极末位浮点/PDF元数据/字体渲染可能不同，不要求不同平台整文件SHA相同。输入SHA必须完全相同。

## 服务器完整重审（可选，勿重复作为新实验）

本次实际执行目录：

```text
/home/boyjiang/sllm-v4-pro/analysis-nfs40-20261108-v2/
/home/boyjiang/sllm-v4-pro/results/nfs40-final-20261108-v2/
```

原始CPU命令（原输出已存在，若确需重放必须指定新输出）：

```bash
python3 /home/boyjiang/sllm-v4-pro/analysis-nfs40-20261108-v2/collect_v4_formal_results.py \
  --code /path/to/checkout-at-88cc71ec50c8668c26f26636d7d432c3351356fe \
  --evidence /home/boyjiang/sllm-v4-pro/evidence \
  --output /absolute/NEW_FULL_REVIEW_DIR
```

Collector要求无在途canonical作业、干净执行源88cc71e、原protocol bffa18…、65ee修订和完整原evidence。报告发布提交高于88cc71e，因此最终发布后的当前checkout不能直接拿来通过这个执行源门禁；先使用原执行提交的独立只读副本。不要为迁就重放修改源门禁、冻结协议或失败记录。

它会读约数百MB的小型原监控/缓存/请求证据，不读805GiB权重或877GB Store payload。原始v1资源汇总因gpu=null失败，v1脚本/日志/receipt原样保留；v2只将缺失采样显式保留，没有改变runtime资格规则。

## 原始归档

服务器 `results/nfs40-raw-evidence-20261108.tar.gz` 为40个attempt完整树+原journal+campaign complete+handles，SHA见 inputs/v4/raw-archive-receipt.json。只读归档的第一次writer完成tar后，在Python3.10的hashlib.file_digest处失败；随后对**同一个已完成包**用流式sha256并全读gzip/tar检查，不重建包或模型。该分析兼容性问题记录在receipt。

原生参考、Store完整验真、独立恢复、两轮服务等前置证据仍在仓库docs/evidence；正式原始包不替代它们。模型/Store资产保留原路径，报告不复制大资产。

## 文件完整性

MANIFEST.json列出本目录除自身外所有交付文件SHA/大小。发布回执是该manifest生成之后的外部文件，不参与自引用。可用标准库逐项验证：

```python
import hashlib, json, pathlib
base = pathlib.Path('.')
for row in json.loads((base/'MANIFEST.json').read_bytes())['files']:
    data = (base/row['path']).read_bytes()
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']
```
