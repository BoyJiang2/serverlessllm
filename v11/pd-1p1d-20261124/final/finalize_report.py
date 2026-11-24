import csv
import hashlib
import json
from pathlib import Path
from statistics import median
import sys

folder = Path(sys.argv[1])
report = folder / 'FINAL-1P1D-S1S2S3-SUMMARY.md'
audit = json.loads((folder / 'evidence-index.json').read_text(encoding='utf-8'))
proof = json.loads((folder / 'store-restore-evidence.json').read_text(encoding='utf-8'))
assert audit['through'] == 'B1' and audit['passed'] and audit['formal_switches'] == 36
assert len(proof['starts']) == 48
with (folder / 'segments.csv').open(encoding='utf-8', newline='') as stream:
    rows = list(csv.DictReader(stream))
assert len(rows) == 42
base = {name: [r for r in rows if r['baseline'] == name and r['segment'] != '0']
        for name in ('B1', 'B2')}
assert all(len(group) == 18 for group in base.values())
summary = {}
for name, group in base.items():
    formal = [r for r in audit['runs'] if r['case'][0] == name and r['case'][2] is None]
    summary[name] = {
        'initial_count': 3, 'switch_count': len(group),
        'golden_count': sum(len(r['segments']) for r in formal),
        'median_total_s': median(float(r['total_s']) for r in group),
        'median_net_s': median(float(r['net_s']) for r in group),
        'host_hit_512_count': sum(seg['host_hit_tokens'] == 512 for r in formal for seg in r['segments'][1:]),
        'runs': [{'run_id': r['run_id'], 'common_zero_seconds': r['common_zero_seconds'],
                  'host_min_available_gib': r.get('host_min_available_gib'),
                  'raw_result': r['receipt']} for r in formal],
    }
    assert all(r['common_zero_seconds'] >= 60 for r in formal)
    print(name, json.dumps(summary[name], ensure_ascii=False))

lines = ['## 验收结论', '',
         '| 基线 | 首段／切换 | 金标通过 | T_total 中位数（秒） | T_net 中位数（秒） | 512-token Host KV 回读 |',
         '|---|---:|---:|---:|---:|---:|']
for name, info in summary.items():
    lines.append(f"| {name} | {info['initial_count']}／{info['switch_count']} | {info['golden_count']}/21 | "
                 f"{info['median_total_s']:.3f} | {info['median_net_s']:.3f} | {info['host_hit_512_count']}/18 |")
lines.extend(['', '两个基线的 S1、S2、S3 各验收 1 轮。各序列均完成双机 16 卡至少 60 秒共同归零。'
                  '本次比较覆盖指定模型切换位置，每个位置有一对实测值。当前预约剩余时间不足以让三个序列各补两轮。',
              '', '![B1/B2 三条序列的首段冷启和配对切换](b1-b2-switch-sequences.png)', ''])
body = report.read_text(encoding='utf-8')
assert body.count('## 三模型闭环') == 1 and '## 验收结论' not in body
body = body.replace('## 三模型闭环', '\n'.join(lines) + '\n## 三模型闭环', 1)
body += """
## Store 恢复证据与重算

[B1 Store 原生恢复索引](store-restore-evidence.json)覆盖三模型闭环和三条正式序列。正式序列的 18 个切换均包含 P、D 各八 rank 的原生恢复确认。V4 的字节量来自 planner 的张量覆盖范围，实际恢复用原生 GPU staging 的 UUID 核验。逐轮原始回执的路径和 SHA-256 见 [证据索引](evidence-index.json)。双机空卡、共享内存和服务退出的实时时点见 [Prism 交接回执](handoff-to-prism.json)。

在 qjhs003 对同一批原始证据重算时，使用本报告目录作为当前目录和新的输出目录。以下命令只读原始 GPU 实验回执。

```bash
export PYTHONPATH=/home/boyjiang/pd-v6-b1-closureplan50.P1Se3muW/runtime
export PATH="/data/brick2/boyjiang/pd-1p1d-baselines/tmp/v6-postboot-route.pMZpk2Ro/pair-ssh-bin:$PATH"
export PD_V6_SSH_SOCKET="/tmp/pd-v6-postboot-$(id -u)-51752"
mapfile -t run_ids < <(python3 -c 'import json; print("\\n".join(r["run_id"] for r in json.load(open("evidence-index.json"))["runs"]))')
out="$(mktemp -d)/final"
python3 -B v6_campaign.py --through B1 --out "$out" "${run_ids[@]}"
python3 -B verify_store_restore.py --out "$out/store-restore-evidence.json" "${run_ids[@]:6}"
python3 -B render_memory_peaks.py "$out/evidence-index.json" >> "$out/FINAL-1P1D-S1S2S3-SUMMARY.md"
python3 -B plot_final.py "$out" --check-only
python3 -B finalize_report.py "$out"
```

将三个重算数据文件复制到安装 Matplotlib 和中文字体的工作站后执行 `python -B plot_final.py <复制后的目录>`，生成报告所引用的 PNG。
"""
report.write_text(body, encoding='utf-8')
receipt = folder / 'formal-summary.json'
receipt.write_text(json.dumps({'schema': '1p1d-v6-formal-summary', 'baselines': summary,
                              'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                         for p in (folder / 'evidence-index.json', folder / 'segments.csv',
                                                   folder / 'store-restore-evidence.json')}},
                             ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
