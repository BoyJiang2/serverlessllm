"""Offline figures: all C23 attempts primary; other protocols never pooled for latency."""
import argparse
import collections
import csv
import hashlib
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LogNorm
import numpy as np

from final_statistics import ROOT, RUN, CONDITIONS, checked_json, describe, load_rows

PROTOCOL = 'v13-canonical23-qualified-disk-jit-seed'
OLD_RUN = 'sllm-glm51fp8-20261026T225201Z-02cd4cdc'
DEST = ROOT/'results/figures'
LABELS = ['Store 内存池命中', 'Store 本地盘冷读', 'Safetensors 冷读', 'Safetensors 热读']
COLORS = ['#167d8d', '#64aeb9', '#d38140', '#765eac']
METRICS = ['weight_ingestion_span_s', 'command_to_first_token_s',
           'command_to_correct_business_response_s', 'preparation_s']
INPUTS = {}


def read(path, expected=None):
    path = Path(path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected:
        assert sha == expected, str(path)
    INPUTS[path.relative_to(ROOT).as_posix()] = sha
    return json.loads(raw)


def exact(run, suffix):
    base = ROOT/'reviews/actual-controls'/run
    idx = read(base/'formal-exact-index.json')
    entries = idx['files'] if isinstance(idx, dict) else idx
    matches = [e for e in entries if e['path'].endswith(suffix)]
    assert len(matches) == 1, (run, suffix)
    e = matches[0]
    return read(base/e.get('local_path', e.get('local')), e['sha256'])


def write_csv(name, records):
    assert records
    with (DEST/name).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def hot_evidence(rows):
    out = []
    for row in rows:
        if row['condition'] != 'safetensors_disk_hot' or row['status'] != 'success':
            continue
        run, ident = row['execution_run_id'], row['attempt_id']
        assert run in (RUN, OLD_RUN)
        raw = read(ROOT/row['local_result_path'], row['result_sha256'])
        record = dict(attempt_id=ident, protocol_revision=row['protocol_revision'],
                      weight_s=row['metrics'][METRICS[0]],
                      business_s=row['metrics'][METRICS[2]])
        snapshots = []
        numa = []
        for when, suffix in [('pre', ''), ('post', '_after')]:
            d = exact(run, f'cache_{ident}{suffix}/summary.json')
            files = d['files']
            assert len(files) == 148 and len({x['file'] for x in files}) == 148
            assert all(0 <= x['resident_pages'] <= x['pages'] and x['pages'] > 0 for x in files)
            pages = sum(x['pages'] for x in files)
            resident = sum(x['resident_pages'] for x in files)
            record.update({f'{when}_files': len(files), f'{when}_pages': pages,
                           f'{when}_resident_pages': resident,
                           f'{when}_resident_fraction': resident/pages,
                           f'{when}_minimum_file_fraction': min(x['resident_pages']/x['pages'] for x in files),
                           f'{when}_verification_begin_ns': d['verification_begin_ns'],
                           f'{when}_verification_complete_ns': d['verification_complete_ns']})
            snapshots.append({x['file']: (x['bytes'], x['pages'], x['page_size']) for x in files})
            numa.append(d)
            if when == 'pre':
                assert d['cache_condition_passed'] is True
                record['repair_passes'] = d['repair_passes']
                record['reader_node'] = d['hot_placement']['node']
                record['eviction_resident_pages'] = d['hot_placement']['eviction_resident_pages']
                record['startup_after_scan_s'] = (raw['startup_command_ns']-d['verification_complete_ns'])/1e9
        assert snapshots[0] == snapshots[1]
        def vm(snapshot):
            return {line.split()[0]: int(line.split()[1]) for line in snapshot['vmstat'].splitlines()}
        for interval, begin, end in [('preparation', numa[0]['numa_before'], numa[0]['final_numa_after']),
                                     ('service_through_postscan', numa[0]['final_numa_after'], numa[1]['final_numa_after'])]:
            before, after = vm(begin), vm(end)
            for counter in ('pgscan_kswapd', 'pgscan_direct', 'pgsteal_kswapd', 'pgsteal_direct',
                            'pgmajfault', 'workingset_refault_file', 'numa_pages_migrated'):
                record[f'{interval}_{counter}_global_delta'] = after[counter]-before[counter]
        for metric in ('phase_device_setup_including_communication_span_s', 'phase_compile_graph_and_warmup_span_s'):
            record[metric] = row['metrics'][metric]
        record['memory_events_before'] = raw.get('memory_events_before', '').replace('\n', ';')
        record['memory_events_after'] = raw.get('memory_events_after', '').replace('\n', ';')
        out.append(record)
    assert len(out) == 7 and sum(r['protocol_revision'] == PROTOCOL for r in out) == 6
    return out


def figure(title, subtitle, ncols=1):
    fig, axes = plt.subplots(1, ncols, figsize=(12.4, 7.0), squeeze=False)
    fig.suptitle(title, x=.075, y=.96, ha='left', fontsize=21, weight='bold', color='#153345')
    fig.text(.075, .885, subtitle, fontsize=10, color='#4c6069', va='top')
    fig.subplots_adjust(left=.19 if ncols == 1 else .09, right=.97, bottom=.20, top=.78, wspace=.32)
    return fig, axes[0]


def foot(fig, text):
    fig.text(.075, .075, text, fontsize=9, color='#4c6069', va='top', linespacing=1.55)


def dots(ax, groups, metric):
    for i, group in enumerate(groups):
        values = [r['metrics'][metric] for r in group]
        median = statistics.median(values)
        ax.bar(i, median, color=COLORS[i], width=.65, alpha=.8, zorder=2)
        ax.scatter(i+np.linspace(-.16, .16, len(values)), values,
                   s=27, color='#142d3c', edgecolors='white', linewidth=.5, zorder=3)
        ax.annotate(f'{median:.1f}', (i, max(values)), xytext=(0, 8), textcoords='offset points', ha='center', fontsize=10)
    ax.set_xticks(range(4), ['池命中', 'Store\n冷读', 'SF\n冷读', 'SF\n热读'])
    ax.set_ylabel('秒')
    ax.set_ylim(0, ax.get_ylim()[1]*1.12)
    ax.grid(axis='y', alpha=.18, zorder=0)


def main(output):
    DEST.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    font_manager.fontManager.addfont('C:/Windows/Fonts/msyh.ttc')
    plt.rcParams.update({'font.family': 'Microsoft YaHei', 'font.size': 11, 'axes.unicode_minus': False,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.hashsalt': 'glm51fp8-c23-figures-v1', 'pdf.fonttype': 42})
    rows = load_rows()
    read(ROOT/'results/formal-ledger.json')
    old = read(ROOT/'results/final-statistics.json', '3a46d25c3879feab83ed42abf357db285e6f8af8708135dbace7db250c46cedb')
    costs = read(ROOT/'results/c23-costs.json', '336d3d51a9991fbd8f906dec8fd0f407cf9ce585f29efc1cfea739c521976fce')
    for r in rows:
        read(ROOT/r['local_result_path'], r['result_sha256'])
    current = [r for r in rows if r['protocol_revision'] == PROTOCOL]
    assert len(current) == 26 and all(r['status'] == 'success' and r['execution_run_id'] == RUN for r in current)
    assert len({tuple(r[k] for k in ('node', 'boot_id', 'code_commit', 'source_manifest_sha256',
                                   'protocol_revision', 'preparation_revision', 'resource_window_revision')) for r in current}) == 1
    groups = [[r for r in current if r['condition'] == c] for c in CONDITIONS]
    assert list(map(len, groups)) == [7, 7, 6, 6]
    summary = []
    for proto in dict.fromkeys(r['protocol_revision'] for r in rows):
        for condition in CONDITIONS:
            cohort = [r for r in rows if r['protocol_revision'] == proto and r['condition'] == condition]
            if not cohort:
                continue
            good = [r for r in cohort if r['status'] == 'success']
            for metric in METRICS:
                vals = [r['metrics'][metric] for r in good if metric in r['metrics']]
                summary.append(dict(protocol_revision=proto or 'UNKNOWN_RAW_METADATA', condition=condition,
                                    attempted=len(cohort), success=len(good), failed=len(cohort)-len(good),
                                    metric=metric, **(describe(vals) if vals else dict(n=0, mean=None, median=None,
                                    p90_nearest_rank=None, minimum=None, maximum=None))))
    # Independent recomputation must reproduce each existing C23 stratum, not a matched-six mean.
    for s in summary:
        if s['protocol_revision'] == PROTOCOL:
            prior = next(x for x in old['strata'] if x['stratum']['protocol_revision'] == PROTOCOL
                         and x['stratum']['condition'] == s['condition'])
            assert s['mean'] == prior['metrics'][s['metric']]['mean']
    write_csv('protocol-summary.csv', summary)
    raw_records = []
    for r in rows:
        raw_records.append(dict(order=r['order'], attempt_id=r['attempt_id'], protocol_revision=r['protocol_revision'],
                                node=r['node'], boot_id=r['boot_id'], code_commit=r['code_commit'],
                                source_manifest_sha256=r['source_manifest_sha256'],
                                preparation_revision=r['preparation_revision'], resource_window_revision=r['resource_window_revision'],
                                status=r['status'], result_sha256=r['result_sha256'],
                                **{m: (r['metrics'] or {}).get(m) for m in METRICS}))
    write_csv('all40-stratified.csv', raw_records)
    baseline = []
    for c, group in zip(CONDITIONS, groups):
        for metric in METRICS[:3]:
            target = statistics.mean(r['metrics'][metric] for r in group)
            cold = statistics.mean(r['metrics'][metric] for r in groups[2])
            baseline.append(dict(condition=c, metric=metric, n=len(group), mean_s=target,
                                 cold_baseline_n=6, cold_baseline_mean_s=cold,
                                 cold_mean_divided_by_path_mean=cold/target,
                                 interpretation='Postrun descriptive ratio of all-C23 means; unequal n; no ratio CI'))
    write_csv('c23-baseline-comparison.csv', baseline)
    write_csv('c23-all26-metrics.csv', [dict(attempt_id=r['attempt_id'], **r['metrics']) for r in current])
    supplement = []
    for condition, metrics in old['c23_matched']['intervals'].items():
        for metric in METRICS[:3]:
            d = metrics[metric]
            supplement.append(dict(condition=condition, metric=metric, n=d['point']['n'],
                                   mean=d['point']['mean'], mean_ci95_low=d['ci95']['mean'][0], mean_ci95_high=d['ci95']['mean'][1],
                                   scope='Supplement only: C23 matched blocks5-10; marginal percentile bootstrap; not all26 CI'))
    write_csv('matched-six-supplement.csv', supplement)
    hot = hot_evidence(rows)
    write_csv('hot-seven-residency.csv', hot)
    figs = []
    fig, axs = figure('01  |  四条原生服务路径：中位数与逐次观测',
                     '主分析：canonical23 全部26次成功；池命中 / Store冷读各7次，Safetensors冷 / 热读各6次。\n'
                     '相同官方FP8模型、TP=8及原生服务栈；四种条件均保留716 GiB公共池。', 3)
    for ax, metric, title in zip(axs, METRICS[:3], ['权重载入', '启动指令到首token', '启动指令到正确业务响应']):
        dots(ax, groups, metric)
        ax.set_title(title, fontsize=12, pad=18)
    foot(fig, '柱及数字为中位数，散点保留全部观测；热读的单次低值可见，不用均值掩盖差异。历史修订不混算。\n'
              '首token取后端事件，非客户端网络首字节；无置信区间柱，中位数也不是对任一次观测的替代。')
    figs.append(('01-latency-baseline', fig))

    fig, (ax,) = figure('02  |  条件准备同样需要付出成本',
                       'canonical23 全部26项；本页使用算术均值，两个时间区间互不重叠，单位：秒。')
    prep, after = [], []
    for group in groups:
        raws = [read(ROOT/r['local_result_path']) for r in group]
        prep.append(statistics.mean((r['preparation_complete_ns']-r['preparation_begin_ns'])/1e9 for r in raws))
        after.append(statistics.mean((r['correct_business_response_ns']-r['preparation_complete_ns'])/1e9 for r in raws))
    ax.barh(range(4), prep, color='#cbd8df', label='条件准备')
    ax.barh(range(4), after, left=prep, color='#167d8d', label='准备完成到首个正确业务响应')
    for i, (p, a) in enumerate(zip(prep, after)):
        if p < 30:
            ax.text(p+4, i, f'准备 {p:.1f}', ha='left', va='center', fontsize=8, color='white')
        else:
            ax.text(p/2, i, f'{p:.1f}', ha='center', va='center', fontsize=10)
        ax.text(p+a/2, i, f'{a:.1f}', ha='center', va='center', color='white')
        ax.text(p+a+5, i, f'{p+a:.1f}', va='center')
    ax.set_yticks(range(4), LABELS); ax.invert_yaxis(); ax.set_xlabel('秒'); ax.set_xlim(0, max(np.array(prep)+after)*1.12)
    ax.legend(loc='upper left', bbox_to_anchor=(0, 1.15), ncol=2, frameon=False, fontsize=8)
    foot(fig, '本批公共初始化238.77秒，单独记账，不分摊后隐藏；颜色只表示阶段，四行使用相同的两种颜色。\n'
              '以上不是完整部署成本；资格验证、审批闲置、后续请求、删除和释放仍需另计。')
    figs.append(('02-preparation-cost', fig))

    fig, (ax,) = figure('03  |  启动阶段：权重载入占据主要差异',
                       'canonical23 全部26项的算术均值；每格是独立事件区间，存在嵌套，不得相加。')
    phase_keys = ['phase_device_setup_including_communication_span_s', 'phase_communication_groups_span_s',
                  'weight_ingestion_span_s', 'phase_quantization_postprocess_span_s', 'phase_kv_cache_allocation_span_s',
                  'phase_compile_graph_and_warmup_span_s', 'backend_construct_s', 'command_to_route_publication_s']
    matrix = np.array([[statistics.mean(r['metrics'][k] for r in g) for k in phase_keys] for g in groups])
    image = ax.imshow(matrix, cmap='YlGnBu', norm=LogNorm(vmin=.05, vmax=500), aspect='auto')
    ax.set_yticks(range(4), LABELS)
    ax.set_xticks(range(8), ['设备初始化\n含通信', '通信组\n初始化', '权重\n载入', 'FP8\n后处理', 'KV池\n分配', '图编译 /\n预热', '后端\n构建', '指令到\n路由发布'], fontsize=9)
    for i in range(4):
        for j in range(8):
            ax.text(j, i, f'{matrix[i,j]:.2f}', ha='center', va='center', color='white' if matrix[i,j]>20 else '#183444', fontsize=10)
    fig.colorbar(image, ax=ax, pad=.02, label='秒（对数色阶）', shrink=.9)
    foot(fig, '设备初始化包含通信组；后端构建包含多项子阶段；路由发布相对于启动指令。此图不是可相加的分解。\n'
              'Store冷读的FP8后处理反而更慢：7.13秒 vs 0.58秒，不能用“省掉后处理”解释其优势。')
    figs.append(('03-startup-phases', fig))

    fig, axs = figure('04  |  删除实例之后：公共池仍然驻留',
                     'canonical23 每次删除后观察60秒；worker匿名内存与整批退出后的可回收文件缓存是不同口径。', 3)
    trial_groups = [[t for t in costs['trials'] if t['condition'] == c] for c in CONDITIONS]
    anon = [[t['idle_worker_anon_bytes']['mean']/2**30 for t in g] for g in trial_groups]
    cpu = [[t['idle_worker_average_cpu_cores'] for t in g] for g in trial_groups]
    all_anon = [x for g in anon for x in g]
    axs[0].axis('off')
    axs[0].text(.5, .88, 'worker 匿名内存', ha='center', transform=axs[0].transAxes, fontsize=12)
    axs[0].text(.5, .61, f'≈{statistics.mean(all_anon):.0f} GiB', ha='center', transform=axs[0].transAxes, fontsize=28, color='#167d8d', weight='bold')
    axs[0].text(.5, .36, f'四条件均保留公共池\n逐次60秒均值范围\n{min(all_anon):.2f}–{max(all_anon):.2f} GiB\n配置池容量：716 GiB', ha='center', transform=axs[0].transAxes, fontsize=11, linespacing=1.8)
    for i, vals in enumerate(cpu):
        axs[1].bar(i, statistics.mean(vals), color=COLORS[i], alpha=.8)
        axs[1].scatter(i+np.linspace(-.15, .15, len(vals)), vals, color='#153345', s=16)
    axs[1].set_xticks(range(4), ['池命中', 'Store', 'SF冷', 'SF热'])
    axs[1].set_title('worker 闲置CPU', fontsize=12); axs[1].set_ylabel('平均占用核数'); axs[1].grid(axis='y', alpha=.15)
    axs[2].bar([0, 1], [1769/1024, 0], color=['#167d8d', '#cbd8df'])
    axs[2].set_xticks([0, 1], ['删除\n单个实例', '整个批次\n退出'])
    axs[2].set_title('每卡GPU显存', fontsize=12); axs[2].set_ylabel('GiB，8张卡数值相同')
    foot(fig, f'worker口径不含head与宿主；完整部署cgroup采样峰值为{costs["sampled_cgroup_peak_bytes"]/2**30:.1f} GiB，不是内核峰值。\n'
              f'整批退出：自有任务 / 匿名 / shmem均为0；仍保留{costs["final_release"]["file_bytes"]/2**30:.1f} GiB可回收文件缓存及磁盘资产。')
    figs.append(('04-idle-resources', fig))

    fig, (ax,) = figure('05  |  原始成功35次，其中当前代码26次',
                       '当前canonical23：26成功 / 0失败；此前修订合计：9成功 / 5失败。原40个ID均已终态，无未开始项。')
    for r in rows:
        i = CONDITIONS.index(r['condition'])
        ax.scatter(r['order'], i, color=COLORS[i] if r['status']=='success' else '#be3e42',
                   marker='o' if r['status']=='success' else 'X', s=95, zorder=3)
    for boundary in [9.5, 10.5, 14.5]:
        ax.axvline(boundary, color='#91a1a8', ls='--', lw=1)
    ax.set_yticks(range(4), LABELS); ax.invert_yaxis(); ax.set_xticks(range(1, 41, 2)); ax.set_xlabel('原始预登记执行顺序')
    ax.set_xlim(.3, 40.7); ax.grid(axis='x', alpha=.12)
    foot(fig, '顺序1–9：初始修订（含原始metadata未知）；10：C19失败；11–14：C21；15–40：C23。\n'
              '红叉为失败，保留在分母；未重试、替换或改判。混合历史完成率不是当前代码的可靠性估计。')
    figs.append(('05-all40-outcomes', fig))

    fig, axs = figure('06  |  热读耗时分组与完整驻留端点',
                     '七次成功热读逐项展示：04属于历史C21，05–10属于当前C23；不混合两修订建立双峰分布。', 2)
    x = np.arange(7)
    axs[0].bar(x, [r['weight_s'] for r in hot], color=['#a6b3bc']+[COLORS[3]]*6, label='权重载入')
    axs[0].plot(x, [r['business_s'] for r in hot], 'o-', color='#153345', label='启动到正确业务响应')
    axs[0].set_ylabel('秒'); axs[0].legend(fontsize=8, frameon=False, loc='upper center', bbox_to_anchor=(.5, 1.15))
    for field, marker, label in [('pre_resident_fraction', 'o', '启动前'), ('post_resident_fraction', 'x', '删除后')]:
        axs[1].plot(x, [100*r[field] for r in hot], marker=marker, ls='--', label=label)
    axs[1].set_ylim(95, 101); axs[1].set_ylabel('冻结清单页驻留率（%）'); axs[1].legend(fontsize=9, frameon=False, loc='lower right')
    for ax in axs:
        ax.set_xticks(x, ['04\nC21']+[f'{i:02}\nC23' for i in range(5, 11)])
        ax.grid(axis='y', alpha=.15)
    foot(fig, '每份扫描覆盖148个冻结输入文件；每文件及所有列出页前后均100%驻留，未发现端点部分驻留污染。\n'
              'C23仅一次低值、五次高值；端点不证明连续驻留，亦不足以证明总体双峰或两种因果缓存状态。')
    figs.append(('06-hot-residency', fig))

    fig, (ax,) = figure('07  |  完整部署成本不止权重加载',
                       'C23从宿主准备到最终释放共220.90分钟；各行存在重叠，不得相加，也不代表GPU始终占用。')
    rawcurrent = [read(ROOT/r['local_result_path']) for r in current]
    host = exact(RUN, '/host-preparation.json')
    # The full host-to-release span and gaps are from the independently recomputed cost ledger.
    intervals = costs['intervals_nonadditive']
    total = intervals['host_preparation_begin_to_final_release_s']
    pre = intervals['host_preparation_s']
    gap = intervals['host_preparation_end_to_formal_head_dispatch_s']
    init = intervals['formal_common_initialization_s']
    first = min(r['preparation_begin_ns'] for r in rawcurrent)
    last = max(r['complete_ns'] for r in rawcurrent)
    # Align trial times to actual host monotonic origin, not sums of overlapping spans.
    host_start = host['begin_ns']
    spans = [('完整观察区间', 0, total, '#cbd8df'),
             ('宿主准备', 0, pre, '#657f8c'),
             ('资格验证 / 审批 / 派发间隔', pre, gap, '#e4b77d'),
             ('公共初始化', pre+gap, init, '#167d8d'),
             ('26次完整尝试周期', (first-host_start)/1e9, (last-first)/1e9, '#765eac'),
             ('最终释放观察', total-intervals['final_release_observation_s'], intervals['final_release_observation_s'], '#64aeb9')]
    for i, (label, start, duration, color) in enumerate(spans):
        ax.barh(i, duration/60, left=start/60, color=color, height=.62)
        ax.text((start+duration)/60+1, i, f'{duration/60:.2f} 分钟', va='center', fontsize=9)
    ax.set_yticks(range(len(spans)), [s[0] for s in spans], fontsize=9); ax.invert_yaxis(); ax.set_xlabel('距宿主准备开始的分钟数'); ax.set_xlim(0, total/60+31)
    fig.subplots_adjust(left=.28)
    foot(fig, '源码 / 编译种子复制：18.66 MB、583文件、0.169秒，属于宿主准备；最初创建成本记入历史账。\n'
              '四条件均保留公共池，资源等待、公共初始化及释放没有从成本中消失。')
    figs.append(('07-deployment-timeline', fig))

    cold_inputs = []
    for condition, group in ((CONDITIONS[1], groups[1]), (CONDITIONS[2], groups[2])):
        for row in group:
            d = exact(RUN, f"cache_{row['attempt_id']}/summary.json")
            files = d['files']
            assert all(x['resident_pages'] == 0 for x in files)
            cold_inputs.append(dict(attempt_id=row['attempt_id'], condition=condition,
                                    input_files=len(files), input_bytes=sum(x['bytes'] for x in files),
                                    weight_ingestion_s=row['metrics']['weight_ingestion_span_s'],
                                    postprocess_s=row['metrics']['phase_quantization_postprocess_span_s'],
                                    business_s=row['metrics']['command_to_correct_business_response_s'],
                                    physical_disk_read_bytes='NOT_MEASURED'))
    write_csv('cold-loading-evidence.csv', cold_inputs)
    by_cold = [[r for r in cold_inputs if r['condition'] == c] for c in (CONDITIONS[1], CONDITIONS[2])]
    assert all(len({r['input_bytes'] for r in g}) == 1 for g in by_cold)
    fig, axs = figure('08  |  Store冷读快在哪里：已有证据与缺口',
                      '同一修订、TP=8；冷读准入扫描输入均零驻留。资产大小、时间和物理磁盘读量是不同概念。', 3)
    columns = [('冻结输入总大小', 'input_bytes', 1e9, 'GB（十进制）'),
               ('权重载入时间', 'weight_ingestion_s', 1, '秒，算术均值'),
               ('FP8后处理时间', 'postprocess_s', 1, '秒，算术均值')]
    for ax, (title, field, divisor, unit) in zip(axs, columns):
        vals = [statistics.mean(r[field] for r in g)/divisor for g in by_cold]
        ax.bar([0, 1], vals, color=[COLORS[1], COLORS[2]], width=.6)
        ax.set_xticks([0, 1], ['Store冷读', 'SF冷读']); ax.set_title(title, fontsize=12); ax.set_ylabel(unit)
        ax.set_ylim(0, max(vals)*1.22)
        for i, value in enumerate(vals):
            ax.text(i, value, f'{value:.2f}', ha='center', va='bottom')
        ax.grid(axis='y', alpha=.15)
    foot(fig, 'Store冻结输入略大，且FP8后处理更慢；主要时间差体现在权重载入事件，不能据此归因具体机制。\n'
              '未采集可归属的物理读取字节或吞吐；文件大小不是实际I/O量，预分片的贡献仍需独立验证。')
    figs.append(('08-cold-mechanism-evidence', fig))

    with PdfPages(output/'glm51-fp8-results.pdf', metadata={'Title': 'GLM-5.1 FP8 - C23 stratified results',
                                                        'Author': 'ServerlessLLM extension experiment',
                                                        'CreationDate': None, 'ModDate': None}) as pdf:
        for name, fig in figs:
            fig.savefig(DEST/(name+'.svg'), metadata={'Date': None})
            fig.savefig(output/(name+'.png'), dpi=190)
            pdf.savefig(fig)
            plt.close(fig)
    report = ['# GLM-5.1 FP8 分层图表与基线对比', '',
              '主分析仅使用 `v13-canonical23-qualified-disk-jit-seed` 的全部26项：7/7/6/6。其余修订不混入性能主表。', '',
              '基线是相同官方FP8 checkpoint、TP=8、上下文/推理配置与原生ServerlessLLM服务路径的 Safetensors 本地盘冷读；四种条件均保留716 GiB公共Store池。',
              'A2独立vLLM参考只证明后端/正确性，包含审计开销，不能据此计算等价端到端加速。不同模型或论文硬件也不作为等价基线。', '',
              '## 主表：全部26项', '',
              '| 条件 | n | 权重均值/中位/P90(s) | 启动→首token均值(s) | 启动→正确业务均值/中位/P90(s) |',
              '|---|---:|---:|---:|---:|']
    for label, group in zip(LABELS, groups):
        w, token, b = [describe([r['metrics'][m] for r in group]) for m in METRICS[:3]]
        report.append(f'| {label} | {len(group)} | {w["mean"]:.3f} / {w["median"]:.3f} / {w["p90_nearest_rank"]:.3f} | {token["mean"]:.3f} | {b["mean"]:.3f} / {b["median"]:.3f} / {b["p90_nearest_rank"]:.3f} |')
    report += ['', 'P90采用nearest-rank；n=6/7时等于样本最大值。主表是全部已观察项的描述统计，不套用matched-six的置信区间。散点保留所有样本。',
               '不确定性补充：[matched blocks5–10](matched-six-supplement.csv)，四条件各6次，沿用已审阅的整块配对重采样（seed20261024，10000次；边际95%百分位区间，非同时覆盖）。样本小且连续采集，不能消除相关性，也不能代表全部26项的区间。', '',
               '冷Safetensors均值/各路径均值的比值见[c23-baseline-comparison.csv](c23-baseline-comparison.csv)，属于事后描述性比值、样本数不等，无加速比置信区间。', '',
               '## 其他protocol_revision单列', '',
               '| 原始修订 | 条件 | 总数/成功/失败 | 权重均值(s) | 正确业务均值(s) |', '|---|---|---:|---:|---:|']
    for s in summary:
        if s['protocol_revision'] == PROTOCOL or s['metric'] != METRICS[0]:
            continue
        b = next(x for x in summary if x['protocol_revision']==s['protocol_revision'] and x['condition']==s['condition'] and x['metric']==METRICS[2])
        wtext = f'{s["mean"]:.3f}' if s['mean'] is not None else 'NA'
        btext = f'{b["mean"]:.3f}' if b['mean'] is not None else 'NA'
        report.append(f'| {s["protocol_revision"]} | {s["condition"]} | {s["attempted"]}/{s["success"]}/{s["failed"]} | {wtext} | {btext} |')
    report += ['', '原始metadata未知仍为未知，不补成v8。协议分层表仍是历史描述；代码/manifest/preparation/window/node/boot逐项保留在[all40-stratified.csv](all40-stratified.csv)，原完整细分统计见[final-statistics.json](../final-statistics.json)。失败不计入耗时均值，但始终保留在总分母：35成功/5失败/0未开始。', '',
               '## Hot耗时分组与驻留核验', '',
               '| ID | 修订 | 权重(s) | 正确业务(s) | 前/后驻留率 | 最小单文件驻留率前/后 |', '|---|---|---:|---:|---:|---:|']
    for h in hot:
        report.append(f'| {h["attempt_id"]} | {"C23" if h["protocol_revision"]==PROTOCOL else "C21"} | {h["weight_s"]:.3f} | {h["business_s"]:.3f} | {h["pre_resident_fraction"]:.0%}/{h["post_resident_fraction"]:.0%} | {h["pre_minimum_file_fraction"]:.0%}/{h["post_minimum_file_fraction"]:.0%} |')
    report += ['', '14份完整summary各148文件、184621464页；逐文件resident_pages均等于pages。原完整mincore向量已由此前逐页原始审计验证，本图从保留的SHA绑定summary重算，不声称重新解压远端向量。repair_passes均0，准备前自有输入eviction扫描均0，reader均绑定node1。',
               'C23内hot08为65.661秒，其余五次138.942–145.085秒；C21 hot04为69.589秒，但不能与C23混合建立双峰分布。六次仅一个低值，尚不能统计确认总体双峰。',
               '差异主要落在权重ingestion；设备/通信和graph预热未出现对应约75秒差异。前后驻留均100%，没有准入时部分驻留污染的证据。准备区间及准备后至删除/观察后区间，global pgscan/pgsteal kswapd/direct增量均0。',
               '全局major-fault、file-refault、NUMA-migration有增量，但不能归属本模型/worker；reader CPU亲和性不证明文件页物理NUMA位置。完整mincore是约6秒的逐文件扫描窗口，非全模型同一瞬间快照。端点不能证明加载全程连续驻留，也不能证明两种真实缓存状态。根因未确定，保留hot08，不剔除、不改判、不补跑。',
               '逐次时刻、repair、计数器及阶段数据见[hot-seven-residency.csv](hot-seven-residency.csv)。进一步定因需要独立非计时控制中的按文件/worker实际磁盘读量、NUMA物理页和连续驻留归属证据；本次没有新增计算或改变正式记录。', '',
               '## 分图解读', '']
    explanations = [
        ('01-latency-baseline', '四条路径的中位数', '柱表示中位数，散点是全部26项。热读存在一个明显低值，故不以均值柱掩盖样本差异。所有样本来自相同C23修订；每个柱的样本量分别为7、7、6、6。'),
        ('02-preparation-cost', '准备和启动成本', '两种固定颜色分别代表条件准备以及准备完成到首个正确业务响应，区间互不重叠；本图使用均值。公共初始化238.77秒另计，审批、后续请求、删除及释放也不包含在柱内。'),
        ('03-startup-phases', '启动阶段', '权重载入、设备、通信、后处理和预热的事件区间存在嵌套，不得将热图各格相加。Store冷读的FP8后处理均值为7.13秒，Safetensors冷读为0.58秒，因此差异不能解释为省去后处理。'),
        ('04-idle-resources', '实例删除后的资源', '四种条件的worker匿名内存逐次60秒均值约719 GiB（范围718.85–719.48 GiB）；716 GiB是公共池配置值，不是worker总匿名内存。删除实例后每卡保留1769 MiB辅助显存。整个部署退出后自有任务、匿名内存和共享内存为零，但约705 GiB可回收文件缓存仍保留。'),
        ('05-all40-outcomes', '全部预登记尝试', '40项中35项原始成功、5项失败，当前C23贡献26项成功；其余历史修订有9成功和5失败。混合修订的35/40不能作为当前代码可靠性。失败没有重试或改判。'),
        ('06-hot-residency', '热读分组与驻留', '七次前后端点均完整驻留，C23仅一次低值和五次高值，历史C21低值单列。逐文件扫描不是同一瞬间或连续跟踪，无法据此确认总体双峰及其物理原因。'),
        ('07-deployment-timeline', '整体部署时间', '从宿主准备到最终释放为220.90分钟，其中包含资格验证与审批等待；阶段互相重叠，不能逐行相加，也不表示GPU连续占用。公共初始化和闲置成本单独保留。'),
        ('08-cold-mechanism-evidence', '冷读差异的证据边界', '冻结输入清单为Store 762.117 GB、Safetensors 756.209 GB，Store并不更小。权重载入均值121.455秒对391.671秒；FP8后处理均值7.13秒对0.58秒。实际服务响应均值180.875秒对448.496秒，约2.48倍。时间差主要落在权重载入事件，但各事件可能嵌套，不能相减拼成因果分解。Store已有按TP rank保存的布局；它可能改变读取和张量处理路径，其独立贡献未测量。现有记录缺少可归属的物理读取字节、设备吞吐及CPU/H2D分解，文件大小除以耗时不能称为磁盘吞吐，因此无法证明具体加速机制。后续需独立控制并记录各进程/cgroup的磁盘读量、CPU、H2D及阶段时间。')]
    for name, title, explanation in explanations:
        report += [f'### {title}', '', explanation, '', f'![{title}]({name}.svg)', '']
    report += ['## 复现与边界', '',
               '运行：`python -X utf8 tools/plot_results.py --output ../outputs/glm51-fp8-figures-v2`。需Python、matplotlib、numpy和微软雅黑字体；PDF由同一图形矢量导出。',
               'SVG/CSV/源码/输入及输出hash纳入Git；PDF/PNG在本机outputs目录，避免违反仓库二进制与单文件大小限制。manifest记录两类输出hash。',
               '阶段区间有嵌套，不能相加；worker闲置统计不等于整个部署。删除实例保留公共池及每卡1769 MiB辅助显存；整个批次退出后自有任务/匿名/shmem为0，可回收文件缓存约705 GiB和磁盘资产保留。',
               '本材料是ServerlessLLM在GLM-5.1官方FP8上的扩展实验，不声称复现论文原始模型或全部评测。历史exit137仍原因未知。']
    (DEST/'README.md').write_text('\n'.join(report)+'\n', encoding='utf-8')
    binary_names = [name+'.png' for name, _ in figs]+['glm51-fp8-results.pdf']
    output_hashes = {name: hashlib.sha256((output/name).read_bytes()).hexdigest() for name in binary_names}
    manifest = dict(schema='sllm-figures-v1', scope='Postrun visual analysis. Primary all26 C23, historical protocols separate.',
                    runtime_code=current[0]['code_commit'], source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    inputs=INPUTS, tracked_outputs={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.iterdir())
                                                  if p.suffix in ('.svg', '.csv', '.md')}, local_binary_outputs=output_hashes)
    (DEST/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'current': len(current), 'hot': len(hot), 'pdf_pages': len(figs), 'output': str(output)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True, help='Local binary output directory, outside tracked repository')
    main(parser.parse_args().output.resolve())
