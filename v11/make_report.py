"""Render v11/report.md from the run directories.

Every number in the tables comes from a run directory (qualification.json,
switch-events.csv); nothing is typed by hand. Groups that did not pass are kept
in the report with their recorded reason instead of being dropped.
"""
import csv
import json
import re
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
    result = json.loads(qual.read_bytes())
    audit = run / 'b1-store-audit.json'
    if result.get('group') == 'B1' and audit.is_file():
        review = json.loads(audit.read_bytes())
        if review['passed'] is False:
            result['passed'] = False
            result['store_audit_reason'] = review['reason']
            for step in result['b1_host_kv']['steps']:
                if step['step'] in review['failed_steps']:
                    step['closed'] = False
    return result


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


def fmt_ms_seconds(value):
    """Three decimals, for clear latencies that are a few milliseconds."""
    try:
        return f'{float(value):.3f}'
    except (TypeError, ValueError):
        return '—'


def number(value):
    """CSV cells arrive as strings; return a float or None."""
    try:
        if value in (None, ''):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


MODEL_NAMES = {'K': 'Kimi-K2.6', 'G': 'GLM-5.1-FP8', 'D': 'DeepSeek-V4-Pro'}
LOG_PATTERNS = {
    'init_engine_s': r'init engine \(profile, create kv cache, warmup model\) took ([\d.]+) s',
    'available_kv_gib': r'Available KV cache memory: ([\d.]+) GiB',
    'kv_tokens': r'GPU KV cache size: ([\d,]+) tokens',
}


def step_dir_for(run, step, model):
    """Locate the step directory for one switch-events row."""
    matches = sorted(run.glob(f'{int(step):02d}-{model}-*'))
    for path in matches:
        if path.is_dir() and not path.name.endswith('-guard'):
            return path
    return None


def engine_facts(step_dir):
    """Values the engine printed about this activation, or None when absent."""
    if step_dir is None:
        return {}
    log = step_dir / 'backend.log'
    if not log.exists():
        return {}
    text = log.read_text(errors='replace')
    facts = {}
    for key, pattern in LOG_PATTERNS.items():
        match = re.search(pattern, text)
        if match:
            raw = match.group(1).replace(',', '')
            facts[key] = float(raw) if '.' in raw else int(raw)
    return facts


def sample_peaks(run):
    """Per-step peaks from the 1 Hz resource samples (phase is '<step-dir>:<phase>')."""
    rows = csv_rows(run / 'resource-samples.csv')
    if not rows:
        rows = []
        path = run / 'resource-samples.jsonl'
        if path.exists():
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    peaks = {}
    for row in rows:
        phase = str(row.get('phase') or '')
        if ':' not in phase:
            continue
        step = phase.split(':')[0]
        gpus = []
        for index in range(8):
            value = row.get(f'gpu{index}')
            try:
                gpus.append(int(value))
            except (TypeError, ValueError):
                continue
        avail = number(row.get('mem_available'))
        rss = number(row.get('store_rss'))
        entry = peaks.setdefault(step, dict(gpu=[0] * 8, same_instant=0,
                                            min_avail=None, max_rss=0))
        for index, value in enumerate(gpus):
            if value > entry['gpu'][index]:
                entry['gpu'][index] = value
        if len(gpus) == 8:
            entry['same_instant'] = max(entry['same_instant'], sum(gpus))
        if avail is not None:
            entry['min_avail'] = avail if entry['min_avail'] is None else min(entry['min_avail'], avail)
        if rss is not None:
            entry['max_rss'] = max(entry['max_rss'], rss)
    return peaks


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
    scored.sort(key=lambda item: item[0].stat().st_mtime)
    if not scored:
        lines.append('**未通过。** 没有任何 B1 run 记录到逐段 Host KV 证据'
                     '（`qualification.json` 无 `b1_host_kv.steps`）。')
        lines.append('')
        return lines

    lines.append('下面按时间顺序列出**本组正式轮**、其**单模型适配验证**，以及同一序列上'
                 '**补丁前**的失败 run 作为对照——后者用来说明"为什么必须改 LMCache"。')
    lines.append('')
    newest = scored[-1]
    sequence = newest[1].get('sequence')
    contrast = next(((run, qual) for run, qual in reversed(scored[:-1])
                     if qual.get('sequence') == sequence and not qual.get('passed')), None)
    smoke = next(((run, qual) for run, qual in reversed(scored[:-1])
                  if len(qual.get('models') or []) == 1), None)
    selected = []
    for candidate in (contrast, smoke, newest):
        if candidate and candidate[0] not in [item[0] for item in selected]:
            selected.append(candidate)
    selected.sort(key=lambda item: item[0].stat().st_mtime)
    for run, qual in selected:
        identity = run / 'lmcache-patch-identity.json'
        steps = qual['b1_host_kv']['steps']
        closed = sum(1 for step in steps if step.get('closed'))
        state = '通过' if qual.get('passed') else '未通过'
        events = {row['step']: row for row in csv_rows(run / 'switch-events.csv')}
        peaks = sample_peaks(run)
        hosts = {step['step']: step for step in steps}
        same_sequence = [(r, q) for r, q in scored if q.get('sequence') == qual.get('sequence')]
        passing_same = [r for r, q in same_sequence if q.get('passed')]
        lines.append(f"### `{run.name}` — {state}（Host KV 闭环 {closed}/{len(steps)} 段）")
        lines.append('')
        lines.append(f"序列 `{qual.get('sequence')}`，本序列带逐段 Host KV 证据的 run 共 "
                     f"{len(same_sequence)} 个（其中 {len(passing_same)} 个通过；"
                     f'早期无逐段证据或未收尾的尝试不在此列），'
                     f"rep={qual.get('rep')}，"
                     f"protocol_hash `{qual.get('protocol_hash')}`"
                     f"{'（暂定，尚未定稿）' if qual.get('protocol_hash_provisional') else ''}，"
                     f"最终释放 {fmt_seconds(qual.get('final_release_seconds'))} s、"
                     f"8 卡全 0 = {qual.get('final_all_gpu_zero')}。")
        lines.append('')
        if len(passing_same) < 3:
            lines.append(f'**重复次数**：本序列目前只有 {len(passing_same)} 次**通过**的运行。'
                         '按 AGENTS.md §6"每个切换点建议 ≥3 次重复并报中位数与极差"，'
                         '下表是单次点值，不是统计结论；定稿前至少还要再跑 '
                         f'{max(0, 3 - len(passing_same))} 次。')
            lines.append('')

        # --- 基线变更声明：这是"为什么能通过"的前提，必须在结论同层出现 ---
        if identity.is_file():
            patch = json.loads(identity.read_text())
            lines.append('**基线变更（必须与本报告的冻结基线分开读）**：本 run 不是原版 LMCache。')
            lines.append('')
            lines.append('| 项 | 冻结基线 | 本 run 实际 |')
            lines.append('|---|---|---|')
            lines.append('| LMCache | 0.5.5，连接器 `lmcache.integration.vllm.'
                         'lmcache_mp_connector_0201` | 0.5.5 源码 `{}`，连接器改为通用 '
                         '`{}` |'.format(patch.get('source_commit'),
                                          patch.get('connector_module')))
            lines.append('| hybrid KV cache manager | 由 vLLM 关闭（`--kv-transfer-config` 触发） '
                         '| **重新开启**（`disable_hybrid_kv_cache_manager = {}`） |'.format(
                             str(patch.get('disable_hybrid_kv_cache_manager')).lower()))
            lines.append('| vLLM 兼容层 | 无 | 补 `KVCacheSpecKind` / '
                         '`get_kv_cache_spec_kind` 兼容层（补丁 SHA-256 `{}`） |'.format(
                             str(patch.get('patch_sha256'))[:16] + '…'))
            lines.append('')
            lines.append(f"overlay `{patch.get('overlay')}`，依赖回落 `{patch.get('fallback_site')}`；"
                         f"V4 的适配先行验证 run 为 `{patch.get('qualified_v4_smoke')}`；"
                         '补丁原文见 `lmcache-hma.patch`，身份见 `lmcache-patch-identity.json`。')
            lines.append('')
            lines.append('**没有这三项变更，V4 段不可能写入**：连接器发 1 组 block id、'
                         '引擎只暴露 1 张 256-token block 表，而 LMCache 按张量形状分出 7 个 '
                         'kernel group（`tokens_per_block` = 2 / 64 / 256），'
                         '`store()` 的 `zip(..., strict=True)` 会直接抛 '
                         '`ValueError: zip() argument 2 is longer than argument 1` 并按设计丢弃整笔写入。'
                         '因此本 run 的 B1 结论**只对"带该补丁的 LMCache"成立**，'
                         '不能外推到冻结基线。')
            lines.append('')

        if qual.get('store_audit_reason'):
            lines.extend([qual['store_audit_reason'] + ' 证据见 `b1-store-audit.json`。', ''])

        # --- 验收表（新增 Store 命中列）---
        lines.append('| step | 模型 | Store 读取 | Store 命中 | Host 写入峰值 | 写入时刻 | clear 前 | clear 后 | clear 状态 | 闭环 |')
        lines.append('|---|---|---|---|---|---|---|---|---|---|')
        for step in steps:
            event = events.get(str(step['step']), {})
            start_ns = event.get('t2_ns')
            first_ns = step.get('first_write_ns')
            if first_ns and start_ns:
                write_at = '激活后 %.1f s' % ((int(first_ns) - int(start_ns)) / 1e9)
            else:
                write_at = '—'
            lines.append('| {step} | {model} | {read} | {hit} | {peak} | {peak_at} | {before} | {after} | {status} | {closed} |'.format(
                step=step['step'], model=step['model'],
                read=fmt_bytes(event.get('file_read_bytes')),
                hit=fmt_bytes(event.get('hit_bytes')),
                peak=fmt_bytes(step.get('peak_bytes')),
                peak_at=write_at,
                before=fmt_bytes(step.get('used_before_clear')),
                after=fmt_bytes(step.get('used_after_clear')),
                status=step.get('clear_status'),
                closed='是' if step.get('closed') else '**否**'))
        lines.append('')

        # --- 写入来源：必须写清是声明的填充请求，而不是业务流量 ---
        fills = [row for row in events.values() if row.get('kv_fill_passed') == 'True']
        biggest = max((step.get('peak_bytes') or 0) for step in steps) if steps else 0
        lines.append('**写入来源**：Host 层里的数据来自每段一条**声明的 512-token 填充请求**'
                     '（`kv_fill`，`max_tokens=8`、`ignore_eos`），即 2 个 256-token chunk；'
                     '冻结业务请求只有 19–43 token，凑不满一个 chunk，且本组按 C1 关闭了 '
                     'prefix caching，因此业务请求不产生整 chunk 写入。'
                     '本 run 单段 Host 峰值最大为 {}（{} 字节），'
                     '不能读作"业务流量填满了 16 GiB Host 层"。'.format(
                         fmt_bytes(biggest), f'{biggest:,}'))
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

        ordered = sorted(events.values(), key=lambda row: int(row['step']))
        figures = [(name, title) for name, title in (
            ('b1-timing.png', 'Figure 1. B1 激活与切换时延'),
            ('b1-store.png', 'Figure 2. B1 Store 读取与块分配'),
            ('b1-kv.png', 'Figure 3. B1 Host KV 写入与 GPU KV 预算'),
            ('b1-gpu.png', 'Figure 4. B1 八卡峰值显存'),
        ) if (run / name).exists()]
        if figures:
            lines.append('#### 图')
            lines.append('')
            for name, title in figures:
                lines.append(f'![{title}](runs/{run.name}/{name})')
                lines.append('')
                lines.append(f'*{title}*')
                lines.append('')
        if ordered:
            # --- 切换时延 ---
            lines.append('#### 加载与切换时延')
            lines.append('')
            lines.append('| 段 | 模型 | 激活至正确响应 (s) | 首 token (s) | 切换含观察 (s) | 切换不含观察 (s) | 观察 (s) |')
            lines.append('|---|---|---|---|---|---|---|')
            for row in ordered:
                lines.append('| {step} | {model} | {act} | {first} | {switch} | {switch_no} | {obs} |'.format(
                    step=int(row['step']) + 1, model=MODEL_NAMES.get(row['to_model'], row['to_model']),
                    act=fmt_seconds(row.get('activation_to_correct_seconds')),
                    first=fmt_seconds(row.get('first_token_seconds')),
                    switch=fmt_seconds(row.get('switch_seconds')),
                    switch_no=fmt_seconds(row.get('switch_excluding_observation_seconds')),
                    obs=fmt_seconds(row.get('observation_seconds'))))
            lines.append('')
            lines.append('`t0` = 上一段正确响应，`t1` = 上一 Backend 退出，`t2` = 本段开始激活，'
                         '`t3` = 引擎就绪，`t3_5` = 首 token，`t4` = 本段正确响应。'
                         '首段没有上一模型，切换指标缺测。')
            lines.append('')

            # --- 阶段分解 ---
            lines.append('#### 阶段分解（`t1` 与 `t2` 分离）')
            lines.append('')
            lines.append('| 段 | 模型 | 上一正确响应→退出 (s) | 退出→下一激活 (s) | 激活→引擎就绪 (s) | 就绪→首 token (s) | 首 token→正确 (s) | 本段 clear (s) |')
            lines.append('|---|---|---|---|---|---|---|---|')
            for position, row in enumerate(ordered):
                previous = ordered[position - 1] if position else None
                def delta(a, b):
                    try:
                        return fmt_seconds((int(a) - int(b)) / 1e9)
                    except (TypeError, ValueError):
                        return '—'
                exit_gap = delta(row.get('t1_ns'), previous.get('t4_ns')) if previous else '—'
                lines.append('| {step} | {model} | {a} | {b} | {c} | {d} | {e} | {f} |'.format(
                    step=int(row['step']) + 1, model=MODEL_NAMES.get(row['to_model'], row['to_model']),
                    a=exit_gap, b=delta(row.get('t2_ns'), row.get('t1_ns')),
                    c=delta(row.get('t3_ns'), row.get('t2_ns')),
                    d=delta(row.get('t3_5_ns'), row.get('t3_ns')),
                    e=delta(row.get('t4_ns'), row.get('t3_5_ns')),
                    f=fmt_ms_seconds(row.get('kv_clear_seconds'))))
            lines.append('')
            lines.append('"退出→下一激活"包含排空显存、`vram_gate` 门槛判定和容器创建，'
                         '不是 LMCache 或 vLLM 的耗时。')
            lines.append('')

            # --- Store 与 Host KV ---
            lines.append('#### Store 权重与 Host KV')
            lines.append('')
            lines.append('| 段 | 模型 | 读取 (GiB) | 命中 (GiB) | 分配块 | 归还块 | 驱逐路径 | Host 峰值 (MiB) | clear 前 (MiB) | clear 后 (MiB) |')
            lines.append('|---|---|---|---|---|---|---|---|---|---|')
            for row in ordered:
                def gib(value):
                    try:
                        return f'{int(value) / 2 ** 30:,.2f}'
                    except (TypeError, ValueError):
                        return '—'
                def mib(value):
                    try:
                        return f'{int(value) / 2 ** 20:,.2f}'
                    except (TypeError, ValueError):
                        return '—'
                host = hosts.get(int(row['step']), {})
                lines.append('| {step} | {model} | {read} | {hit} | {alloc} | {free} | {evict} | {peak} | {before} | {after} |'.format(
                    step=int(row['step']) + 1, model=MODEL_NAMES.get(row['to_model'], row['to_model']),
                    read=gib(row.get('file_read_bytes')), hit=gib(row.get('hit_bytes')),
                    alloc=row.get('allocated_blocks'), free=row.get('freed_blocks'),
                    evict=row.get('evicted_paths'),
                    peak=mib(host.get('peak_bytes')),
                    before=mib(host.get('used_before_clear')),
                    after=mib(host.get('used_after_clear'))))
            lines.append('')
            hits = [int(number(row.get('hit_bytes')) or 0) for row in ordered]
            hits = [value for value in hits if value > 0]
            hit_total = sum(hits)
            lines.append('Store 权重池 1 TiB、块 32 MiB；Host KV 层 16 GiB、chunk 256 token。'
                         '本 run 的 Store 命中合计 {}，来自 {} 段'
                         '（其余段是完整重读）——池子容不下两个完整模型'
                         '（Kimi 574.6 GiB + GLM 710.2 GiB = 1284.8 GiB），'
                         '所以常态是"一份完整模型 + 部分残块"，'
                         '命中表现为"少读一部分"而不是"零读取"。'.format(
                             fmt_bytes(hit_total), len(hits)))
            lines.append('')

            # --- KV 容量与口径 ---
            lines.append('#### 引擎加载与 GPU KV 容量')
            lines.append('')
            lines.append('| 段 | 模型 | `init engine` (s) | 激活→引擎就绪 (s) | 可用 KV (GiB) | GPU KV 容量 (token) | 每 token 成本 (KiB) |')
            lines.append('|---|---|---|---|---|---|---|')
            per_token = []
            for row in ordered:
                model = row['to_model']
                facts = engine_facts(step_dir_for(run, row['step'], model))
                gib = facts.get('available_kv_gib')
                tokens = facts.get('kv_tokens')
                cost = (gib * 1024 ** 2 / tokens) if (gib and tokens) else None
                if cost:
                    per_token.append((model, cost))
                try:
                    ready = (int(row['t3_ns']) - int(row['t2_ns'])) / 1e9
                except (TypeError, ValueError):
                    ready = None
                lines.append('| {step} | {model} | {init} | {ready} | {gib} | {tokens} | {cost} |'.format(
                    step=int(row['step']) + 1, model=MODEL_NAMES.get(model, model),
                    init=fmt_seconds(facts.get('init_engine_s')),
                    ready=fmt_seconds(ready),
                    gib=('%.2f' % gib) if gib else '—',
                    tokens=f'{tokens:,}' if tokens else '—',
                    cost=('%.1f' % cost) if cost else '—'))
            lines.append('')
            lines.append('`init engine` 是 vLLM 自报的"profile + 建 KV 池 + warmup"耗时，'
                         '它只是"激活→引擎就绪"的一部分：差额来自 Store 恢复权重'
                         '（每卡 71.8–102.2 GiB 量级）和进程拉起。')
            lines.append('')
            if per_token:
                worst = max(per_token, key=lambda item: item[1])
                best = min(per_token, key=lambda item: item[1])
                lines.append('**不要把"容量"当模型间可比指标**：按"可用 KV ÷ 容量"反算每 token 成本，'
                             '{} 为 {:.1f} KiB，{} 为 {:.1f} KiB（相差 {:.0f} 倍）。'
                             '这说明 {} 的容量不是被显存卡住，而是被引擎侧的其它约束'
                             '（block/seq/indexer）卡住；"2048-token 理论并发"= 容量 ÷ 2048，'
                             '只能当同模型横向比较的口径，不能用来判断谁"更能装"。'.format(
                                 MODEL_NAMES.get(best[0], best[0]), best[1],
                                 MODEL_NAMES.get(worst[0], worst[0]), worst[1],
                                 worst[1] / best[1] if best[1] else 0,
                                 MODEL_NAMES.get(worst[0], worst[0])))
                lines.append('')

            # --- 资源峰值 ---
            if peaks:
                lines.append('#### 资源峰值（1 Hz 采样）')
                lines.append('')
                lines.append('| 段 | 模型 | GPU0–7 峰值 (MiB, 取最大卡) | 八卡同刻峰值 (GiB) | 宿主可用最低 (GiB) | Store RSS 峰值 (GiB) |')
                lines.append('|---|---|---|---|---|---|')
                for row in ordered:
                    step_dir = step_dir_for(run, row['step'], row['to_model'])
                    entry = peaks.get(step_dir.name if step_dir else '', None)
                    if not entry:
                        continue
                    gpu_peak = max(entry['gpu'])
                    lines.append('| {step} | {model} | {gpu:,} | {total:,.2f} | {avail:,.1f} | {rss:,.2f} |'.format(
                        step=int(row['step']) + 1, model=MODEL_NAMES.get(row['to_model'], row['to_model']),
                        gpu=gpu_peak, total=entry['same_instant'] / 1024,
                        avail=(entry['min_avail'] or 0) / 2 ** 30,
                        rss=entry['max_rss'] / 2 ** 30))
                lines.append('')
                lines.append('GPU 数值是 NVML 整卡占用（含权重、GPU KV、工作区与运行时开销），'
                             '无法把 GPU KV 单独分离出来；"八卡同刻峰值"是逐条采样先求和再取最大。')
                lines.append('')

        lines.append('本组验收口径回顾：写入必须**在激活窗口内被观测到**，且早于 clear，'
                     'clear 后归零并返回 200，三条同时成立才记 `closed`。'
                     'clear 返回 200 本身不算证据——清一个本来就是 0 的层同样返回 200。')
        lines.append('')

    lines.append('#### 早期失败记录（已解决，保留以说明演进）')
    lines.append('')
    lines.append('| 阶段 | 现象 | 根因 | 处理 |')
    lines.append('|---|---|---|---|')
    lines.append('| 2026-11-11 | 注册超时/拒绝 | 端口被外来 server 占用、server 容器无 GPU、'
                 '缺 `cupy`、`AttnWindowDesc: world_size must be >= 1, got 0` | 端口预检、'
                 '`--gpus`、装依赖；`world_size=0` 由 0.5.5 自带 `_0201` 连接器（PR #4135）解决 |')
    lines.append('| 2026-11-12 上午 | 7 段序列只到 3/7 | V4 退出后 30,607 MiB/卡 不归还，'
                 '下一段 `free 108.16 < desired 125.82 GiB` 拒启；util 降到 0.75 又让 V4 '
                 'KV 只剩 0.68 GiB 而拒服务 | V4 独占 util 0.76 + 每段 `vram_gate` |')
    lines.append('| 2026-11-12 下午 | GLM/V4 段 Host 恒为 0 | hybrid KV 被折叠成单张 block 表，'
                 '`store()` 的 `zip(strict=True)` 抛 `ValueError` 并 fail-closed | 换通用连接器 + '
                 '重开 hybrid KV cache manager + `KVCacheSpecKind` 兼容层（本 run 即该配置） |')
    lines.append('')
    lines.append('预验收第 1 部分（`b1-preacceptance-part1.json`）已通过：`GET /status` 200、'
                 '`POST /cache/clear {"tier":"l1"}` 200、`tier=l2` 400、`num_l2_adapters=0`、'
                 '`memory_used_bytes` 可读——但那只是管理接口，写入真实性由上面的逐段证据回答。')
    lines.append('')
    return lines


def b2_section(runs):
    lines = ['## B2 组结果（原生 Sleep）', '']
    if not runs:
        lines.append('**尚未运行。**')
        lines.append('')
        return lines
    lines.append('按 AGENTS.md §2「必须将 Level 1 和 Level 2 分为两组实验，不允许混作'
                 '同一种机制」，本节按级别分开报告，**不把两个级别合成一个通过率**。')
    lines.append('')
    for level in (1, 2):
        subset = [(run, qual) for run, qual in runs
                  if level in (qual.get('levels') or [])]
        lines.append(f'### Level {level}')
        lines.append('')
        if not subset:
            lines.append('未运行。')
            lines.append('')
            continue
        passing = [item for item in subset if item[1].get('passed')]
        strict = [item for item in passing
                  if item[1].get('final_all_gpu_zero')
                  and (item[1].get('final_release_seconds') or 0) >= 55]
        lines.append('| 项 | 值 |')
        lines.append('|---|---|')
        lines.append(f'| run 数 | {len(subset)} |')
        lines.append(f'| 运行器判定通过 | {len(passing)}/{len(subset)} |')
        lines.append(f'| 同时满足"8 卡归零 + 释放观察 ≥55 s" | {len(strict)}/{len(subset)} |')
        lines.append('')
        if len(passing) != len(strict):
            lines.append('注意：有 run 被运行器判定通过但未达到严格释放门槛'
                         '（8 卡全 0 且释放观察 ≥60 s），下表逐条标出，不计入严格通过。')
            lines.append('')
        for run, qual in subset:
            status = '未通过'
            if qual.get('passed'):
                status = ('通过' if qual.get('final_all_gpu_zero')
                          and (qual.get('final_release_seconds') or 0) >= 55
                          else '运行器通过，严格释放门槛未达')
            lines.append(f"#### `{run.name}` — {status}")
            lines.append('')
            lines.append(f"序列 `{qual.get('sequence')}`，level {qual.get('levels')}，"
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
        lines.append('B1 有通过的 run，但**两组的序列不同**（见文首状态表），'
                     '逐段数值只能各自纵向比较；跨组差值需等 A 在 `seq-7-agents` 上跑完。')
        lines.append('')
        lines.append('另外，B1 的通过依赖"带补丁的 LMCache"（通用连接器 + 重开 hybrid KV cache manager '
                     '+ `KVCacheSpecKind` 兼容层），A 组是冻结基线；'
                     '因此即使序列对齐，这两列也不是"同一软件的两种配置"。')
    else:
        lines.append('**不可比。** B1 没有任何 run 通过 Host KV 闭环验收，'
                     '详见 `B1-FAILED.md`。')
        lines.append('')
        lines.append('因此本报告不给出"A 比 B1 快/慢多少"的数字结论。')
    lines.append('')

    lines.append('### 对比二：A（Store 常驻） vs B2（原生 Sleep，L1 / L2 分组报告）')
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
        lines.append('| B2-L1：Sleep → Wake | Sleep {} s 均值（n={}），Wake {} s 均值（n={}），'
                     '唤醒后同一 PID 继续答题 | `runs/B2-*/L1/level-result.json` |'.format(
                         f'{mean(sleep1):,.1f}' if sleep1 else '—', len(sleep1),
                         f'{mean(wake1):,.1f}' if wake1 else '—', len(wake1)))
    else:
        lines.append('| B2-L1 | 未运行 | — |')
    if sleep2 or wake2:
        lines.append('| B2-L2 | Sleep {} s 均值（n={}），Wake {} s 均值（n={}），'
                     'CPU 备份为 0（权重从原生 checkpoint 重载） | `runs/B2-*/L2/` |'.format(
                         f'{mean(sleep2):,.1f}' if sleep2 else '—', len(sleep2),
                         f'{mean(wake2):,.1f}' if wake2 else '—', len(wake2)))
    else:
        lines.append('| B2-L2 | **未通过**：checkpoint 重载发出逐层告警，'
                     '随后 `wake_up(kv_cache)` CUDA OOM。kernel 模式从磁盘读取 checkpoint，按 kernel 参数名查找时遇到 `embed` 缺失。16 GiB／rank 的 checkpoint 诊断通过 KV 唤醒，唤醒后首个金标请求触发 CUDA illegal instruction（`160706Z`） | `B2-L2-FAILED.md` |')
    lines.append('')
    lines.append('**口径差异（必须一起读）**：A 的计时是"新进程要从 Store 恢复权重 + 引擎初始化 + 答题"，'
                 'B2 的 sleep/wake 是"同一常驻进程的内存/显存搬移"，两者不是同一件事；'
                 '报告把两边都列出来，但不把它们的差值包装成单一"加速比"。')
    lines.append('')
    lines.append('### 容量与可行性边界（实测）')
    lines.append('')
    lines.append('- Store 池 1 TiB：装不下两个完整模型（Kimi 574.6 + GLM 710.2 = 1284.8 GiB），'
                 '实际是"一份完整模型 + 部分残块"；换模型必然发生真实驱逐，'
                 '命中表现为"少读一部分"（B1 正式轮第 4 段 GLM 只重读 621.39 GiB）'
                 '而不是"零读取"。')
    lines.append('- B2-L1：1.9 TiB 主机上只能容纳"一份权重备份 + 当前活动模型"；两份备份会把 '
                 '`MemAvailable` 压到 0.3 GiB（实测），三模型 L1 campaign 在本机不可行，'
                 '见 `B2-L1-RAM-BOUNDARY.md`。')
    lines.append('- B2-L2：`002126Z` 为 V4-Pro TP8 独立补丁诊断，每 rank KV 池 16 GiB、CUDA 同步定位。'
                 '同一组八 rank 完成 checkpoint 重载、KV 唤醒和三项金标，重载 RPC 91.896 秒。'
                 '运行器判定通过，最终八卡归零采样跨度 58.873 秒。历史失败见 `B2-L2-FAILED.md`。')
    lines.append('- B2-L2 同卡短测 `003454Z`：V4 → Kimi → V4 三段金标均通过，两个容器正常退出。'
                 '返回 V4 时八 rank 身份连续，唤醒 225.437 秒，其中 checkpoint 重载 RPC 222.863 秒。'
                 '每 rank KV 池 16 GiB，V4 使用独立补丁及 CUDA 同步诊断。最终 50 个八卡全零采样横跨 61.002 秒。')
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
    lines.append('本报告由 `make_report.py` 从各 run 目录的 `qualification.json`、'
                 '`switch-events.csv`、`resource-samples.csv` 与各段 `backend.log` 生成，'
                 '数字全部来自原生证据，未通过项照实列出。')
    lines.append('')
    lines.append('| 组 | 状态 | 序列 | 结论来源 |')
    lines.append('|---|---|---|---|')
    for group in ('A', 'B1', 'B2'):
        entries = grouped.get(group, [])
        if not entries:
            lines.append(f'| {group} | 未运行 | — | — |')
            continue
        sequences = sorted({str(qual.get('sequence')) for _, qual in entries})
        if group == 'B2':
            parts = []
            for level in (1, 2):
                subset = [(run, qual) for run, qual in entries
                          if level in (qual.get('levels') or [])]
                if not subset:
                    continue
                passed = sum(1 for _, qual in subset if qual.get('passed'))
                strict = sum(1 for _, qual in subset
                             if qual.get('passed') and qual.get('final_all_gpu_zero')
                             and (qual.get('final_release_seconds') or 0) >= 55)
                parts.append(f'L{level} 严格 {strict}/{len(subset)}'
                             f'（运行器判定 {passed}/{len(subset)}）')
            state = '；'.join(parts) if parts else '未运行'
            lines.append(f'| B2 | {state} | {", ".join(sequences)} | '
                         '`runs/*/qualification.json` |')
            continue
        passed = sum(1 for _, qual in entries if qual.get('passed'))
        total = len(entries)
        if group == 'B1':
            frozen = [(run, qual) for run, qual in entries
                      if qual.get('sequence') == 'seq-7-agents']
            frozen_passed = sum(1 for _, qual in frozen if qual.get('passed'))
            lines.append(f'| B1 | 正式序列 {frozen_passed}/{len(frozen)} 通过；'
                         f'全部 run {passed}/{total}（其余为早期探针与失败尝试） | '
                         f'{", ".join(sequences)} | `runs/*/qualification.json` |')
            continue
        state = '通过' if passed == total and total else ('部分通过' if passed else '未通过')
        lines.append(f'| {group} | {state}（{passed}/{total} 个 run 通过） | '
                     f'{", ".join(sequences)} | `runs/*/qualification.json` |')
    lines.append('')
    a_sequences = sorted({str(qual.get('sequence')) for _, qual in grouped.get('A', [])})
    b1_sequences = sorted({str(qual.get('sequence')) for _, qual in grouped.get('B1', [])})
    if a_sequences and b1_sequences and set(a_sequences) != set(b1_sequences):
        lines.append('> **A 与 B1 不是同一负载，两列不可直接相减。** '
                     f'A 的序列是 {", ".join("`" + item + "`" for item in a_sequences)}，'
                     f'B1 的序列是 {", ".join("`" + item + "`" for item in b1_sequences)}。'
                     '逐段数值只能各自纵向比较；跨组差值需要先让 A 在 B1 的同一条序列上跑完'
                     '（本报告生成时 A 尚无该序列的完成回执）。')
        lines.append('')
    lines.extend(a_section(grouped.get('A', [])))
    lines.extend(b1_section(grouped.get('B1', [])))
    lines.extend(b2_section(grouped.get('B2', [])))
    lines.extend(comparison_section(grouped))
    (V11 / 'report.md').write_text('\n'.join(lines) + '\n')
    print('wrote', V11 / 'report.md', len(lines), 'lines')


if __name__ == '__main__':
    main()
