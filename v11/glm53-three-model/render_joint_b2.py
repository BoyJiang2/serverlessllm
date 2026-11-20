"""Render the formal GLM-5.3 three-model Sleep L2 measurements."""

import argparse
import json
from pathlib import Path
from statistics import median

from recompute_b1 import GIB, NAMES, SEQUENCES
from recompute_joint_b2 import POOL_CONFIGS
from render_b1 import MODEL, relative, seconds, table


WEIGHT_PATH = {'safetensors_disk_cold': 'SafeTensors 冷读',
               'store_inplace_cumem': 'Store 原位恢复', 'checkpoint_wake': 'Checkpoint 唤醒'}
TIMES = ('weight_preparation_s', 'new_gpu_kv_s', 'old_gpu_kv_release_to_new_kv_ready_s',
         'activation_to_serve_s', 'activation_to_first_gold_s', 'activation_to_all_gold_s')
TIME_HEADERS = ('权重准备（s）', '新 GPU KV（s）', '旧 GPU KV 释放 → 新 KV 就绪（s）',
                '至可服务（s）', '首个正确完整响应（s）', '全套业务金标（s）')
PIN_STATUS = {'success_confirmed': '整池注册成功', 'failure_observed': '观察到注册失败',
              'disabled_reported': '日志报告关闭',
              'unverified': '锁页未核实'}


def times(rows, field):
    values = [row[field] for row in rows if row[field] is not None]
    return f'{seconds(median(values))}（n={len(values)}）' if values else '—（n=0）'


def pool_observations(runs):
    rows = []
    for run in runs:
        memory = run.get('host_monitor') or {}
        pin = run.get('host_pin') or {}
        peak = run.get('host_kv_peak_bytes')
        if peak is None and not memory and not pin:
            continue
        host = [memory.get(field) for field in (
            'min_available_bytes', 'lmcache_anon_at_min_bytes', 'lmcache_anon_peak_bytes')]
        rows.append((f"`{run['run_id']}`", seconds(peak / 2**20 if peak is not None else None),
                     *(seconds(value / GIB if value is not None else None) for value in host),
                     PIN_STATUS.get(pin.get('state'), '—')))
    if not rows:
        return []
    return ['', '## Host KV 占用与锁页观测', '',
            *table(('run ID', '有效 KV 观测峰值（MiB）', '主机最低可用（GiB）',
                    '该点 LMCache 匿名内存（GiB）', 'LMCache 匿名内存峰值（GiB）', '锁页日志'), rows),
            '', '有效 KV 从各探针前后快照取最大值。主机和 cgroup 指标取池就绪后的实际采样。'
            '后端选择日志仅记录使用的注册接口，锁页状态以可核实的日志为准。']


def kimi_comparison(wake, history, history_link, *, has_store=True):
    positions = {(run['sequence'], row['index']): {} for run, row in wake if row['model'] == 'K'}
    cohorts = ('历史原生未修', '历史 WNA16 修复', '本轮联合 B2 · WNA16 + meta')
    for item in history['kimi']:
        position = item['sequence'], item['step']
        if position not in positions:
            continue
        historical = positions[position]
        for run_id, row in item['baseline'].items():
            previous = historical.setdefault((cohorts[0], run_id), row)
            if previous != row:
                raise ValueError(f'Conflicting historical evidence for {position} in {run_id}')
        historical[cohorts[1], item['run']] = item['candidate']

    pool_phrase = '常驻双池' if has_store else '常驻 Host KV 池（无权重 Store）'
    lines = ['', '## Kimi 同序列同唤醒位置对照', '',
             f'历史原生 Sleep 使用 GLM-5.1-FP8。联合 B2 使用 GLM-5.3、{pool_phrase}及 Host KV 回读。'
             '历史与本轮的同位置数据用于描述实际运行差异。'
             '单模型同配置补丁对照另见[准入与优化证据](B2-admission-20261119.md)。', '']
    details = []
    summaries = []
    fields = ('reload', 'last_expert', 'anon', 'refault')
    for run, row in wake:
        if row['model'] != 'K':
            continue
        position = run['sequence'], row['index']
        label = f'{position[0]}/{position[1]:02d}'
        memory = row.get('kimi_reload_memory') or {}
        expert = row.get('kimi_experts') or {}
        metrics = {cohort: [] for cohort in cohorts}
        for (cohort, run_id), sample in positions[position].items():
            mem = sample['memory']
            layer = sample['layer_completion'] or {}
            offsets = mem['endpoint_offsets_ns']
            metrics[cohort].append(dict(run_id=run_id, reload=sample['checkpoint_reload_s'],
                                        last_expert=layer.get('last_completion_s'),
                                        anon=mem['anon_peak_bytes'] / GIB if mem['anon_peak_bytes'] is not None else None,
                                        refault=mem['refault_delta_pages'],
                                        memory_complete=bool(offsets) and max(map(abs, offsets)) <= 5 * 10**9))
        metrics[cohorts[2]].append(dict(run_id=run['run_id'], reload=row['checkpoint_reload_s'],
                                        last_expert=expert.get('last_expert_s'),
                                        anon=memory['anon_peak_bytes'] / GIB if memory else None,
                                        refault=memory.get('refault_pages_delta'),
                                        memory_complete=memory.get('covers_window_to_3s', False)))
        for cohort, samples in metrics.items():
            for sample in samples:
                cells = [seconds(sample[field]) for field in fields]
                if not sample['memory_complete']:
                    cells[-2:] = [cell + '（部分覆盖）' if cell != '—' else cell for cell in cells[-2:]]
                details.append((label, cohort, f"`{sample['run_id']}`", *cells))
            memory_samples = [sample for sample in samples if sample['memory_complete']]
            summaries.append((label, cohort, *(times(samples, field) for field in fields[:2]),
                              *(times(memory_samples, field) for field in fields[2:])))
    headers = ('checkpoint 重载（s）', '重载开始至最后专家层（s）', '匿名内存采样峰值（GiB）', '文件页 refault（页）')
    lines += table(('位置', '配置', 'run ID', *headers), details)
    lines += ['', '各位置、各配置按有效 n 报中位数。专家层时间覆盖 60 层 × 8 rank。'
              '匿名内存为 checkpoint 窗口内 Worker cgroup 采样峰值。refault 为窗口内首末采样点的计数增量。'
              '内存中位数要求历史首末采样距端点各不超过 5 秒，本轮另要求首末偏差和最大间隔均不超过 3 秒。'
              f'[历史逐次对照证据]({history_link})', '']
    return lines + table(('位置', '配置', *headers), summaries)


def render(runs, *, evidence_links, history, history_link, figure=None):
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

    configs = {(run.get('weight_store_gib', 864 if run['pool_init']['store_init_s'] is not None else 0),
                run.get('host_kv_pool_gib', 813)) for run in runs}
    if len(configs) != 1 or not configs.issubset(POOL_CONFIGS):
        raise ValueError('Expected one supported pool configuration per report')
    store_gib, host_kv_gib = configs.pop()
    has_store = store_gib > 0
    if host_kv_gib == 384:
        for run in runs:
            if (run.get('final_host_empty_ns') is None or (run.get('kimi_worker_imports') or {}).get('ranks') != 8
                    or any((row.get('kimi_experts') or {}).get('layer_rank_completions') != 480
                           for row in run['segments']
                           if row['model'] == 'K' and row['mode'] == 'checkpoint_wake')):
                raise ValueError(f"Missing Host KV or Kimi evidence for {run['run_id']}")
    pool_desc = (f'qjhs003 使用 8×H20、TP8、{store_gib} GiB 权重 Store 和 {host_kv_gib} GiB Host KV 池。'
                 'GLM-5.3 使用 vLLM 0.30.0，Kimi-K2.6 和 DeepSeek-V4-Pro 使用 vLLM 0.20.1。'
                 '每轮先初始化 Host KV 池，再初始化权重 Store。'
                 if has_store else
                 f'qjhs003 使用 8×H20、TP8 与 {host_kv_gib} GiB Host KV 池（不启用权重 Store）。'
                 'GLM-5.3 使用 vLLM 0.30.0，Kimi-K2.6 和 DeepSeek-V4-Pro 使用 vLLM 0.20.1。'
                 '每轮仅初始化 Host KV 池。')

    lines = ['# V11 联合 B2 三模型 Sleep L2 · GLM-5.3', '',
             f'**{len(runs)}/3 轮验收通过，共 {len(data)} 段。** '
             f'{len(first)} 次首次加载和 {len(wake)} 次原 Worker 唤醒均通过金标。'
             '回访实测从 Host KV 恢复 512 token，删除对应键后复查为零命中。'
             '模型间使用同一前缀做命中与未命中的配对查询。', '',
             pool_desc, '',
             '## 轮次与释放', '']
    if has_store:
        lines += table(('序列与 run ID', '模型顺序', 'Host KV 池（s）', 'Store 池（s）', '两池就绪（s）',
                        '金标', '八卡归零观察（s）'), [
            (f"`{run['run_id']}`", ' → '.join(MODEL[NAMES[key]] for key in SEQUENCES[run['sequence']]),
             *(seconds(run['pool_init'][field]) for field in ('kv_init_s', 'store_init_s', 'both_ready_s')),
             '7/7', seconds(run['gpu_release']['seconds'])) for run in runs])
    else:
        lines += table(('序列与 run ID', '模型顺序', 'Host KV 池（s）', '金标', '八卡归零观察（s）'), [
            (f"`{run['run_id']}`", ' → '.join(MODEL[NAMES[key]] for key in SEQUENCES[run['sequence']]),
             seconds(run['pool_init']['kv_init_s']),
             '7/7', seconds(run['gpu_release']['seconds'])) for run in runs])
    lines += pool_observations(runs)
    if figure:
        lines += ['', f'![首次加载和回访唤醒的权重准备、激活至可服务]({figure})']
    lines += ['', '## 首次加载', '']
    if has_store:
        lines += table(('段', '目标模型', '路径', *TIME_HEADERS, 'Store 重读 / 命中（GiB）', '金标'), [
            (f"{sequence} · `{row['evidence']}`", MODEL[NAMES[row['model']]], WEIGHT_PATH[row['mode']],
             *(seconds(row[field]) for field in TIMES),
             f"{row['store']['file_read_bytes'] / 2**30:.3f} / {row['store']['hit_bytes'] / 2**30:.3f}"
             if row['mode'] == 'store_inplace_cumem' else '不适用', '通过') for sequence, row in first])
    else:
        lines += table(('段', '目标模型', '路径', *TIME_HEADERS, '金标'), [
            (f"{sequence} · `{row['evidence']}`", MODEL[NAMES[row['model']]], WEIGHT_PATH[row['mode']],
             *(seconds(row[field]) for field in TIMES), '通过') for sequence, row in first])
    lines += ['', '## 回访唤醒', '']
    lines += table(('段', '目标模型', *TIME_HEADERS, '其中 checkpoint 重载（s）',
                    'Host KV 恢复（token / MiB）', '金标'), [
        (f"{sequence} · `{row['evidence']}`", MODEL[NAMES[row['model']]],
         *(seconds(row[field]) for field in TIMES), seconds(row['checkpoint_reload_s']),
         f"{row['kv']['hit_tokens']} / {sum(row['kv']['rank_bytes'].values()) / 2**20:.3f}", '通过')
        for sequence, row in wake])
    summary_note = ('各列报告中位数和有效样本数。SafeTensors 冷读、Store 首载和 checkpoint 唤醒分别统计。'
                    if has_store else
                    '各列报告中位数和有效样本数。SafeTensors 冷读和 checkpoint 唤醒分别统计。')
    lines += ['', '## 按模型和路径汇总', '', summary_note, '']
    groups = [(key, mode, [row for _, row in data if row['model'] == key and row['mode'] == mode])
              for key in NAMES for mode in WEIGHT_PATH]
    lines += table(('模型', '路径', '段数', *TIME_HEADERS), [
        (MODEL[NAMES[key]], WEIGHT_PATH[mode], str(len(rows)), *(times(rows, field) for field in TIMES))
        for key, mode, rows in groups if rows])
    lines += kimi_comparison([(run, row) for run in runs for row in run['segments']
                             if row['mode'] == 'checkpoint_wake'], history, history_link, has_store=has_store)
    lines += ['', '## 计时与证据', '',
              '- 首次加载的权重准备采用八 rank 最早开始至最晚成功返回。唤醒从权重映射开始计至 checkpoint 重载结束。'
              '回访的 Host KV 恢复在后续请求中实测，H2D 字节覆盖参与的八 rank。',
              '- GLM-5.3 的 Host KV 回读分别核对每个 rank 的 MLA 78 层与 indexer 21 层。'
              '每段 `wake-transfers.json` 保留分组、block ID、传输方向及完成回执，文件名带段号。',
              '- GLM-5.3 首次加载缺八 rank GPU KV 分配的独立单调时钟端点。'
              '休眠回执记录整体 Sleep 区间，缺各 rank 开始卸载 `kv_cache` 的时点。'
              '这两项保留缺测。GLM-5.3 回访使用实际 KV 唤醒开始至就绪端点。',
              '- 至可服务计到 LMCache 注册完成。后续段的业务响应窗口包含注册后的跨模型对照查询。'
              '业务金标仅统计业务请求。新 GPU KV 采用原生分配或唤醒端点。缺独立端点显示为 `—`。', '',
              ' · '.join(evidence_links), '']
    return '\n'.join(lines)


def plot(runs, output):
    from plot_b1 import plt

    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5), sharex=True)
    for axis, wake, title in zip(axes, (False, True), ('First loads', 'Same-worker checkpoint wakes'), strict=True):
        rows = [(run['sequence'], row) for run in sorted(runs, key=lambda run: run['sequence']) if run['qualified']
                for row in run['segments'] if (row['mode'] == 'checkpoint_wake') == wake]
        labels = [f"{sequence}/{row['index']:02d}  {MODEL[NAMES[row['model']]]}" for sequence, row in rows]
        y = range(len(rows))
        for field, offset, color, label in (
                ('weight_preparation_s', -0.18, '#2B826D', 'Weight preparation'),
                ('activation_to_serve_s', 0.18, '#286BA4', 'Activation to service ready')):
            measured = [(i + offset, row[field]) for i, (_, row) in enumerate(rows) if row[field] is not None]
            if measured:
                bars = axis.barh(*zip(*measured, strict=True), height=0.30, color=color, label=label)
                axis.bar_label(bars, fmt='%.1f', padding=4, fontsize=8)
        axis.set_yticks(y, labels)
        axis.invert_yaxis()
        axis.set_title(title, loc='left')
        axis.set_xlabel('Seconds')
        axis.grid(axis='x', color='#DCE2E8', linewidth=0.6)
        axis.set_axisbelow(True)
        axis.spines[['top', 'right', 'left']].set_visible(False)
        axis.tick_params(axis='y', length=0)
    fig.legend(*axes[0].get_legend_handles_labels(), loc='upper center', ncols=2, frameon=False)
    fig.tight_layout(rect=(0, 0, 1, 0.94), w_pad=3)
    axes[0].set_xlim(0, axes[0].get_xlim()[1] * 1.12)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--figure', type=Path)
    parser.add_argument('--kimi-history', type=Path, default=Path(__file__).resolve().parents[1] /
                        'large-pool-20261114' / 'sleep-wna16-sequences' / 'audit.json')
    args = parser.parse_args()
    runs = [json.loads(path.read_bytes()) for path in args.results]
    links = [f"[{run['run_id']}]({relative(path, args.output)})" for path, run in zip(args.results, runs, strict=True)]
    links += [f'[原始证据路径、大小和 SHA-256]({relative(args.index, args.output)})']
    history = json.loads(args.kimi_history.read_bytes())
    figure = relative(args.figure, args.output) if args.figure else None
    report = render(runs, evidence_links=links, history=history, history_link=relative(args.kimi_history, args.output),
                    figure=figure)
    if args.figure:
        plot(runs, args.figure)
    args.output.write_text(report, encoding='utf-8')


if __name__ == '__main__':
    main()
