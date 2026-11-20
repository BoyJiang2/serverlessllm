"""Plot activation to first correct response by qualified B1 segment."""

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt


COLORS = {
    'K': '#286BA4', 'kimi': '#286BA4', 'Kimi-K2.6': '#286BA4',
    'G': '#795397', 'glm53': '#795397', 'GLM-5.3': '#795397',
    'D': '#2B826D', 'v4': '#2B826D', 'DeepSeek-V4-Pro': '#2B826D',
}
NAMES = {'K': 'Kimi-K2.6', 'kimi': 'Kimi-K2.6', 'G': 'GLM-5.3', 'glm53': 'GLM-5.3',
         'D': 'DeepSeek-V4-Pro', 'v4': 'DeepSeek-V4-Pro'}


def plot(csv_path, output):
    runs = defaultdict(list)
    with csv_path.open(newline='', encoding='utf-8') as source:
        for row in csv.DictReader(source):
            runs[row['sequence'], row['run_id']].append(row)
    if not runs or any(len(rows) != 7 for rows in runs.values()):
        raise ValueError('The plot requires at least one complete qualified seven-segment run')
    count = len(runs)
    columns = min(3, count)
    rows_count = (count + columns - 1) // columns
    fig, axes = plt.subplots(rows_count, columns, figsize=(5.4 * columns, 4.2 * rows_count),
                             sharex=True, squeeze=False)
    ceiling = max(float(row['activate_to_first_s']) for rows in runs.values() for row in rows)
    if not math.isfinite(ceiling) or ceiling <= 0:
        raise ValueError('Invalid activation intervals')
    for ax in axes.flat[count:]:
        ax.set_visible(False)
    for ax, ((sequence, run), rows) in zip(axes.flat, sorted(runs.items())):
        rows.sort(key=lambda x: int(x['segment']))
        seconds = [float(row['activate_to_first_s']) for row in rows]
        if any(not math.isfinite(x) or x <= 0 for x in seconds):
            raise ValueError(f'Missing activation interval in {run}')
        models = [row['model'] for row in rows]
        labels = [f'{i + 1}  {NAMES.get(model, model)}' for i, model in enumerate(models)]
        bars = ax.barh(labels, seconds, color=[COLORS[model] for model in models], height=0.62)
        ax.bar_label(bars, fmt='%.1f', padding=5, fontsize=9)
        ax.set_xlim(0, ceiling * 1.16)
        ax.invert_yaxis()
        ax.set_title(f'{sequence} · {run.rsplit("-", 1)[-1]}', loc='left', pad=9)
        ax.set_xlabel('Activation to first correct complete response (s)')
        ax.tick_params(axis='x', labelbottom=True)
        ax.grid(axis='x', color='#DCE2E8', linewidth=0.6)
        ax.set_axisbelow(True)
        ax.spines[['top', 'right', 'left']].set_visible(False)
        ax.tick_params(axis='y', length=0)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=170, bbox_inches='tight', facecolor='white')
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plot(args.csv, args.output)
