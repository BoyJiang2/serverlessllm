"""CPU-only comparison of SHA-bound existing evidence; never imports model code."""
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

sys.dont_write_bytecode = True
BASE = Path(__file__).resolve().parent
CONDITIONS = ['safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit']
LABELS = ['SF cold', 'SF hot', 'Store cold', 'Store pool']
MODELS = ['Kimi-K2.6', 'GLM-5.1-FP8', 'DeepSeek-V4-Pro']
COLORS = ['#3267A5', '#7EAED0', '#C57935', '#4D9568']


def read(path):
    return json.loads(path.read_bytes())


def mean(values):
    return statistics.fmean(values)


def build():
    manifest = read(BASE / 'INPUTS.json')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((BASE / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Changed input: ' + name)
    legacy = BASE / 'inputs/legacy'
    v4 = BASE / 'inputs/v4'
    sys.path.insert(0, str(v4 / 'analysis-v2'))
    from v4_campaign_statistics import campaign_statistics, join_validated_reviews
    protocol = read(v4 / 'protocol.json')
    policy = read(v4 / 'amendment.json')
    raw = [read(v4 / 'results/raw-replay' / a['attempt_id'] / 'result.json') for a in protocol['attempts']]
    reviews = {r['attempt_id']: read(v4 / 'original-reviews' / r['attempt_id'] / 'runtime-review.json')
               for r in raw if r['attempt_id'] not in policy['acknowledged_failures']}
    rows = join_validated_reviews(raw, protocol, reviews, policy['acknowledged_failures'],
                                 'bffa18dfe81a2c18ab31b8df51246c587c2e7abe58aea214057775a46e7a24be')
    assert rows == read(v4 / 'results/qualified-rows.json')
    formal = campaign_statistics(rows, protocol)
    assert formal == read(v4 / 'results/statistics.json')
    assert len(rows) == 40 and sum(r['success'] for r in rows) == 38
    kimi = read(legacy / 'kimi-four-condition-actual-analysis-v1-20261107.json')
    glm = read(legacy / 'kimi-glm-existing-plot-input-20261107.json')
    old_summary = read(legacy / 'comparison-summary.json')
    stages = read(legacy / 'kimi-glm-mutually-exclusive-stage-input-20261107.json')
    primary = {m: {} for m in MODELS}
    for model in MODELS[:2]:
        for c in CONDITIONS:
            group = stages['models'][model][c]
            segments = group['segments_s']
            values = [sum(x) for x in zip(*(segments[k] for k in stages['segment_order'][2:]))]
            assert all(math.isclose(x, y, abs_tol=1e-8) for x, y in zip(values, old_summary['models'][model][c]['A_values_s']))
            primary[model][c] = dict(blocks=group['blocks'], ids=group['run_ids'], A_s=values,
                prep_s=segments['preparation_s'], gap_s=segments['preparation_to_startup_gap_s'],
                startup_segments_s={k: segments[k] for k in stages['segment_order'][2:]},
                controller_mean_s=old_summary['models'][model][c]['controller_elapsed_mean_s'])
    for c in CONDITIONS:
        group = [r for r in rows if r['success'] and r['block'] in range(2, 11) and r['condition'] == c]
        assert len(group) == 9 and {r['code_commit'] for r in group} == {'88cc71ec50c8668c26f26636d7d432c3351356fe'}
        metrics = [r['metrics'] for r in group]
        route = [m['command_to_route_publication_s'] for m in metrics]
        after_route = [m['command_to_first_token_s'] - a for m, a in zip(metrics, route)]
        tail = [m['command_to_correct_business_response_s'] - m['command_to_first_token_s'] for m in metrics]
        assert min(route + after_route + tail) >= 0
        primary[MODELS[2]][c] = dict(blocks=[r['block'] for r in group], ids=[r['attempt_id'] for r in group],
            A_s=[m['command_to_correct_business_response_s'] for m in metrics],
            prep_s=[m['preparation_s'] for m in metrics], gap_s=[0.0] * 9,
            startup_segments_s=dict(command_to_route_s=route, route_to_backend_first_token_s=after_route,
                                   backend_first_token_to_correct_http_s=tail),
            controller_mean_s=mean(r['runtime_observations']['total_elapsed_seconds'] for r in group))
    descriptive = {MODELS[0]: {}, MODELS[1]: {}, MODELS[2]: {}}
    for r in kimi['qualified_descriptives_including_labeled_unpaired']:
        if r['cohort'] == 'original':
            descriptive[MODELS[0]][r['condition']] = r['metrics']['A_s']['raw']
    for c in CONDITIONS:
        descriptive[MODELS[1]][c] = glm['descriptive_C23_26'][c]['A_s']
        descriptive[MODELS[2]][c] = [r['metrics']['command_to_correct_business_response_s'] for r in rows if r['success'] and r['condition'] == c]
    assert [len(descriptive[MODELS[0]][c]) for c in CONDITIONS] == [9, 9, 10, 10]
    assert [len(descriptive[MODELS[1]][c]) for c in CONDITIONS] == [6, 6, 7, 7]
    assert [len(descriptive[MODELS[2]][c]) for c in CONDITIONS] == [10, 9, 10, 9]
    summary = {}
    for model in MODELS:
        summary[model] = {}
        for c in CONDITIONS:
            g = primary[model][c]
            assert len(g['A_s']) == len(g['blocks'])
            assert all(math.isclose(a, sum(s), abs_tol=1e-8) for a, s in zip(g['A_s'], zip(*g['startup_segments_s'].values())))
            summary[model][c] = dict(n=len(g['A_s']), blocks=g['blocks'], A_mean_s=mean(g['A_s']),
                A_median_s=statistics.median(g['A_s']), prep_mean_s=mean(g['prep_s']), gap_mean_s=mean(g['gap_s']),
                prep_plus_A_mean_s=mean(g['prep_s']) + mean(g['A_s']),
                controller_mean_s=g['controller_mean_s'])
    for model in MODELS:
        cold = summary[model][CONDITIONS[0]]['A_mean_s']
        for c in CONDITIONS:
            summary[model][c]['relative_to_own_SF_cold'] = summary[model][c]['A_mean_s'] / cold
    return primary, descriptive, summary, formal, kimi, read(legacy / 'comparison-all40-glm-ledger.json')


def export(output):
    primary, descriptive, summary, formal, kimi, glm_ledger = build()
    output.mkdir(exist_ok=False)
    (output / 'comparison.json').write_text(json.dumps(dict(summary=summary, arrays=primary,
        descriptive_arrays=descriptive, v4_frozen_formal_statistics=formal,
        cohort_note='Legacy primary Kimi8/GLM6 unchanged; V4 blocks2..10 uniform-source9 is comparison-only descriptive sensitivity, not a replacement for frozen formal pair-specific n10/n9.'), indent=2) + '\n')
    with (output / 'comparison.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['model', 'condition', 'n', 'A_mean_s', 'A_median_s', 'prep_mean_s', 'gap_mean_s', 'prep_plus_A_mean_s', 'controller_mean_s', 'relative_to_own_SF_cold'])
        writer.writeheader()
        for model in MODELS:
            for c in CONDITIONS:
                writer.writerow(dict(model=model, condition=c, **{k: v for k, v in summary[model][c].items() if k != 'blocks'}))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.hashsalt': 'sllm-three-model-20261108', 'figure.dpi': 130})

    def save(fig, name, note):
        fig.text(.5, .012, note, ha='center', fontsize=9)
        fig.tight_layout(rect=(0, .045, 1, 1))
        for extension in ['png', 'svg', 'pdf']:
            fig.savefig(output / (name + '.' + extension), bbox_inches='tight')
        plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for j, model in enumerate(MODELS):
        for i, c in enumerate(CONDITIONS):
            x = j + (i - 1.5) * .19
            axes[0].bar(x, summary[model][c]['A_mean_s'], .18, color=COLORS[i], label=LABELS[i] if j == 0 else None)
            axes[0].scatter(x + np.linspace(-.035, .035, len(primary[model][c]['A_s'])), primary[model][c]['A_s'], s=9, c='#222222', alpha=.6)
            axes[1].bar(x, summary[model][c]['relative_to_own_SF_cold'], .18, color=COLORS[i])
    for ax in axes:
        ax.set_xticks(range(3), ['Kimi (n=8)', 'GLM C23 (n=6)', 'V4 (n=9)']); ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
    axes[0].set(title='Startup command to correct first business response', ylabel='Mean seconds'); axes[0].legend(fontsize=8)
    axes[1].set(title='Normalized within each model', ylabel='Latency / own SF-cold mean (lower is better)')
    save(fig, 'fig1_startup', 'Different engines, models, requests, CPU, JIT and media; descriptive comparison, no cross-model causal test.')

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.2))
    for ax, model in zip(axes, MODELS):
        for c, label, color in zip(CONDITIONS, LABELS, COLORS):
            values = sorted(descriptive[model][c]); ax.step(values, np.arange(1, len(values) + 1) / len(values), where='post', color=color, label=f'{label} n={len(values)}')
        ax.set(title=model, xlabel='Startup seconds', ylabel='ECDF', ylim=(0, 1.05)); ax.legend(fontsize=8); ax.grid(alpha=.2)
    save(fig, 'fig2_ecdf', 'Qualified descriptive samples only; failures remain in the original 40-ID denominator and are never set to zero.')

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3), sharey=True)
    for ax, model in zip(axes, MODELS):
        prep = [summary[model][c]['prep_mean_s'] for c in CONDITIONS]; a = [summary[model][c]['A_mean_s'] for c in CONDITIONS]
        ax.bar(range(4), prep, color='#A9BBC6', label='Recorded preparation'); ax.bar(range(4), a, bottom=prep, color=COLORS, label='Startup A')
        ax.set(title=model, xticks=range(4), xticklabels=LABELS, ylabel='Mean seconds'); ax.tick_params(axis='x', labelrotation=25); ax.legend(fontsize=8)
    save(fig, 'fig3_preparation', 'Prep+A excludes gap. Legacy Prep is target-state construction; V4 Prep includes environment/pool/admission. Not equivalent cost windows.')

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.1), sharey=True)
    for ax, model in zip(axes, MODELS):
        values = [summary[model][c]['controller_mean_s'] for c in CONDITIONS]
        if all(v is None for v in values): ax.text(.5, .5, 'Not available in the verified input\nNo imputation', ha='center', transform=ax.transAxes)
        else: ax.bar(range(4), values, color=COLORS)
        ax.set(title=model, xticks=range(4), xticklabels=LABELS, ylabel='Mean controller elapsed seconds'); ax.tick_params(axis='x', labelrotation=25)
    save(fig, 'fig4_controller', 'Kimi and V4 controller endpoints differ; observed envelopes, not nested-stage sums or total study cost. GLM remains missing.')

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    load = read(BASE / 'inputs/legacy/kimi-existing-startup-log-runtime-results-audit-20261107.json')
    axes[0].bar(range(4), [load['primary8_complete_blocks_description'][c]['mean_s'] for c in CONDITIONS], color=COLORS)
    axes[0].set_title('Kimi f7 log loading envelope (n=8)')
    axes[1].text(.5, .5, 'Same loading-envelope field absent\nNo zero or inferred duration', ha='center', transform=axes[1].transAxes); axes[1].set_title('GLM comparable field missing')
    rows = read(BASE / 'inputs/v4/results/qualified-rows.json')
    for j, (field, label) in enumerate([('phase_weight_ingestion_span_s', 'Ingestion'), ('phase_quantization_postprocess_span_s', 'Postprocess'), ('phase_weights_pipeline_including_allocation_span_s', 'Pipeline')]):
        axes[2].bar(np.arange(4) + (j - 1) * .25, [mean(r['metrics'][field] for r in rows if r['success'] and r['block'] >= 2 and r['condition'] == c) for c in CONDITIONS], .24, label=label)
    axes[2].set_title('V4 all-rank phase envelopes (n=9)'); axes[2].legend(fontsize=7)
    for ax in axes: ax.set(xticks=range(4), xticklabels=LABELS, ylabel='Mean seconds'); ax.tick_params(axis='x', labelrotation=25)
    save(fig, 'fig5_loading', 'Different observation definitions. V4 group H2D/postprocess interleave; phase envelopes overlap and must not be added.')

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.3))
    for ax, model in zip(axes, MODELS):
        bottom = np.zeros(4)
        for key, label, color in [('command_to_route_s', 'Command -> route', '#3267A5'), ('route_to_backend_first_token_s', 'Route -> backend first output', '#C57935'), ('backend_first_token_to_correct_http_s', 'First output -> correct HTTP', '#4D9568')]:
            values = np.array([mean(primary[model][c]['startup_segments_s'][key]) for c in CONDITIONS]); ax.bar(range(4), values, bottom=bottom, color=color, label=label); bottom += values
        ax.set(title=model, xticks=range(4), xticklabels=LABELS, ylabel='Mean seconds'); ax.tick_params(axis='x', labelrotation=25); ax.legend(fontsize=7)
    save(fig, 'fig6_exclusive', 'The three non-overlapping host-event intervals sum to A. Backend first output is not client streaming TTFT or GPU token time.')

    fig, axes = plt.subplots(3, 1, figsize=(13, 5.4))
    originals = [r for r in kimi['runs'] if r['cohort'] == 'original']
    groups = [originals, glm_ledger['rows'], rows]
    for ax, model, group in zip(axes, MODELS, groups):
        assert len(group) == 40
        mapping = {(r['block'], r['condition']): r for r in group}
        grid = [[int(mapping[(b, c)].get('qualified', mapping[(b, c)].get('success', mapping[(b, c)].get('status') == 'success'))) for b in range(1, 11)] for c in CONDITIONS]
        from matplotlib.colors import ListedColormap
        ax.imshow(grid, aspect='auto', cmap=ListedColormap(['#B84D4D', '#4D9568']), vmin=0, vmax=1)
        for i in range(4):
            for j in range(10): ax.text(j, i, 'OK' if grid[i][j] else 'FAIL', ha='center', va='center', fontsize=7, color='white')
        ax.set(yticks=range(4), yticklabels=LABELS, xticks=range(10), xticklabels=range(1, 11), title=model, xlabel='Original block (within model; not cross-model pairing)')
    save(fig, 'fig7_original40', 'All original 120 IDs retained: Kimi38/40, GLM35/40 mixed revisions, V4 38/40. No supplemental or pilot replacement.')
    print('CPU_COMPARISON_OK: SHA inputs, V4 independent-review join, frozen statistics, cohort counts, interval sums and seven figure families')


if __name__ == '__main__':
    export(Path(sys.argv[1]).resolve())
