# CPU复算与绘图复现

不需要再次root收集、启动模型/Store、安装依赖或读取权重。原始实验数据、失败和金标不变。

## 原服务器路径

统计源码：共享checkout的 benchmarks/analyze_kimi_four_condition.py（实际执行a43734f，SHA043aa4c8a7f8db4f1fdc03afd2a874f9cb1cf916315208cbb13aa722a240f7d5）；collector后续fdc提交没有改此文件。输入v3 SHA4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801。使用新输出文件名，避免覆盖本次结果。

```bash
python3 -B /home/boyjiang/kimi-new/ServerlessLLM/benchmarks/analyze_kimi_four_condition.py /home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-four-analysis-input-snapshot-v1-20261107/input-manifest-v3.json /absolute/new-analysis.json --manifest-sha 4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801
```

## 离开原服务器路径复算

完整仓库的 kimi/evidence/final-kimi-glm-comparison-20261107 已有459份原始小元数据、v3manifest、外部provenance与统计源码副本。delivery-file-index.json给出原server路径→仓库相对路径及每文件SHA。只重定位消费者读取的refs.path；所有ID、qualification、哈希、时间与配对规则不变，原manifest不改。保留Git原始文件字节，勿批量转换换行或覆盖并发文件。

在现有Python中执行下列脚本（BASE为该已存在证据目录，OUT为新的空输出目录；无需新checkout/env）。Linux/WSL可用 python3 - BASE OUT <<'PY'，Windows可将同一脚本保存后用现有Python调用。

```python
import sys, json, hashlib, pathlib, subprocess
base, out = map(lambda x: pathlib.Path(x).resolve(), sys.argv[1:3])
out.mkdir(exist_ok=False)
src = base / 'source/analyze_kimi_four_condition.py'
assert hashlib.sha256(src.read_bytes()).hexdigest() == '043aa4c8a7f8db4f1fdc03afd2a874f9cb1cf916315208cbb13aa722a240f7d5'
original = base / 'analysis-input/input-manifest-v3.json'
assert hashlib.sha256(original.read_bytes()).hexdigest() == '4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801'
index = json.loads((base / 'delivery-file-index.json').read_text(encoding='utf-8'))
paths = {e['server_original']: (base / e['path']).resolve() for e in index['files']}
data = json.loads(original.read_text(encoding='utf-8'))
refs = [r for run in data['runs'] for r in run['refs'].values() if r]
refs += [b['final'] for b in data['blocks']] + data['provenance']
for ref in refs:
    path = paths[ref['path']]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == ref['sha256']
    ref['path'] = path.as_posix()
manifest = out / 'relocated-manifest.json'
manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
subprocess.run([sys.executable, '-B', str(src), str(manifest), str(out / 'analysis.json'), '--manifest-sha', sha], check=True)
```

本次已经在既有Windows Python3.13.1实际完成同方法的467项ref重定位/逐SHA验证，并运行真实CLI，对cohorts、completeblocks、版本、两族比较/CI/p与descriptives逐项复核一致（原服务器Python3.10.12）。新路径/Python/provenance使整个输出文件SHA不同，不能比较全文件hash来判断数值复算；详细比较字段与容差见delivery-portability-recompute-verification.json。此验证仅CPU，没有新模型样本。

## 图表

使用本目录的已保存JSON和现有Matplotlib，各脚本最后一个参数均为新的输出目录：

- plot_kimi_glm_comparison_20261107.py ANALYSIS_JSON GLM_PLOT_INPUT_JSON NEW_DIR
- plot_kimi_glm_all40_20261107.py ANALYSIS_JSON GLM_ALL40_LEDGER_JSON NEW_DIR
- plot_kimi_glm_reset_cost_projection_20261107.py COST_AUDIT_JSON NEW_DIR
- plot_kimi_observed_loading_spans_20261107.py ANALYSIS_JSON COST_AUDIT_JSON LOADING_AUDIT_JSON NEW_DIR

图来源/源码/输入/输出SHA见各provenance。PNG均已视检，PDF/SVG从同一Matplotlib figure导出；不要求不同平台PDF/SVG元数据或渲染字节完全相同。N图是forced-reset模型，不是N>1实测。加载图跨度可能嵌套，不相加/相减成固定开销；fixed32是wholeHTTP，不是纯decode。
