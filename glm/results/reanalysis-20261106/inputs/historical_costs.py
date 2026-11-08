"""Keep known historical costs and explicit gaps; never manufacture a total from nested intervals."""
import datetime
import hashlib
import json
from pathlib import Path

from final_statistics import ROOT, checked_json
from final_costs import span


def main():
    sources = {}

    def source(path):
        data = path.read_bytes()
        key = path.resolve().as_posix()
        sources[key] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return data

    def events(path):
        return [json.loads(line) for line in source(path).splitlines() if line.strip()]

    def event_time(rows, name):
        matches = [r['monotonic_ns'] for r in rows if r['name'] == name]
        assert len(matches) == 1, name
        return matches[0]

    early = ROOT.parent/'outputs'
    first = source(early/'a1-evidence/download-workers4-complete-log.txt').decode().splitlines()[0]
    first = json.loads(first[first.index('{'):])
    resumed = events(early/'a1-evidence/download16-complete.log')
    last = resumed[-1]
    assert first['status'] == resumed[0]['status'] == 'start'
    assert last['status'] == 'all_files_verified' and last['files'] == 150
    delta = lambda a, b: (datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds()
    download = dict(first_begin_utc=first['time'], resumed_begin_utc=resumed[0]['time'], all_verified_utc=last['time'],
                    full_wall_s=delta(first['time'],last['time']), resumed_wall_s=delta(resumed[0]['time'],last['time']),
                    scope='Resumed interval included in full interval. Includes retry, switching and verification, not pure network time.')
    native = []
    for name in ('native_repeat7','native_repeat8'):
        rows = events(early/'native-reference'/name/'events.jsonl')
        native.append(dict(reference=name, scope='Backend correctness reference; includes full tensor audit, not formal timing.',
                           process_to_all_correct_s=span(event_time(rows,'process_started'),event_time(rows,'all_requests_correct')),
                           engine_create_to_ready_s=span(event_time(rows,'engine_create_begin'),event_time(rows,'engine_ready'))))
    component_rows = events(early/'store-evidence/store-validation-restoredraw3/events.jsonl')
    component = dict(scope='A3 component only; does not prove native service.',
                     engine_create_to_ready_s=span(event_time(component_rows,'engine_create_begin'),event_time(component_rows,'engine_ready')),
                     engine_create_to_checks_s=span(event_time(component_rows,'engine_create_begin'),event_time(component_rows,'A3_component_checks_passed')))
    conversion = json.loads(source(ROOT/'evidence/cost-sources/rawexport-event-collection.json'))
    ranks = []
    all_rows = []
    for f in conversion['files']:
        data = f['raw_utf8'].encode()
        assert len(data) == f['bytes'] and hashlib.sha256(data).hexdigest() == f['sha256']
        rows = [json.loads(line) for line in data.splitlines()]
        rank_set = {r['rank'] for r in rows if 'rank' in r}
        assert len(rank_set) == 1
        ranks.append(dict(rank=next(iter(rank_set)), source_sha256=f['sha256'],
                          weights_pipeline_including_export_s=span(event_time(rows,'weights_pipeline_including_allocation_begin'),event_time(rows,'weights_pipeline_including_allocation_end')),
                          gap_ingestion_end_to_postprocess_begin_s=span(event_time(rows,'weight_ingestion_end'),event_time(rows,'quantization_postprocess_begin'))))
        all_rows += rows
    assert sorted(r['rank'] for r in ranks) == list(range(8))
    launch = conversion['metadata'][0]['selected_fields']
    export = dict(run_id=launch['run_id'],boot_id=launch['boot_id'],ranks=ranks,
                  dispatch_to_latest_recorded_warmup_end_s=span(launch['dispatch_monotonic_ns'],max(r['monotonic_ns'] for r in all_rows if r['name']=='compile_graph_and_warmup_end')),
                  pure_save_s=None, scope='Actual eight-rank export-run events. Gap includes unbracketed work; not a proven pure Store-save interval. Final process/cleanup cost not closed here.')
    fields = {'ray_controls','service_supervisor','supervisor','phase','monitor','whole_monitor','resource',
              'resource_release','release','final_release','pool','pool_ready','service_final_state',
              'formal_supervisor','cost_scope','sampled_cgroup_peak_bytes','kernel_peak_bytes'}
    controls = []
    for path in sorted((ROOT/'reviews/actual-controls').glob('*/*outcome.json')):
        raw = json.loads(source(path))
        row = dict(run_id=raw['run_id'],source=path.relative_to(ROOT).as_posix(),
                   code_commit=raw.get('code_commit'),manifest_sha256=raw.get('manifest_sha256'),
                   status=raw.get('status'),recorded_cost_fields={k:v for k,v in raw.items() if k in fields or k.endswith('_seconds')},
                   missing_fields='Unknown, never zero; original source remains authoritative. Budgets inside raw supervisor records are not actual elapsed times.')
        correction = path.parent/'resource-correction.json'
        if correction.exists():
            row['effective_resource_correction'] = json.loads(source(correction))
            row['superseded_resource_note'] = 'Original sampled peak0 retained in source fields; use appended776676474880B correction, kernel peak still null.'
        controls.append(row)
    assert len(controls) == len({r['run_id'] for r in controls}) == 16
    c21_path = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261026T225201Z-02cd4cdc/cost-intervals.json'
    c21 = json.loads(source(c21_path))
    c23 = json.loads(source(ROOT/'results/c23-costs.json'))
    # No total is computed: early boot and current boot have different monotonic clocks.
    out = dict(schema='sllm-historical-cost-inventory-v1',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               scope='Known stage costs and missingness; not a complete historical resource integral or fee total.',
               status='Candidate evidence inventory; independent review required.', sources=sources,
               download=download,stable_native_references=native,component_restore=component,conversion=export,
               prior_control_outcomes=controls,c21_nonadditive_intervals=c21,
               c23_costs=dict(path='results/c23-costs.json',intervals_nonadditive=c23['intervals_nonadditive']),
               total_cost=None,total_cost_reason='Overlapping intervals, different boots, incomplete early failure/compile/idle/cleanup telemetry. No defensible complete sum.',
               unresolved=['Full pure-save interval and conversion resource peaks/integrals.','Earlier native/Store failed attempts, compiler and approval idle lack closed common timelines.','CPU seconds, host/GPU memory-time and energy not comprehensively recorded.','Assets are reusable; do not charge checkpoint/Store capacity as new allocation on every trial.','All original40 formal attempts including failures remain in formal-ledger.json; original9 per-ID raw timings remain separate from current costs.'])
    (ROOT/'results/historical-costs.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(dict(prior_control_runs=len(controls),conversion_ranks=len(ranks),download_wall_s=download['full_wall_s'],total_cost=None))


def c23_supplement():
    """Recover only archived CPU evidence; preserve all original reports and IDs."""
    import statistics
    from final_statistics import LEDGER_SHA

    outdir = ROOT/'results/reanalysis-20261106'
    sources = {}

    def read(path):
        b = path.read_bytes()
        sources[path.relative_to(ROOT).as_posix()] = dict(bytes=len(b), sha256=hashlib.sha256(b).hexdigest())
        return json.loads(b)

    ledger = read(outdir/'inputs/formal-ledger.json')
    assert sources['results/reanalysis-20261106/inputs/formal-ledger.json']['sha256'] == LEDGER_SHA
    monitor = read(outdir/'historical-monitor.json')
    assert monitor['historical_audit_matched'] and not monitor['malformed_line_numbers']
    windows = {(w['attempt_id'], w['window']): w for w in monitor['windows']}
    trials = []
    for state in ledger['rows']:
        if state.get('protocol_revision') != 'v13-canonical23-qualified-disk-jit-seed':
            continue
        path = outdir/'inputs'/state['attempt_id']/'result.json'
        x = read(path)
        assert sources[path.relative_to(ROOT).as_posix()]['sha256'] == state['result_sha256']
        a = windows[x['attempt_id'], 'attempt_recorded']
        assert (a['begin_ns'], a['end_ns']) == (x['preparation_begin_ns'], x['complete_ns'])
        start = windows[x['attempt_id'], 'startup_A']
        assert (start['begin_ns'], start['end_ns']) == (x['startup_command_ns'], x['correct_business_response_ns'])
        assert x['delete_complete_ns'] <= x['idle_observation_begin_ns'] < x['complete_ns']
        assert x['idle_observation_seconds'] >= 60
        trials.append(dict(attempt_id=x['attempt_id'], block=x['block'], condition=x['condition'],
                           recorded_attempt_s=span(a['begin_ns'], a['end_ns']),
                           delete_request_to_ack_s=span(x['delete_command_ns'], x['delete_complete_ns']),
                           idle_observation_s=x['idle_observation_seconds'],
                           startup_A=windows[x['attempt_id'], 'startup_A']['summary'],
                           attempt_resources=a['summary']))
    assert len(trials) == 26 and sum(s['status'] == 'success' for s in ledger['rows']) == 35
    summaries = {}
    for c in sorted({x['condition'] for x in trials}):
        selected = [x for x in trials if x['condition'] == c]
        paired = [x for x in selected if 5 <= x['block'] <= 10]
        assert len(paired) == 6
        summaries[c] = dict(descriptive_n=len(selected), complete_blocks_n=6,
                            recorded_attempt_mean_s=statistics.mean(x['recorded_attempt_s'] for x in selected),
                            complete_blocks_recorded_attempt_mean_s=statistics.mean(x['recorded_attempt_s'] for x in paired),
                            delete_ack_mean_s=statistics.mean(x['delete_request_to_ack_s'] for x in selected),
                            min_idle_s=min(x['idle_observation_s'] for x in selected),
                            peak_gpu_MiB=[max(x['attempt_resources']['gpu_peak_MiB'][i] for x in selected) for i in range(8)],
                            peak_cgroup_bytes=max(x['attempt_resources']['memory_current_peak_bytes'] for x in selected))
    closure = read(ROOT/'evidence/cost-sources/rawexport-container-closure.json')
    events = read(ROOT/'evidence/cost-sources/rawexport-event-collection.json')
    ranks, ends = [], []
    for f in events['files']:
        raw = f['raw_utf8'].encode()
        assert hashlib.sha256(raw).hexdigest() == f['sha256'] and len(raw) == f['bytes']
        rows = [json.loads(line) for line in raw.splitlines()]
        assert all(x['container_hostname'] == closure['container_id'][:12] for x in rows)
        identities = {x['rank'] for x in rows if 'rank' in x}
        assert len(identities) == 1
        ranks.append(next(iter(identities)))
        ends += [x['monotonic_ns'] for x in rows if x['name'] == 'compile_graph_and_warmup_end']
    assert sorted(ranks) == list(range(8)) and len(ends) == 8
    launch = events['metadata'][0]['selected_fields']
    docker_elapsed = (datetime.datetime.fromisoformat(closure['state']['FinishedAt'])-
                      datetime.datetime.fromisoformat(closure['state']['StartedAt'])).total_seconds()
    assert abs(docker_elapsed-closure['elapsed_container_started_to_finished_s']) < 0.000001
    conversion = dict(container_id=closure['container_id'],
                      container_started_to_finished_s=closure['elapsed_container_started_to_finished_s'],
                      dispatch_to_last_rank_warmup_end_s=span(launch['dispatch_monotonic_ns'], max(ends)),
                      pure_save_s=None,
                      scope='One historical conversion container; whole interval includes load/export/postprocess/warmup/exit. Not pure conversion I/O or a new trial.')
    packet = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313'
    index = read(packet/'formal-exact-index.json')
    assert sources[(packet/'formal-exact-index.json').relative_to(ROOT).as_posix()]['sha256'] == 'd73b0cd9a01fc320d05054417b5c553553981cd18fe4a768565a03b5db0bf3b0'
    supervisors = {}
    for role, basename in [('worker', 'formal13-supervisor-result.json'), ('head', 'headformal13-supervisor-result.json')]:
        entry, = [f for f in index['files'] if f['path'].endswith('/'+basename)]
        path = packet/entry['local_path']
        x = read(path)
        assert sources[path.relative_to(ROOT).as_posix()]['sha256'] == entry['sha256']
        supervisors[role] = dict(begin_ns=x['begin_ns'], end_ns=x['end_ns'],
                                 elapsed_s=span(x['begin_ns'], x['end_ns']), boot_id=x['boot_id'])
    assert supervisors['worker']['boot_id'] == supervisors['head']['boot_id']
    result = dict(schema='glm-cpu-historical-supplement-v1', sources=sources,
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  original40=dict(success=35, failed=5, rerun=0), summaries=summaries, trials=trials,
                  monitor_whole=monitor['whole'], conversion=conversion, batch_supervisors=supervisors,
                  limits=['recorded_attempt is preparation_begin_ns to complete_ns, including deletion and >=60s observation; not full supervisor/process/controller lifetime.',
                          'Device peaks are non-simultaneous NVML samples; memory.current includes file cache, not summed process RSS. Do not sum per-card maxima as a simultaneous peak.',
                          'Historical conversion is already measured at container scope; pure save/independent pool fill remains missing.',
                          'No independent client fixed32 begin/end or same-definition f7 envelope established; no N>1 experiment.'])
    (outdir/'historical-supplement.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    lines = ['# GLM历史监控与日志：CPU补充复算', '',
             '本次无GPU查询、模型启动、Store池或权重读取；仅低优先级流式读取历史监控。原40项35成功/5失败不变，历史失败与C23主配对不替换。', '',
             '## 可补齐的已有证据', '',
             '- 原监控1070633372字节留在qjhs003，没有下载到本机。精确路径、整文件SHA、执行函数SHA及52个窗口见historical-monitor.json。',
             '- 9647/9647条有八卡显存值，缺失0、外来GPU进程标记0；最大相邻采样间隔3.689146886秒，与当时审计逐项一致。',
             '- 全批每卡NVML采样高水位99293 MiB；自有slice的memory.current采样峰1553848774656字节（1447.134 GiB），含文件缓存，不是进程RSS或连续精确峰。逐卡峰非同时值，不相加成整机同时峰。', '',
             '| 条件 | C23描述n | 准备开始→记录完成均值/s | 完整B5–B10 n=6同区间/s | 该尝试区间内逐卡峰范围/MiB | 自有cgroup峰/GiB |',
             '|---|---:|---:|---:|---:|---:|']
    for c, x in summaries.items():
        lines.append(f"| {c} | {x['descriptive_n']} | {x['recorded_attempt_mean_s']:.6f} | {x['complete_blocks_recorded_attempt_mean_s']:.6f} | {min(x['peak_gpu_MiB'])}–{max(x['peak_gpu_MiB'])} | {x['peak_cgroup_bytes']/2**30:.3f} |")
    lines += ['', '以上区间严格为complete_ns−preparation_begin_ns，包含缓存准备、三道请求、删除及至少60秒观察，排除之前公共环境/建池，也不保证包含complete_ns之后的持久化、退出或父进程清理。**不能直接填成与Kimi/V4同定义的完整controller elapsed**；原三模型图该格继续缺测，可另加本表。GPU/cgroup按该尝试窗口选样；另有startup_A窗口原始摘要供核查。', '',
              f"本批共享worker supervisor区间为{supervisors['worker']['elapsed_s']:.6f}秒，head supervisor为{supervisors['head']['elapsed_s']:.6f}秒，起止与同boot身份已核验。它们跨越本批26项且相互重叠，不按条件均摊、不相加，也不冒充逐项controller成本。", '',
              '## 转换成本：已有观测而非全部未知', '',
              f"历史rawexport容器StartedAt→FinishedAt：**{conversion['container_started_to_finished_s']:.9f}秒**；派发→八rank最后预热结束：**{conversion['dispatch_to_last_rank_warmup_end_s']:.9f}秒**。两者重叠，不能相加。",
              '八rank事件嵌入字节全部匹配原SHA，容器身份与Docker退出记录相符，Docker UTC区间独立复算容差1微秒。覆盖加载、导出、后处理、预热与退出；不是纯save、纯I/O或GPU内存积分。旧容器成功不追认任何历史失败。', '',
              '## 确实仍需新增测量的项目', '',
              '| 项目 | 旧证据限制 | 如要补齐 |', '|---|---|---|',
              '| fixed32独立客户端HTTP耗时 | 原选定记录没有独立begin/end；后端事件不能冒充客户端耗时 | 新受审小批请求计时，独立新ID，不替换旧40项 |',
              '| 与Kimi完全同定义的f7包络 | GLM已有八rankphase，但起止函数定义不同 | 如必须同口径，重新布点采样；否则保留GLM既有phase独立展示 |',
              '| 纯Store保存/纯填池及资源积分 | 505.467秒是整容器，不能剥离缺失分量 | 专门边界采样；优先复用现有资产，不为补表重复复制Store |',
              '| N>1持续驻留摊销 | 原试验逐条件复位，无连续复用轨迹 | 新协议、预算、守护和审阅，不能从混杂Prep推回本次数 |',
              '| 与其他模型相同的完整controller边界 | 本次仅补回helper记录区间 | 若统一定义所需端点未记录，未来新批加入；不得改名填旧空格 |', '',
              '新增实验另行授权与准入，本次没有执行。缺同口径不等于历史完全没做。', '',
              '## 复算与身份', '', '```text', 'python -m unittest discover -s glm/tools -p test_final_costs.py', 'python glm/tools/historical_costs.py --c23-supplement', '```', '',
              '历史流式采集只调用final_costs.historical_monitor；historical-monitor.json绑定函数/源文件与监控SHA。每条结果按冻结ledger的result_sha256核验。supplement JSON列出全部26项原值和输入SHA；代码不覆写原成本报告。']
    (outdir/'historical-supplement.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(dict(trials=26, monitor_samples=monitor['whole']['samples'], conversion=conversion)))


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--c23-supplement']:
        c23_supplement()
    else:
        main()
