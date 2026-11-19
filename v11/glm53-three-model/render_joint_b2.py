"""Render the formal GLM-5.3 three-model Sleep L2 measurements."""

import argparse
import json
from pathlib import Path
from statistics import median

from recompute_b1 import NAMES, SEQUENCES
from render_b1 import MODEL, relative, seconds, table


WEIGHT_PATH = {'safetensors_disk_cold': 'SafeTensors 冷读',
               'store_inplace_cumem': 'Store 原位恢复', 'checkpoint_wake': 'Checkpoint 唤醒'}
TIMES = ('weight_preparation_s', 'new_gpu_kv_s', 'old_gpu_kv_release_to_new_kv_ready_s',
         'activation_to_serve_s', 'activation_to_first_gold_s', 'activation_to_all_gold_s')
TIME_HEADERS = ('权重准备（s）', '新 GPU KV（s）', '旧 GPU KV 释放 → 新 KV 就绪（s）',
                '至可服务（s）', '首个正确完整响应（s）', '全套业务金标（s）')


def times(rows, field):
    values = [row[field] for row in rows if row[field] is not None]
    return f'{seconds(median(values))}（n={len(values)}）' if values else '—（n=0）'


def render(runs, *, evidence_links):
    runs = sorted((run for run in runs if run['qualified']), key=lambda run: run['sequence'])
    if not runs or len({run['sequence'] for run in runs}) != len(runs):
        raise ValueError('Expected one independently qualified run per included sequence')

    data = []
    for run in runs:
        segments = run['segments']
        if (run['status'] != 'qualified' or run['evidence_errors'] or
                ''.join(row['model'] for row in segments) != SEQUENCES[run['sequence']] or
                run['gpu_release']['seconds'] < 60 or any(row['gold'] is not True for row in segments)):
            raise ValueError(f"Invalid qualified run {run['run_id']}")
        data.extend((run['sequence'], row) for row in segments)
    first = [(sequence, row) for sequence, row in data if row['mode'] != 'checkpoint_wake']
    wake = [(sequence, row) for sequence, row in data if row['mode'] == 'checkpoint_wake']

    lines = ['# V11 联合 B2 三模型 Sleep L2 · GLM-5.3', '',
             f'**{len(runs)}/3 轮验收通过，共 {len(data)} 段。** '
             f'{len(first)} 次首次加载和 {len(wake)} 次原 Worker 唤醒均通过金标。'
             '回访实测从 Host KV 恢复 512 token，删除对应键后复查为零命中。'
             '模型间使用同一前缀做命中与未命中的配对查询。', '',
             'qjhs003 使用 8×H20、TP8、864 GiB 权重 Store 和 813 GiB Host KV 池。'
             'GLM-5.3 使用 vLLM 0.30.0，Kimi-K2.6 和 DeepSeek-V4-Pro 使用 vLLM 0.20.1。'
             '每轮先初始化 Host KV 池，再初始化权重 Store。', '',
             '## 轮次与释放', '']
    lines += table(('序列与 run ID', '模型顺序', 'Host KV 池（s）', 'Store 池（s）', '两池就绪（s）',
                    '金标', '八卡归零观察（s）'), [
        (f"`{run['run_id']}`", ' → '.join(MODEL[NAMES[key]] for key in SEQUENCES[run['sequence']]),
         *(seconds(run['pool_init'][field]) for field in ('kv_init_s', 'store_init_s', 'both_ready_s')),
         '7/7', seconds(run['gpu_release']['seconds'])) for run in runs])
    lines += ['', '## 首次加载', '']
    lines += table(('段', '目标模型', '路径', *TIME_HEADERS, 'Store 重读 / 命中（GiB）', '金标'), [
        (f"{sequence} · `{row['evidence']}`", MODEL[NAMES[row['model']]], WEIGHT_PATH[row['mode']],
         *(seconds(row[field]) for field in TIMES),
         f"{row['store']['file_read_bytes'] / 2**30:.3f} / {row['store']['hit_bytes'] / 2**30:.3f}"
         if row['mode'] == 'store_inplace_cumem' else '不适用', '通过') for sequence, row in first])
    lines += ['', '## 回访唤醒', '']
    lines += table(('段', '目标模型', *TIME_HEADERS, '其中 checkpoint 重载（s）',
                    'Host KV 恢复（token / MiB）', '金标'), [
        (f"{sequence} · `{row['evidence']}`", MODEL[NAMES[row['model']]],
         *(seconds(row[field]) for field in TIMES), seconds(row['checkpoint_reload_s']),
         f"{row['kv']['hit_tokens']} / {sum(row['kv']['rank_bytes'].values()) / 2**20:.3f}", '通过')
        for sequence, row in wake])
    lines += ['', '## 按模型和路径汇总', '',
              '各列报告中位数和有效样本数。SafeTensors 冷读、Store 首载和 checkpoint 唤醒分别统计。', '']
    groups = [(key, mode, [row for _, row in data if row['model'] == key and row['mode'] == mode])
              for key in NAMES for mode in WEIGHT_PATH]
    lines += table(('模型', '路径', '段数', *TIME_HEADERS), [
        (MODEL[NAMES[key]], WEIGHT_PATH[mode], str(len(rows)), *(times(rows, field) for field in TIMES))
        for key, mode, rows in groups if rows])
    lines += ['', '## 计时与证据', '',
              '- 首次加载的权重准备采用八 rank 最早开始至最晚成功返回。唤醒从权重映射开始计至 checkpoint 重载结束。'
              '回访的 Host KV 恢复在后续请求中实测，H2D 字节覆盖参与的八 rank。',
              '- 至可服务计到 LMCache 注册完成。后续段的业务响应窗口包含注册后的跨模型对照查询。'
              '业务金标仅统计业务请求。新 GPU KV 采用原生分配或唤醒端点。缺独立端点显示为 `—`。', '',
              ' · '.join(evidence_links), '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    args = parser.parse_args()
    runs = [json.loads(path.read_bytes()) for path in args.results]
    links = [f"[{run['run_id']}]({relative(path, args.output)})" for path, run in zip(args.results, runs, strict=True)]
    links += [f'[原始证据路径、大小和 SHA-256]({relative(args.index, args.output)})']
    args.output.write_text(render(runs, evidence_links=links), encoding='utf-8')


if __name__ == '__main__':
    main()
