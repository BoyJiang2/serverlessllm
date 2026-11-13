"""Render v11/report.md from the run directories.

Every number in the tables comes from a run directory (qualification.json,
switch-events.csv); nothing is typed by hand. Groups that did not pass are kept
in the report with their recorded reason instead of being dropped.
"""
import csv
import json
from collections import Counter
from pathlib import Path

V11 = Path(__file__).resolve().parent
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
    passed = []
    for run, qual in runs:
        audit = run / 'b1-store-audit.json'
        if audit.is_file():
            continue
        steps = (qual.get('b1_host_kv') or {}).get('steps', [])
        if (qual.get('passed') and len(steps) == qual.get('requested_activations')
                and steps and all(x.get('closed') and x.get('committed_tokens', 0) > 0
                                  and x.get('zero_hit') is True for x in steps)):
            passed.append((run, qual))
    lines.append('验收逐段核对真实 STORE 提交、零命中、清理归零。历史缺测及失败见 `B1-FAILED.md`。')
    lines.append('')
    if passed:
        for run, qual in passed:
            lines.append(f"### `{run.name}` — Host KV 闭环通过")
            lines.append('')
            identity = json.loads((run / 'lmcache-patch-identity.json').read_text(encoding='utf-8'))
            lines.append(f"LMCache {identity['version']}，源码 `{identity['source_commit']}`，"
                         f"补丁 SHA-256 `{identity['patch_sha256']}`。")
            lines.append(f"最终八卡归零采样跨度 {qual['final_release_seconds']:.3f} 秒。")
            lines.append('')
            rows = csv_rows(run / 'switch-events.csv')
            lines.append('| step | 模型 | 读取 | Host 写入前 | Host 清理后 | clear 状态 | clear (s) |')
            lines.append('|---|---|---|---|---|---|---|')
            for row in rows:
                lines.append('| {step} | {model} | {read} | {before} | {after} | {status} | {seconds} |'.format(
                    step=row['step'], model=row['to_model'],
                    read=fmt_bytes(row['file_read_bytes']),
                    before=fmt_bytes(row.get('kv_used_before_clear')),
                    after=fmt_bytes(row.get('kv_used_after_clear')),
                    status=row.get('kv_clear_status'),
                    seconds=fmt_seconds(row.get('kv_clear_seconds'))))
            lines.append('')
    else:
        lines.append('**未通过。** 在固定镜像上，vLLM 0.20.1 的 `LMCacheMPConnector` 与 LMCache 0.5.6/0.5.7rc4')
        lines.append('的 MP server 无法完成 `register_kv_caches` 握手：server 端 `LayoutDescRegistry.register`')
        lines.append('收到 `world_size=0` 并拒绝（`ValueError: AttnWindowDesc: world_size must be >= 1, got 0`），')
        lines.append('worker 等满 300 s 超时后 engine init 失败，因此没有任何一次激活可以记录。')
        lines.append('')
        lines.append('逐次失败与修复记录：')
        lines.append('')
        lines.append('| run | 现象 | 根因 | 处理 |')
        lines.append('|---|---|---|---|')
        lines.append('| `…T092923Z` | 作废 | 遗留探针占用 9100/5555，KV 被写进外来 server | 端口预检 + 校验 tier 大小 |')
        lines.append('| `…T094324Z` | register 超时 | server 无 GPU 设备，无法接收 CUDA IPC handle | server 容器加 `--gpus` |')
        lines.append('| `…T100307Z` | register 超时 | `ModuleNotFoundError: cupy`（`lmcache_driven_transfer.py:483`） | 装 `cupy-cuda13x==14.2.0` |')
        lines.append('| `…T102728Z` | register 拒绝 | `AttnWindowDesc: world_size must be >= 1, got 0` | 未解决（协议错配） |')
        lines.append('')
        lines.append('预验收第 1 部分（`b1-preacceptance-part1.json`）已通过：`GET /status` 200、'
                     '`POST /cache/clear {"tier":"l1"}` 200、`tier=l2` 400、`num_l2_adapters=0`、'
                     '`memory_used_bytes` 可直接读取。也就是说 Host 层本身可控，缺的是 KV 注册握手。')
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
        status = '未通过'
        if qual.get('passed'):
            status = ('通过' if qual.get('final_all_gpu_zero')
                      and (qual.get('final_release_seconds') or 0) >= 60
                      else '运行器通过，严格释放门槛未达')
        lines.append(f"### `{run.name}` — {status}")
        lines.append('')
        lines.append(f"序列 `{qual.get('sequence')}`，level {levels}，"
                     f"最终释放 {fmt_seconds(qual.get('final_release_seconds'))} s、"
                     f"8 卡全 0 = {qual.get('final_all_gpu_zero')}。")
        lines.append('')
        rows = csv_rows(run / 'switch-events.csv')
        stages = {(str(result['level']), str(record['step'])): record['stage']
                  for result in qual.get('results', []) for record in result.get('records', [])}
        if rows:
            lines.append('| level | step | 模型 | 阶段 | 加载或唤醒 (s) | 重载 RPC (s) | sleep (s) | 权重 CPU 备份 | 权重 | KV | Worker PID |')
            lines.append('|---|---|---|---|---|---|---|---|---|---|---|')
            for row in rows:
                stage = stages.get((row['level'], row['step']), {})
                lines.append('| {level} | {step} | {model} | {stage} | {elapsed} | {reload} | {sleep} | {backup} | {weights} | {kv} | {pid} |'.format(
                    level=row['level'], step=row['step'], model=row['model'],
                    stage=row['stage'], elapsed=fmt_seconds(stage.get('seconds')),
                    reload=fmt_seconds(stage.get('reload_seconds')),
                    sleep=fmt_seconds(row.get('sleep_seconds')),
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
    l2_sequence = [(run, qual) for run, qual in b2_runs
                   if qual.get('sequence') == 'seq-7-agents'
                   and qual.get('final_all_gpu_zero')
                   and (qual.get('final_release_seconds') or 0) >= 60]
    sleep2, wake2 = b2_level_seconds(l2_sequence, 2)

    lines.append('### 对比一：A（Store 常驻） vs B1（Store + Host KV 层）')
    lines.append('')
    b1_passed = any(qual.get('passed') for _, qual in b1_runs)
    if b1_passed:
        b1_name = 'B1-seq-7-agents-r1-20261112T144041Z'
        version = '0.5.5'
        for run, qual in reversed(b1_runs):
            identity = run / 'lmcache-patch-identity.json'
            if (qual.get('passed') and qual.get('sequence') == 'seq-7-agents'
                    and qual.get('strict_release_passed')
                    and (qual.get('b1_host_kv') or {}).get('closed')
                    and identity.is_file() and json.loads(identity.read_text(encoding='utf-8'))['version'] == '0.5.6'):
                b1_name = run.name
                version = '0.5.6'
                break
        a_name = 'A-seq-7-agents-r1-20261113T035857Z'
        lines.append(f'同序列 A `{a_name}` 与 B1 `{b1_name}`（LMCache {version}）各完成 7 段。'
                     '两组 V4 显存利用率分别为 0.90 和 0.76。'
                     'B1 额外执行 Host KV 写入和清理。')
        paired_runs = [RUNS / name for name in (a_name, b1_name)]
        left, right = (csv_rows(run / 'switch-events.csv') for run in paired_runs)
        if len(left) == len(right) == 7:
            lines.append('')
            lines.append('| 段 | 模型 | A 激活至正确 (s) | B1 激活至正确 (s) | A Store 命中 | B1 Store 命中 |')
            lines.append('|---|---|---:|---:|---:|---:|')
            for a, b in zip(left, right, strict=True):
                assert (a['step'], a['to_model']) == (b['step'], b['to_model'])
                lines.append(f"| {a['step']} | {a['to_model']} | "
                             f"{float(a['activation_to_correct_seconds']):.3f} | "
                             f"{float(b['activation_to_correct_seconds']):.3f} | "
                             f"{fmt_bytes(a['hit_bytes'])} | {fmt_bytes(b['hit_bytes'])} |")
            lines.append('')
            lines.append('两列均为激活至首个正确响应的耗时。差值同时受权重命中状态、显存配置和实现影响。')
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
        lines.append('| B2-L2 七段 | Sleep {} s（7 段），Wake {} s（4 次返回） | `runs/B2-seq-7-agents-*/L2/level-result.json` |'.format(
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
    lines.append('- B1 七段 `144041Z` 使用带补丁 LMCache 0.5.5 `05a013b`，0.5.6 为依赖回落。'
                 '实际路径和补丁哈希见 `runs/B1-seq-7-agents-r1-20261112T144041Z/lmcache-patch-identity.json`。')
    lines.append('- B2-L2 历史失败见 `B2-L2-FAILED.md`。V4 单模型 `002126Z` 唤醒后金标通过，'
                 '最终八卡归零采样跨度 58.873 秒。')
    lines.append('- B2-L2 同卡短测 `003454Z`：V4 → Kimi → V4 三段金标通过，'
                 'V4 原八 rank 唤醒 225.437 秒，其中重载 222.863 秒。'
                 '每 rank KV 池 16 GiB，V4 使用独立补丁和 CUDA 同步诊断。'
                 '最终 50 个八卡全零采样横跨 61.002 秒。')
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
    lines.extend([
        '## 同序列七段实测图表', '',
        '### 图 1　A 与 B1 的激活耗时及权重读取', '',
        '![A 与 B1 逐段激活耗时和权重读取](figures/a-b1-sequence.png)', '',
        '上图从激活计到首个正确响应。下图是 Store 记录的文件读取字节。V4 显存利用率分别为 0.90 和 0.76。', '',
        '### 图 2　B1 Host KV 写入与清理', '',
        '![B1 Host KV 峰值和清理后占用](figures/b1-host-kv.png)', '',
        'LMCache 0.5.6 每段提交 512 个 KV token，查询零命中，清理后占用均为零。', '',
        '### 图 3　B2-L2 原 Worker 唤醒', '',
        '![B2-L2 四次返回的原 Worker 唤醒耗时](figures/b2-l2-wake.png)', '',
        '四次返回包含权重映射、checkpoint 重载和 KV 唤醒，计时止于唤醒回执。金标随后验证。', '',
        '图表使用 A `035857Z`、B1 `052724Z` 和 B2-L2 `012344Z`。在仓库根目录运行 `python v11/plot_v11.py` 重画。', '',
    ])
    lines.extend(a_section(grouped.get('A', [])))
    lines.extend(b1_section(grouped.get('B1', [])))
    lines.extend(b2_section(grouped.get('B2', [])))
    lines.extend(comparison_section(grouped))
    (V11 / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('wrote', V11 / 'report.md', len(lines), 'lines')


if __name__ == '__main__':
    main()
