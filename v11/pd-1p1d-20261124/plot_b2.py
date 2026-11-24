"""Plot the qualified B2 1P+1D sequences from the published evidence."""

import csv
import json
from math import isclose
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch, Rectangle
from matplotlib.ticker import MultipleLocator


ROOT = Path(__file__).resolve().parent
MODELS = {'K': 'Kimi-K2.6', 'G': 'GLM-5.3', 'D': 'DeepSeek-V4-Pro'}
SHORT = {'Kimi-K2.6': 'Kimi', 'GLM-5.3': 'GLM', 'DeepSeek-V4-Pro': 'V4'}
SEQUENCES = {'S1': 'KGDGKDK', 'S2': 'GDKDGKG', 'S3': 'DKGKDGD'}
INK, MUTED, GRID = '#152842', '#465F7A', '#D8E2ED'
COLD, WAKE, FIRST = '#547A9C', '#167A79', '#233F68'
STAGES = (
    ('P 权重准备', 'P_weights_s', 'weights', 1),
    ('D 权重准备', 'D_weights_s', 'weights', 1),
    ('P Checkpoint 重载', 'P_checkpoint_reload_s', 'reload', 1),
    ('D Checkpoint 重载', 'D_checkpoint_reload_s', 'reload', 1),
    ('P GPU KV 分配', 'P_gpu_kv_alloc_s', 'kv', 3),
    ('D GPU KV 分配', 'D_gpu_kv_alloc_s', 'kv', 3),
    ('可服务 → 正确响应', 'serve_to_correct_response_s', 'response', 2),
    ('Host KV 命中（token）', 'host_hit_tokens', 'tokens', 0),
)


def read_segments():
    with (ROOT / 'segments.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    audit = json.loads((ROOT / 'evidence-index.json').read_text(encoding='utf-8'))
    assert audit['through'] == 'B2' and audit['passed'] is True and audit['formal_switches'] == 18
    verified = {r['case'][1]: r for r in audit['runs'] if r['case'][2] is None}
    assert len(rows) == 21 and set(verified) == set(SEQUENCES)
    grouped = {}
    for seq, order in SEQUENCES.items():
        run = verified[seq]
        group = sorted((r for r in rows if r['sequence'] == seq), key=lambda r: int(r['segment']))
        assert len(group) == len(run['segments']) == 7
        for step, (r, raw, code) in enumerate(zip(group, run['segments'], order, strict=True)):
            assert r['baseline'] == 'B2' and r['run_id'] == run['run_id']
            assert int(r['segment']) == raw['step'] == step and r['target'] == MODELS[code]
            assert raw['model'] == code and raw['old_model'] == (order[step - 1] if step else None)
            assert r['previous'] == MODELS.get(raw['old_model'], '')
            assert raw['measurement'] == ('switch' if step else 'first_cold_start')
            assert r['action'] == raw['action'] == (
                'load_safetensors_cold' if step < 3 else 'wake_checkpoint_same_worker')
            assert r['evidence'] == raw['receipt']['path']
            for key in ('total_s', 'net_s', 'release_wait_s', 'serve_to_correct_response_s'):
                r[key] = float(r[key])
                assert r[key] == raw['timing'][key]
            assert isclose(r['total_s'] - r['net_s'], r['release_wait_s'], abs_tol=1e-8)
            r['host_hit_tokens'] = int(r['host_hit_tokens'])
            assert r['host_hit_tokens'] == raw['host_hit_tokens'] == (0 if step < 3 else 512)
            for _, key, band, _ in STAGES:
                if band in ('response', 'tokens'):
                    continue
                role, field = key.split('_', 1)
                r[key] = float(r[key]) if r[key] else None
                assert r[key] == raw['role_stage_timing'][role].get(field)
        grouped[seq] = group
    return grouped


def switch_figure(grouped):
    fig = plt.figure(figsize=(14.4, 7.1), facecolor='white')
    grid = fig.add_gridspec(2, 3, left=.07, right=.975, bottom=.18, top=.78,
                           hspace=.63, wspace=.22, height_ratios=(1, 3.2))
    fig.text(.07, .945, 'B2 1P+1D · 三模型冷启动与切换', fontsize=26, weight='bold', color=INK)
    fig.text(.07, .895, 'S1／S2／S3 各 1 轮 · 3 次独立首启 · 18 次正式切换', fontsize=12, color=MUTED)
    fig.text(.07, .83, '独立首段冷启（秒）', fontsize=12, weight='bold', color=INK)
    fig.legend(handles=[Patch(color=COLD, label='轮内首次 SafeTensors 冷载'),
                        Patch(color=WAKE, label='原 Worker Sleep L2 唤醒')],
               loc='upper right', bbox_to_anchor=(.978, .862), frameon=False, ncol=2, fontsize=10)
    for col, (seq, rows) in enumerate(grouped.items()):
        first = rows[0]
        start = fig.add_subplot(grid[0, col])
        start.text(0, .77, f'{seq} · {first["target"]}', transform=start.transAxes, color=INK, fontsize=11)
        start.barh(.19, first['total_s'], height=.20, color=FIRST)
        start.text(first['total_s'] + 25, .19, f'{first["total_s"]:,.1f}', va='center', color=INK, fontsize=10)
        start.set(xlim=(0, 1560), ylim=(0, 1))
        start.axis('off')

        ax = fig.add_subplot(grid[1, col])
        switches = rows[1:]
        y = [r['total_s'] for r in switches]
        ax.bar(range(1, 7), y, width=.64, color=[COLD] * 2 + [WAKE] * 4, zorder=3)
        for x, elapsed in enumerate(y, start=1):
            ax.text(x, elapsed + 29, f'{elapsed:,.1f}', ha='center', fontsize=9, color=INK)
        ax.set_title(f'{seq} · 六次正式切换', loc='left', fontsize=13, weight='bold', color=INK, pad=10)
        ax.set(xlim=(.48, 6.52), ylim=(0, 1450))
        ax.set_xticks(range(1, 7), [f'{r["segment"]}\n{SHORT[r["previous"]]}→{SHORT[r["target"]]}'
                                    for r in switches], fontsize=8)
        ax.yaxis.set_major_locator(MultipleLocator(400))
        ax.grid(axis='y', color=GRID, zorder=0)
        ax.tick_params(length=0, colors=MUTED)
        ax.spines[['top', 'right']].set_visible(False)
        ax.spines[['left', 'bottom']].set_color(GRID)
        if col == 0:
            ax.set_ylabel('发出请求 → 正确完整响应（秒）', color=MUTED)
    switches = [r for group in grouped.values() for r in group[1:]]
    fig.text(.07, .085, f'正式切换中位数  T_total {median(r["total_s"] for r in switches):.3f} 秒'
             f'   ·   T_net {median(r["net_s"] for r in switches):.3f} 秒（n=18）', fontsize=11, color=INK)
    fig.text(.07, .047, '柱高为 T_total。T_net 扣除实际阻塞后续启动的释放观察等待。', fontsize=10, color=MUTED)
    fig.savefig(ROOT / 'b2-switch-sequences.png', dpi=210, facecolor='white')
    plt.close(fig)


def stage_figure(grouped):
    fig, axes = plt.subplots(1, 3, figsize=(14.4, 5.3), facecolor='white')
    fig.subplots_adjust(left=.17, right=.975, top=.76, bottom=.24, wspace=.13)
    fig.text(.07, .92, 'B2 1P+1D · P／D 阶段耗时与 Host KV', fontsize=23, weight='bold', color=INK)
    fig.text(.07, .858, '每个序列 7 段 · 第 0 段为独立首启 · 第 1–2 段为轮内首次冷载 · 第 3–6 段为回访唤醒',
             fontsize=11, color=MUTED)
    maxima = {band: max(r[key] or 0 for group in grouped.values() for r in group
                        for _, key, stage_band, _ in STAGES if stage_band == band)
              for _, _, band, _ in STAGES}
    for col, (ax, (seq, rows)) in enumerate(zip(axes, grouped.items(), strict=True)):
        for y, (_, key, band, precision) in enumerate(STAGES):
            cmap = plt.get_cmap('GnBu' if band == 'tokens' else 'Blues')
            for x, row in enumerate(rows):
                value = row[key]
                shade = .13 + .73 * ((value or 0) / maxima[band])
                face = '#EDF1F5' if value is None else cmap(shade)
                ax.add_patch(Rectangle((x-.5, y-.5), 1, 1, facecolor=face, edgecolor='white', linewidth=1.4))
                ax.text(x, y, '—' if value is None else f'{value:,.{precision}f}',
                        ha='center', va='center', fontsize=8.8, color='white' if shade > .59 else INK)
        ax.axvline(.5, color=FIRST, linewidth=1.25)
        ax.axvline(2.5, color=WAKE, linewidth=1.35)
        ax.set_xlim(-.5, 6.5)
        ax.set_ylim(len(STAGES)-.5, -.5)
        ax.set_title(seq, loc='left', fontsize=14, color=INK, weight='bold', pad=9)
        ax.set_xticks(range(7), [f'{r["segment"]}\n{SHORT[r["target"]]}' for r in rows], fontsize=9)
        ax.set_yticks(range(len(STAGES)), [label if col == 0 else '' for label, *_ in STAGES], fontsize=10)
        ax.tick_params(length=0, colors=MUTED)
        ax.spines[:].set_visible(False)
    fig.text(.07, .115, 'P = qjhs003，D = qjhs002。除 Host KV 命中外，数值单位为秒。', fontsize=10, color=INK)
    fig.text(.07, .067, '同类指标共用色阶。阶段可能重叠。横线表示首次加载无 Checkpoint 重载。', fontsize=10, color=MUTED)
    fig.savefig(ROOT / 'b2-pd-stages.png', dpi=210, facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    fonts = ('Microsoft YaHei', 'Noto Sans CJK SC', 'PingFang SC', 'Source Han Sans SC')
    installed = {f.name for f in font_manager.fontManager.ttflist}
    font = next((name for name in fonts if name in installed), None)
    if font is None:
        raise SystemExit(f'Install a supported CJK font: {", ".join(fonts)}')
    plt.rcParams['font.family'] = font
    plt.rcParams['axes.unicode_minus'] = False
    segments = read_segments()
    switch_figure(segments)
    stage_figure(segments)
