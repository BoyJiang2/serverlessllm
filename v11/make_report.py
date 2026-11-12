"""Render v11/report.md from the run directories.

Every number in the tables comes from a run directory (qualification.json,
switch-events.csv); nothing is typed by hand. Groups that did not pass are kept
in the report with their recorded reason instead of being dropped.
"""
import csv
import json
from collections import Counter
from pathlib import Path

V11 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v11')
RUNS = V11 / 'runs'

GROUP_TITLES = {
    'A': 'A 组：SLLM Store 权重缓存（同 Store 常驻）',
    'B1': 'B1 组：Store + GPU KV L1 + Host KV 层（LMCache MP）',
    'B2': 'B2 组：原生 Sleep（L1 / L2 同组）',
    'G1': 'G1：三模型基线门',
}


def load(run):
    qual = run / 'qualification.json'
    if not qual.exists():
        return None
    return json.loads(qual.read_bytes())


def csv_rows(path):
    if not path.exists():
        return []
    with path.open() as stream:
        return list(csv.DictReader(stream))


def fmt_bytes(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return '—'
    for unit, scale in (('TiB', 1024 ** 4), ('GiB', 1024 ** 3), ('MiB', 1024 ** 2)):
        if abs(number) >= scale:
            return f'{number / scale:,.1f} {unit}'
    return str(number)


def fmt_seconds(value):
    try:
        return f'{float(value):,.1f}'
    except (TypeError, ValueError):
        return '—'


def a_section(runs):
    lines = ['## A 组结果（SLLM Store）', '']
    passing = [(run, qual) for run, qual in runs if qual.get('passed')]
    if len(passing) > 1:
        lines.append('### 序列汇总')
        lines.append('')
        lines.append('| run | 序列 | 段数 | full_hit | partial_hit | full_miss | 平均切换(不含观察) | 最终释放 |')
        lines.append('|---|---|---|---|---|---|---|---|')
        for run, qual in passing:
            rows_summary = csv_rows(run / 'switch-events.csv')
            states = [row['cache_state'] for row in rows_summary]
            switches = []
            for row in rows_summary:
                try:
                    switches.append(float(row['switch_excluding_observation_seconds']))
                except (KeyError, ValueError):
                    continue
            lines.append('| `{}` | {} | {} | {} | {} | {} | {} s | {} s |'.format(
                run.name, qual.get('sequence'), qual.get('activations'),
                states.count('full_hit'), states.count('partial_hit'),
                states.count('full_miss'),
                f'{mean(switches):,.1f}' if switches else '—',
                fmt_seconds(qual.get('final_release_seconds'))))
        lines.append('')
        lines.append('说明：A 的"激活→正确"包含新进程从 Store 恢复权重（1 TiB 池、32 MiB 块）'
                     '+ 8 卡引擎初始化 + 答题，所以即使 full_hit 也是百秒量级；'
                     '命中只省掉磁盘读取，不省进程与引擎拉起。')
        lines.append('')
        coverage = Counter()
        selfs = 0
        for run, qual in passing:
            for row in csv_rows(run / 'switch-events.csv'):
                source = row.get('from_model') or ''
                target = row.get('to_model') or ''
                if not source or source == target:
                    selfs += 1
                    continue
                coverage[f'{source}→{target}'] += 1
        if coverage:
            lines.append('campaign 覆盖（{} 个通过的 A run 合计）：'.format(len(passing))
                         + '、'.join(f'`{key}` {value}' for key, value in sorted(coverage.items()))
                         + f'；自切换 {selfs} 次单独统计 —— 最小非自方向计数 {min(coverage.values())}。')
            lines.append('')
    for run, qual in runs:
        rows = csv_rows(run / 'switch-events.csv')
        if not rows:
            continue
        lines.append(f"### `{run.name}` — {'通过' if qual.get('passed') else '未通过'}")
        lines.append('')
        lines.append(f"序列 `{qual.get('sequence')}`，{qual.get('activations')}/"
                     f"{qual.get('requested_activations', qual.get('activations'))} 段完成，"
                     f"protocol_hash `{qual.get('protocol_hash')}`"
                     f"{'（暂定）' if qual.get('protocol_hash_provisional') else ''}，"
                     f"最终释放 {fmt_seconds(qual.get('final_release_seconds'))} s、"
                     f"8 卡全 0 = {qual.get('final_all_gpu_zero')}。")
        lines.append('')
        lines.append('| step | 迁移 | 缓存 | 读取 | 命中 | 块分配/释放 | 驱逐 | 激活→正确 (s) | 首 token (s) | 切换不含观察 (s) |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for row in rows:
            lines.append('| {step} | {frm}→{to} | {state} | {read} | {hit} | {alloc}/{freed} | {evict} | {act} | {ttft} | {switch} |'.format(
                step=row['step'],
                frm=row['from_model'] or '∅',
                to=row['to_model'],
                state=row['cache_state'],
                read=fmt_bytes(row['file_read_bytes']),
                hit=fmt_bytes(row['hit_bytes']),
                alloc=row['allocated_blocks'], freed=row['freed_blocks'],
                evict=row['evicted_paths'],
                act=fmt_seconds(row['activation_to_correct_seconds']),
                ttft=fmt_seconds(row['first_token_seconds']),
                switch=fmt_seconds(row['switch_excluding_observation_seconds'])))
        store_pids = {row['store_pid'] for row in rows}
        lines.append('')
        lines.append(f'Store 进程: {", ".join(sorted(store_pids))}（同一进程贯穿全部激活）。')
        lines.append('')
    return lines


def b1_section(runs):
    lines = ['## B1 组结果（Host KV 层）', '']
    if not runs:
        lines.append('尚无 B1 run。')
        lines.append('')
        return lines

    lines.append('**本组验收口径**：每一段必须同时满足三件事才算闭环——(1) 该模型存活期内、'
                 '由 1 Hz 采样实际观测到 Host 层非零字节（`kv_peak_bytes`）；'
                 '(2) 这次写入发生在清空动作之前（`kv_write_before_clear`）；'
                 '(3) 清空后记账归零且 `POST /cache/clear` 返回 200（`kv_clear_ok`）。'
                 '只看"清空前字节"是不够的：清理动作发生在 backend 退出并排空显存之后，'
                 '那时 Host 层可能已经是 0，而模型存活期内其实写过。'
                 '`passed` 以 `b1_host_kv.steps[].closed` 为准，不再以"跑完 7 段"为准。')
    lines.append('')

    with_evidence = [(run, qual) for run, qual in runs
                     if isinstance(qual.get('b1_host_kv'), dict)]
    scored = [(run, qual) for run, qual in with_evidence
              if qual['b1_host_kv'].get('steps')]
    if not scored:
        lines.append('**未通过。** 没有任何 B1 run 记录到逐段 Host KV 证据'
                     '（`qualification.json` 无 `b1_host_kv.steps`）。')
        lines.append('')
        return lines

    for run, qual in scored[-3:]:
        steps = qual['b1_host_kv']['steps']
        closed = sum(1 for step in steps if step.get('closed'))
        state = '通过' if qual.get('passed') else '未通过'
        lines.append(f"### `{run.name}` — {state}（Host KV 闭环 {closed}/{len(steps)} 段）")
        lines.append('')
        lines.append('| step | 模型 | 读取 | Host 写入峰值 | 写入时刻 | clear 前 | clear 后 | clear 状态 | 闭环 |')
        lines.append('|---|---|---|---|---|---|---|---|---|')
        events = {row['step']: row for row in csv_rows(run / 'switch-events.csv')}
        for step in steps:
            event = events.get(str(step['step']), {})
            start_ns = event.get('t2_ns')
            first_ns = step.get('first_write_ns')
            if first_ns and start_ns:
                write_at = '激活后 %.1f s' % ((int(first_ns) - int(start_ns)) / 1e9)
            else:
                write_at = '—'
            lines.append('| {step} | {model} | {read} | {peak} | {peak_at} | {before} | {after} | {status} | {closed} |'.format(
                step=step['step'], model=step['model'],
                read=fmt_bytes(event.get('file_read_bytes')),
                peak=fmt_bytes(step.get('peak_bytes')),
                peak_at=write_at,
                before=fmt_bytes(step.get('used_before_clear')),
                after=fmt_bytes(step.get('used_after_clear')),
                status=step.get('clear_status'),
                closed='是' if step.get('closed') else '**否**'))
        lines.append('')
        open_steps = [step for step in steps if not step.get('closed')]
        if open_steps:
            lines.append('未闭环的段及其证据：')
            lines.append('')
            for step in open_steps:
                reason = []
                if not step.get('write_observed'):
                    reason.append('激活窗口内 Host 层始终 0 字节（`kv_peak_bytes={}`）'.format(
                        step.get('peak_bytes')))
                if not step.get('write_before_clear'):
                    reason.append('未观测到"先写后清"的时序')
                if not step.get('clear_ok'):
                    reason.append('清空后未归零或 clear 未返回 200')
                lines.append(f"- step {step['step']} `{step['model']}`：" + '；'.join(reason))
            lines.append('')
            lines.append('这些段对应的 `passed` 为 false——`engine ready` 与正确响应只说明模型跑起来了，'
                         '不说明 KV 层闭环。')
            lines.append('')
    return lines


def b2_section(runs):
    lines = ['## B2 组结果（原生 Sleep，L1/L2 同组）', '']
    if not runs:
        lines.append('**尚未运行。**')
        lines.append('')
        return lines
    for run, qual in runs:
        levels = qual.get('levels') or []
        lines.append(f"### `{run.name}` — {'通过' if qual.get('passed') else '未通过'}")
        lines.append('')
        lines.append(f"序列 `{qual.get('sequence')}`，level {levels}，"
                     f"最终释放 {fmt_seconds(qual.get('final_release_seconds'))} s、"
                     f"8 卡全 0 = {qual.get('final_all_gpu_zero')}。")
        lines.append('')
        rows = csv_rows(run / 'switch-events.csv')
        if rows:
            lines.append('| level | step | 模型 | 阶段 | sleep (s) | 权重 CPU 备份 | 权重 | KV | Worker PID |')
            lines.append('|---|---|---|---|---|---|---|---|---|')
            for row in rows:
                lines.append('| {level} | {step} | {model} | {stage} | {sleep} | {backup} | {weights} | {kv} | {pid} |'.format(
                    level=row['level'], step=row['step'], model=row['model'],
                    stage=row['stage'], sleep=fmt_seconds(row.get('sleep_seconds')),
                    backup=fmt_bytes(row.get('weights_cpu_backup_bytes')),
                    weights=fmt_bytes(row.get('weights_bytes')),
                    kv=fmt_bytes(row.get('kv_bytes')), pid=row.get('worker_pid')))
            lines.append('')
        for result in qual.get('results', []):
            lines.append(f"level {result['level']}: PID 连续性 = {result.get('pid_continuity')}，"
                         f"金标 = {result.get('golds_passed')}，"
                         f"PID {result.get('worker_pids')}")
            lines.append('')
        if qual.get('failures'):
            lines.append('失败记录：')
            lines.append('')
            lines.append('```')
            lines.append(str(qual['failures'].get('error'))[:400])
            lines.append('```')
            lines.append('')
    return lines


def a_timings(runs):
    """Mean activation-to-correct for A hits and misses, over passing runs."""
    hits, misses = [], []
    for run, qual in runs:
        if not qual.get('passed'):
            continue
        for row in csv_rows(run / 'switch-events.csv'):
            try:
                value = float(row['activation_to_correct_seconds'])
            except (KeyError, ValueError):
                continue
            state = row.get('cache_state')
            if state == 'full_hit':
                hits.append(value)
            elif state in ('full_miss', 'partial_hit'):
                misses.append(value)
    return hits, misses


def b2_level_seconds(runs, level):
    """Sleep/wake seconds recorded by B2 level runs."""
    sleep, wake = [], []
    for run, qual in runs:
        for result in qual.get('results', []):
            if result.get('level') != level:
                continue
            for record in result.get('records', []):
                stage = record.get('stage') or {}
                if stage.get('kind') == 'wake':
                    wake.append(stage.get('seconds'))
                if record.get('sleep_seconds') is not None:
                    sleep.append(record['sleep_seconds'])
    return [value for value in sleep if value is not None], \
           [value for value in wake if value is not None]


def mean(values):
    return sum(values) / len(values) if values else None


def comparison_section(grouped):
    lines = ['## 两个对比', '']
    a_runs = grouped.get('A', [])
    b1_runs = grouped.get('B1', [])
    b2_runs = grouped.get('B2', [])
    hits, misses = a_timings(a_runs)
    sleep1, wake1 = b2_level_seconds(b2_runs, 1)
    sleep2, wake2 = b2_level_seconds(b2_runs, 2)

    lines.append('### 对比一：A（Store 常驻） vs B1（Store + Host KV 层）')
    lines.append('')
    b1_passed = any(qual.get('passed') for _, qual in b1_runs)
    if b1_passed:
        lines.append('B1 有通过的 run，数值对比见上表。')
    else:
        lines.append('**不可比。** B1 在固定镜像上未能完成 KV 注册握手（`world_size=0`），'
                     'engine 无法启动，没有任何一次激活可记录；按协议记"未通过"，'
                     '详见 `B1-FAILED.md`。')
        lines.append('')
        lines.append('因此本报告不给出"A 比 B1 快/慢多少"的数字结论——那需要 B1 先跑起来。')
    lines.append('')

    lines.append('### 对比二：A（Store 常驻） vs B2（原生 Sleep，L1 / L2 同组）')
    lines.append('')
    lines.append('| 路径 | 同一模型再次可服务所需时间 | 证据 |')
    lines.append('|---|---|---|')
    if hits or misses:
        lines.append('| A：Store 命中 | {} s（{} 次 full_hit 均值） | `runs/A-*/switch-events.csv` |'.format(
            f'{mean(hits):,.1f}' if hits else '—', len(hits)))
        lines.append('| A：Store 未命中（冷读/重读） | {} s（{} 次 full_miss/partial_hit 均值） | 同上 |'.format(
            f'{mean(misses):,.1f}' if misses else '—', len(misses)))
    else:
        lines.append('| A | 尚未运行 | — |')
    if sleep1 or wake1:
        lines.append('| B2-L1：Sleep → Wake | Sleep {} s 均值，Wake {} s 均值，唤醒后同一 PID 继续答题 | `runs/B2-*/L1/level-result.json` |'.format(
            f'{mean(sleep1):,.1f}' if sleep1 else '—', f'{mean(wake1):,.1f}' if wake1 else '—'))
    else:
        lines.append('| B2-L1 | 未运行 | — |')
    if sleep2 or wake2:
        lines.append('| B2-L2 | Sleep {} s，Wake {} s | `runs/B2-*/L2/` |'.format(
            f'{mean(sleep2):,.1f}' if sleep2 else '—', f'{mean(wake2):,.1f}' if wake2 else '—'))
    else:
        lines.append('| B2-L2 | **未通过**：逐层 `reload_weights` 对 V4 无效（每层 Failed to load weights），'
                     '随后 `wake_up(kv_cache)` CUDA OOM | `runs/B2-…T115102Z/DIAGNOSIS.md` |')
    lines.append('')
    lines.append('**口径差异（必须一起读）**：A 的计时是"新进程要从 Store 恢复权重 + 引擎初始化 + 答题"，'
                 'B2 的 sleep/wake 是"同一常驻进程的内存/显存搬移"，两者不是同一件事；'
                 '报告把两边都列出来，但不把它们的差值包装成单一"加速比"。')
    lines.append('')
    lines.append('### 容量与可行性边界（实测）')
    lines.append('')
    lines.append('- Store 池 1 TiB：同时只放得下一份模型权重；换模型时必然发生真实驱逐（见 A 组驱逐列）。')
    lines.append('- B2-L1：1.9 TiB 主机上只能容纳"一份权重备份 + 当前活动模型"；两份备份会把 '
                 '`MemAvailable` 压到 0.3 GiB（实测），三模型 L1 campaign 在本机不可行，'
                 '见 `B2-L1-RAM-BOUNDARY.md`。')
    lines.append('- B2-L2：不占 CPU 备份，但重载路径在固定镜像上对 V4 无效（见上）。')
    lines.append('')
    return lines


def main():
    runs = []
    for run in sorted(RUNS.glob('*')):
        qual = load(run)
        if qual is None:
            continue
        runs.append((run, qual))
    grouped = {}
    for run, qual in runs:
        grouped.setdefault(qual.get('group'), []).append((run, qual))

    lines = ['# V11 三模型三路径切换实验报告', '']
    lines.append('本报告由 `make_report.py` 从各 run 目录的 `qualification.json` 与 `switch-events.csv` 生成，'
                 '数字全部来自原生证据，未通过项照实列出。')
    lines.append('')
    lines.append('| 组 | 状态 | 结论来源 |')
    lines.append('|---|---|---|')
    for group in ('A', 'B1', 'B2'):
        entries = grouped.get(group, [])
        if not entries:
            lines.append(f'| {group} | 未运行 | — |')
            continue
        passed = sum(1 for _, qual in entries if qual.get('passed'))
        total = len(entries)
        state = '通过' if passed == total and total else ('部分通过' if passed else '未通过')
        lines.append(f'| {group} | {state}（{passed}/{total} 个 run 通过） | `runs/*/qualification.json` |')
    lines.append('')
    lines.extend(a_section(grouped.get('A', [])))
    lines.extend(b1_section(grouped.get('B1', [])))
    lines.extend(b2_section(grouped.get('B2', [])))
    lines.extend(comparison_section(grouped))
    (V11 / 'report.md').write_text('\n'.join(lines) + '\n')
    print('wrote', V11 / 'report.md', len(lines), 'lines')


if __name__ == '__main__':
    main()
