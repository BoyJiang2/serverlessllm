"""Render the independent GLM-5.3, Kimi and V4 formal B1 report."""

import argparse
import json
import os
from pathlib import Path

from recompute_b1 import NAMES, SEQUENCES


MODEL = {'kimi': 'Kimi-K2.6', 'glm53': 'GLM-5.3', 'v4': 'DeepSeek-V4-Pro'}
SHORT = {'kimi': 'Kimi', 'glm53': 'GLM', 'v4': 'V4'}
CACHE = {'safetensors_disk_cold': 'SF 冷读', 'full_miss': 'Store 全未命中',
         'partial_hit': 'Store 局部命中', 'full_hit': 'Store 全命中'}
RAW = ('qjhs003:/home/boyjiang/sllm-v4-pro/cache-competition-20261109/'
       'glm53-four-condition-20261118/formal-b1-runs')


def table(header, rows):
    return ['| ' + ' | '.join(row) + ' |' for row in (header, ('---',) * len(header), *rows)]


def seconds(value):
    return '—' if value is None else f'{value:.3f}'


def relative(target, report):
    return Path(os.path.relpath(target, report.parent)).as_posix()


def render(data, *, figure, json_link, csv_link, index_link):
    runs = sorted((run for run in data['runs'] if run['status'] == 'qualified'),
                  key=lambda run: (run['sequence'], run['run_id']))
    if len(runs) != len({run['run_id'] for run in runs}):
        raise ValueError('Duplicate qualified run IDs')
    for run in runs:
        models = [NAMES[key] for key in SEQUENCES[run['sequence']]]
        if ([(step['segment'], step['model']) for step in run['steps']] != list(enumerate(models))
                or any(step['run_id'] != run['run_id'] for step in run['steps'])
                or run['release']['seconds'] < 60 or run['release']['samples'] < 2):
            raise ValueError(f"Invalid qualified sequence or release: {run['run_id']}")
    lines = ['# V11 B1 三模型复现 · GLM-5.3', '', '## 实验结论', '']
    if runs:
        completed = '、'.join(dict.fromkeys(run['sequence'] for run in runs))
        steps = [step for run in runs for step in run['steps']]
        token_counts = {step['host_committed_tokens'] for step in steps}
        tokens = str(min(token_counts)) if len(token_counts) == 1 else f'{min(token_counts)}–{max(token_counts)}'
        lines += [f'**{completed} 的 {len(runs)} 轮正式验收通过，共 {len(steps)}/{len(steps)} 段金标通过。** '
                  f'每段 Host KV 提交 {tokens} token，清理后占用归零，旧 KV 命中均为 0 token。'
                  f'每轮独立完成至少 60 秒八卡归零观察。']
    else:
        lines += ['当前证据中尚无完成七段金标、清理和八卡释放验收的正式轮次。']
    lines += ['', 'qjhs003 使用 8×H20、TP8、864 GiB 权重 Store 和 813 GiB 常驻 LMCache Host KV 池。'
              'GLM-5.3 使用 vLLM 0.30.0，Kimi-K2.6 和 DeepSeek-V4-Pro 使用 vLLM 0.20.1。三模型共用 LMCache 0.5.5。',
              '', '## 冻结序列', '']
    lines += table(('序列', '模型顺序', '正式通过轮数'), [
        (sequence, ' → '.join(MODEL[NAMES[key]] for key in keys), str(sum(run['sequence'] == sequence for run in runs)))
        for sequence, keys in SEQUENCES.items()])
    lines += ['', '每轮首段经 SafeTensors 冷页准入，后六段通过同一权重 Store 恢复。'
              '每段独立启动 Backend，完成业务及 KV 探针后退出并清理 Host KV。', '']

    if runs:
        lines += ['## 数据 1 · 轮前初始化和整轮释放', '']
        lines += table(('序列与 run ID', 'Host KV 池初始化（s）', '权重 Store 初始化（s）', '两池就绪（s）',
                        '八卡归零观察（s）', '归零采样数', '金标'), [
            (f"{run['sequence']} · `{run['run_id']}`", seconds(run['pool_init']['kv_init_s']),
             seconds(run['pool_init']['store_init_s']), seconds(run['pool_init']['both_ready_s']),
             seconds(run['release']['seconds']), str(run['release']['samples']), '7/7') for run in runs])
        lines += ['', '先建 Host KV 池，再建权重 Store。两池就绪从首个池初始化发起计至第二个池就绪，含中间间隔。'
                  '释放观察从末尾连续八卡归零的首个采样计至末个采样。', '',
                  '## 数据 2 · 逐段启动、权重和 Host KV', '',
                  f'![正式 B1 七段激活至首个正确完整响应]({figure})', '']
        for run in runs:
            lines += [f"### {run['sequence']} · `{run['run_id']}`", '']
            lines += table(('原始段目录', '切换目标', '权重状态', 'A（s）', '完整权重加载（s）',
                            'Store 重读 / 命中（GiB）', 'Host KV 清理前 → 后（MiB）', '金标'), [
                (f"`{step['evidence']}`",
                 f"{SHORT[step['source_model']]} → {SHORT[step['model']]}" if step['source_model'] else SHORT[step['model']],
                 CACHE[step['cache_state']], seconds(step['activate_to_first_s']), seconds(step['full_weight_load_s']),
                 f"{step['file_read_bytes'] / 2**30:.3f} / {step['hit_bytes'] / 2**30:.3f}",
                 f"{step['host_used_bytes_before_clear'] / 2**20:.3f} → {step['host_used_bytes_after_clear'] / 2**20:.3f}",
                 '通过') for step in run['steps']])
            lines += ['']
        lines += [f'[逐段 CSV]({csv_link}) · [带原始端点和逐 rank 值的复算 JSON]({json_link})', '']

    lines += ['## 计时与缓存口径', '',
              '- **A** 取控制器放行激活至首个通过金标的完整业务响应。请求和 tokenizer 按各模型冻结，`kv_fill` 在业务响应之后单独记录。',
              '- **完整权重加载** 取八 rank 中最早进入加载调用到最晚成功返回的主机端跨度。V4 分组恢复及 H2D 可发生在后处理内。缺测记 `—`。',
              '- **SF 冷读** 按冻结 SafeTensors 权重的客户端页缓存零驻留判定。**Store 全未命中** 按目标 Store 路径在权重池内全部缺失判定，'
              '表中重读量来自 Store 路径回执，磁盘页缓存状态以另有准入证据为准。',
              '- **Host KV** 取 LMCache 状态回执中清理前后的池内有效占用。整轮 GPU 释放由八卡采样独立核验。', '',
              '## 原始证据', '', f'原始目录为 `{RAW}/<run_id>/`。', '',
              '- 轮次使用 `protocol.json`、`admission.json`、`live-admission.json`、`pool-init-timing.json`、`result.json` 和 `release.jsonl`。',
              '- 逐段使用 `step-result.json`、业务响应与金标回执、`events/*.jsonl` 或 `weight-load-rank-*.jsonl`、`kv-write.json`、'
              '`kv-clear.json` 和 `lookup-{before,after}.prom`。首段另有 `sf-admission/summary.json` 和原始 mincore 向量。',
              '', f'[失败与未结束记录索引]({json_link}) · [原始证据路径、大小和 SHA-256]({index_link})', '',
              '[联合 B2 容量与 CUDA 注册观察](B2-admission-20261119.md)', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('summary', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--figure', default='figures/b1-activation.png', help='PNG path relative to the report')
    args = parser.parse_args()
    doc = json.loads(args.summary.read_bytes())
    markdown = render(doc, figure=args.figure, json_link=relative(args.summary, args.report),
                      csv_link=relative(args.summary.with_name('b1-segments.csv'), args.report),
                      index_link=relative(args.summary.with_name('evidence-index.json'), args.report))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(markdown, encoding='utf-8')


if __name__ == '__main__':
    main()
