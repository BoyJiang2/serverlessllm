"""Draw the formal B1/B2 1P+1D comparison from the final audited 42 rows."""

import argparse
import csv
import json
from math import isclose, isfinite
from pathlib import Path
import re


SEQUENCES = {'S1': 'KGDGKDK', 'S2': 'GDKDGKG', 'S3': 'DKGKDGD'}
MODELS = {'K': 'Kimi-K2.6', 'G': 'GLM-5.3', 'D': 'DeepSeek-V4-Pro'}
SHORT = {'K': 'Kimi', 'G': 'GLM', 'D': 'V4'}
BASELINES = {'B1': ('SLLM Store', '#167A79'), 'B2': ('vLLM Sleep L2', '#547A9C')}
FROZEN = {
    ('B2', 'S1'): ('PD-V6-B2-S1-20261123T193346Z', '8a11204784296ee10dcbf78cbd4edc91dd18e539481b0c85bfe46b76b1fd7a36'),
    ('B2', 'S2'): ('PD-V6-B2-S2-20261124T012404Z', '14216e82b1b281ddb985a38d108b2226081ee06d0a1496a53f0e32db931111c8'),
    ('B2', 'S3'): ('PD-V6-B2-S3-20261124T030726Z', 'e934c43e5b35143ccd99c5e3e8c1dc9d4682483cbb317f4693265703be1f5b8c'),
    ('B1', 'S1'): ('PD-V6-B1-S1-20261124T094436Z', '86ee8d00ce93f0d2c4734dfbf2b4dbfa0f74887164f64177704dd31a2ec42480'),
    ('B1', 'S2'): ('PD-V6-B1-S2-20261124T110733Z', 'a8195696006bf5a7998fc4ca359d437f4ef518b7e7a185b43142786eb876bf73'),
    ('B1', 'S3'): ('PD-V6-B1-S3-20261124T122400Z', 'f800f5ee4808f6249052282e6077d37122da58767d13bd8ee5b492234ae68ced'),
}
INK, MUTED, GRID = '#152842', '#465F7A', '#D8E2ED'


def require(valid, message):
    if not valid:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def read_formal(root):
    audit = read_json(root / 'evidence-index.json')
    require(audit['through'] == 'B1' and audit['protocol'] == 'v6' and audit['passed'] is True
            and audit['formal_switches'] == 36, 'Use the accepted combined B1/B2 evidence index.')
    cases = [(base, seq, model) for base, models in (('B2', 'KDG'), ('B1', 'KGD'))
             for seq, model in [('S1', model) for model in models] + [(seq, None) for seq in SEQUENCES]]
    require([tuple(run['case']) for run in audit['runs']] == cases
            and len({run['run_id'] for run in audit['runs']}) == len(cases), 'Unexpected audit run set.')
    formal = {tuple(run['case'][:2]): run for run in audit['runs'] if run['case'][2] is None}
    for case, run in formal.items():
        base, sequence = case
        identity = run['run_id'], run['receipt']['sha256']
        require(re.fullmatch(rf'PD-V6-{base}-{sequence}-\d{{8}}T\d{{6}}Z', identity[0])
                and re.fullmatch(r'[0-9a-f]{64}', identity[1]) and run['common_zero_seconds'] >= 60
                and identity == FROZEN.get(case, identity), f'Invalid formal receipt for {case}.')

    with (root / 'segments.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    keys = [(row['baseline'], row['sequence'], int(row['segment'])) for row in rows]
    require(len(keys) == 42 and set(keys) == {(*case, step) for case in formal for step in range(7)},
            'Expected exactly 42 unique segments, including six independent cold starts.')
    series = {case: [None] * 7 for case in formal}
    for (base, sequence, step), row in zip(keys, rows, strict=True):
        run, order = formal[base, sequence], SEQUENCES[sequence]
        require(len(run['segments']) == 7, f'Incomplete audited sequence: {base}/{sequence}.')
        raw = run['segments'][step]
        model, old = order[step], order[step - 1] if step else None
        action = ('start_store_new_worker' if base == 'B1' and step else
                  'wake_checkpoint_same_worker' if base == 'B2' and step >= 3 else 'load_safetensors_cold')
        timing = raw['timing']
        total, net, wait = (float(row[key]) for key in ('total_s', 'net_s', 'release_wait_s'))
        require(row['run_id'] == run['run_id'] and raw['step'] == step
                and (raw['model'], raw['old_model']) == (model, old)
                and (row['target'], row['previous']) == (MODELS[model], MODELS.get(old, ''))
                and row['action'] == raw['action'] == action
                and row['evidence'] == raw['receipt']['path']
                and raw['measurement'] == ('first_cold_start' if not step else 'switch')
                and (total, net, wait) == (timing['total_s'], timing['net_s'], timing['release_wait_s'])
                and all(isfinite(x) for x in (total, net, wait)) and total >= net > 0 and wait >= 0
                and isclose(total - net, wait, rel_tol=0.0, abs_tol=1e-8),
                f'CSV and accepted audit disagree for {base}/{sequence}/{step}.')
        series[base, sequence][step] = total

    store = read_json(root / 'store-restore-evidence.json')
    require(store['schema'] == 'v6-b1-store-restore-evidence-v1', 'Expected the B1 Store verification index.')
    b1 = [run for (base, _), run in formal.items() if base == 'B1']
    expected = {(run['run_id'], raw['step'], raw['model'], role)
                for run in b1 for raw in run['segments'][1:] for role in ('P', 'D')}
    formal_b1_ids = {run['run_id'] for run in b1}
    selected = [row for row in store['starts'] if row['run_id'] in formal_b1_ids]
    require(len(selected) == len(expected)
            and {(r['run_id'], r['step'], r['model'], r['role']) for r in selected} == expected
            and all(sorted(rank['rank'] for rank in r['ranks']) == list(range(8))
                    and all(rank['native_confirmed_batches'] > 0 for rank in r['ranks']) for r in selected)
            and all(run['receipt'] in [{k: ref[k] for k in ('path', 'sha256')}
                                      for ref in store['accepted_run_sources'][run['run_id']]] for run in b1),
            'Store proof does not cover all 18 B1 switches on both roles.')
    return series


def plot(series, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    fonts = ('Microsoft YaHei', 'Noto Sans CJK SC', 'PingFang SC', 'Source Han Sans SC')
    installed = {font.name for font in font_manager.fontManager.ttflist}
    font = next((name for name in fonts if name in installed), None)
    require(font, 'Install a supported Chinese font: ' + ', '.join(fonts))
    plt.rcParams.update({'font.family': font, 'axes.unicode_minus': False})
    fig = plt.figure(figsize=(15, 7.1), facecolor='white')
    grid = fig.add_gridspec(2, 3, left=.075, right=.975, bottom=.19, top=.72,
                           height_ratios=(1, 3.2), hspace=.65, wspace=.22)
    fig.text(.075, .945, 'B1 / B2 1P+1D · 三模型冷启动与切换', color=INK, fontsize=26, weight='bold')
    fig.text(.075, .895, 'S1、S2、S3 每个基线各 1 轮 · 各有 3 次独立首启、18 次正式切换',
             color=MUTED, fontsize=12)
    fig.text(.075, .79, '独立首段 SafeTensors 冷启（秒）', color=INK, fontsize=12, weight='bold')
    fig.legend(handles=[Patch(color=color, label=f'{key} · {label}')
                        for key, (label, color) in BASELINES.items()], loc='upper right',
               bbox_to_anchor=(.978, .842), frameon=False, ncol=2, fontsize=11)
    cold_max = max(values[0] for values in series.values())
    switch_max = max(max(values[1:]) for values in series.values())
    for column, (sequence, order) in enumerate(SEQUENCES.items()):
        cold = fig.add_subplot(grid[0, column])
        cold.set_title(f'{sequence} · {MODELS[order[0]]}', loc='left', color=INK, fontsize=12)
        ax = fig.add_subplot(grid[1, column])
        for index, (base, (_, color)) in enumerate(BASELINES.items()):
            elapsed = series[base, sequence]
            cold.barh(1 - index, elapsed[0], height=.47, color=color)
            cold.text(elapsed[0] + cold_max * .025, 1 - index, f'{elapsed[0]:,.1f}',
                      color=INK, va='center', fontsize=9)
            bars = ax.bar([step + (index - .5) * .34 for step in range(1, 7)], elapsed[1:],
                          width=.32, color=color, zorder=3)
            ax.bar_label(bars, fmt='%.0f', padding=3, fontsize=8, color=INK)
        cold.set(xlim=(0, cold_max * 1.19), ylim=(-.42, 1.42))
        cold.axis('off')
        ax.set_title(f'{sequence} · 六次配对切换', loc='left', color=INK, fontsize=12, weight='bold', pad=9)
        ax.set(xlim=(.48, 6.52), ylim=(0, switch_max * 1.16))
        ax.set_xticks(range(1, 7), [f'{i}\n{SHORT[order[i-1]]}→{SHORT[order[i]]}' for i in range(1, 7)], fontsize=7.5)
        ax.yaxis.set_major_locator(MaxNLocator(nbins=5))
        ax.grid(axis='y', color=GRID)
        ax.tick_params(length=0, colors=MUTED)
        for edge in ('top', 'right'):
            ax.spines[edge].set_visible(False)
        for edge in ('left', 'bottom'):
            ax.spines[edge].set_color(GRID)
        if column == 0:
            ax.set_ylabel('切换发出 → 首个正确完整响应（秒）', color=MUTED, fontsize=11)
    fig.text(.075, .09, '切换图使用实际端到端 T_total。横轴数字是切换编号，箭头两侧标出旧模型和目标模型。',
             color=MUTED, fontsize=10)
    fig.text(.075, .052, 'B1 的六次目标启动均用 Store。B2 前两次切换为冷载，后四次为原 Worker Sleep L2 唤醒。',
             color=MUTED, fontsize=10)
    fig.savefig(output, dpi=210, facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path, nargs='?', default=Path(__file__).resolve().parent)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    data = read_formal(args.directory)
    if not args.check_only:
        plot(data, args.directory / 'b1-b2-switch-sequences.png')
    print('Validated 42 rows: 6 cold starts and 36 switches across 18 matched positions.')
