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


def loading_rank_pair(events, rank, begin_ns, end_ns):
    """Keep missing/ambiguous endpoints null; never borrow the actor's identity."""
    names = ['weights_pipeline_including_allocation_begin',
             'weights_pipeline_including_allocation_end']
    selected = [[e for e in events if e.get('rank') == rank and e['name'] == name
                 and begin_ns <= e['monotonic_ns'] <= end_ns] for name in names]
    if [len(x) for x in selected] != [1, 1]:
        return None, 'missing_or_duplicate_endpoint'
    first, last = [x[0] for x in selected]
    if (first['pid'], first['container_hostname']) != (last['pid'], last['container_hostname']):
        return None, 'process_identity_differs'
    if last['monotonic_ns'] < first['monotonic_ns']:
        return None, 'endpoint_order_invalid'
    return (first, last), None


def c23_loading_boundary():
    import base64
    import csv
    import statistics
    from final_statistics import PACKET, LEDGER_SHA

    r = ROOT/'results/reanalysis-20261106'
    used = {}

    def read(path, expected=None):
        b = path.read_bytes()
        h = hashlib.sha256(b).hexdigest()
        assert expected is None or h == expected, str(path)
        used[path.relative_to(ROOT).as_posix()] = dict(bytes=len(b), sha256=h)
        return b

    read(Path(__file__))
    ledger = json.loads(read(r/'inputs/formal-ledger.json', LEDGER_SHA))['rows']
    index = json.loads(read(PACKET/'formal-exact-index.json', 'd73b0cd9a01fc320d05054417b5c553553981cd18fe4a768565a03b5db0bf3b0'))
    files = {f['path']: f for f in index['files']}
    absolute_root = '/data/brick1/boyjiang/glm5.1-fp8/'
    prefix = 'validation/controls/'+PACKET.name+'/'

    def exact(path):
        f = files[path]
        return read(PACKET/f['local_path'], f['sha256']), f

    identity_bytes, _ = exact(prefix+'formal13-identity.json')
    identity = json.loads(identity_bytes)
    manifest = json.loads(read(r/'inputs/C23-run-manifest.json'))
    sources = json.loads(read(r/'loading-boundary-sources.json'))
    control_bytes, _ = exact('code/experiment/control-input-manifest.json')
    control = json.loads(control_bytes)
    history_bytes = read(r/'inputs/historical-review-manifest-v11.json', control['external_source_reference']['sha256'])
    historical_sources = {x['path']: x for x in json.loads(history_bytes)['files']}
    assert sources['history_manifest_sha256'] == hashlib.sha256(history_bytes).hexdigest()
    for f in sources['files']:
        assert hashlib.sha256(base64.b64decode(f['base64'])).hexdigest() == f['sha256'] == historical_sources[f['historical_path']]['sha256']
    archive = json.loads(read(r/'inputs/C23-executed-source-archive.json'))
    archived_sources = {x['path']: x for x in archive['files_checked_before_and_after']}
    for f in sources['executed_mount_sources']:
        assert hashlib.sha256(base64.b64decode(f['base64'])).hexdigest() == f['sha256'] == archived_sources[f['path']]['sha256']
    endpoints, attempts = [], []
    for state in ledger:
        if state.get('protocol_revision') != 'v13-canonical23-qualified-disk-jit-seed' or not 5 <= state['block'] <= 10:
            continue
        result = json.loads(read(r/'inputs'/state['attempt_id']/'result.json', state['result_sha256']))
        assert result['execution_run_id'] == PACKET.name
        assert result['code_commit'] == state['code_commit'] == 'f176e05607d04999ab28e8ea8cd49b41270cb528'
        assert result['source_manifest_sha256'] == state['source_manifest_sha256'] == '3eac8c63cdd670a49d872922c11b821d07a0e252f2fbe80f790e169749091fb9'
        phase_path = prefix+'formal-trials/'+state['attempt_id']+'/phases.json'
        phase_bytes, phase_source = exact(phase_path)
        phases = json.loads(phase_bytes)
        events = phases['raw_events']
        assert hashlib.sha256(json.dumps(events, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == phases['raw_event_sha256']
        actor = phases['control_events']['backend_engine_create_begin']
        ready = phases['control_events']['backend_engine_ready']['monotonic_ns']
        assert actor['boot_id'] == state['boot_id'] == manifest['runtime_identity']['boot_id']
        observed = []
        for rank in range(8):
            pair, error = loading_rank_pair(events, rank, result['startup_command_ns'], result['correct_business_response_ns'])
            row = dict(attempt_id=state['attempt_id'], block=state['block'], condition=state['condition'], rank=rank,
                       pid=None, process_start_ticks=None, rank_boot_id=None, rank_clock_offset_ns=None,
                       container_id=identity['container_id'], actor_pid=actor['pid'], actor_start_ticks=actor['process_start_ticks'],
                       backend_engine_create_begin_ns=actor['monotonic_ns'], backend_engine_ready_ns=ready,
                       contextual_boot_id=actor['boot_id'], clock='time.monotonic_ns; fixed emitter; boot binding is actor/container context, not a per-rank stamp',
                       clock_source_sha256='e1ab869bc51cbc42f46430f2296d1a9e4a48fc45aec57cf7908d88ae565c6c6c',
                       begin_ns=None, end_ns=None, source_path=None, source_sha256=None, begin_line=None, end_line=None,
                       device_setup_end_ns=None, quant_postprocess_begin_ns=None, quant_postprocess_end_ns=None,
                       kv_begin_ns=None, recorded_warmup_begin_ns=None,
                       phases_path=absolute_root+phase_path, phases_sha256=phase_source['sha256'], endpoint_error=error,
                       rank_identity_verified=False, source_pipeline_begin_count=None, source_pipeline_end_count=None)
            if pair is not None:
                first, last = pair
                event_path = prefix+f"events-formal13/process-{first['container_hostname']}-{first['pid']}.jsonl"
                data, f = exact(event_path)
                raw = [json.loads(line) for line in data.splitlines()]
                assert raw.count(first) == raw.count(last) == 1
                assert first['container_hostname'] == identity['container_id'][:12]
                assert actor['monotonic_ns'] <= first['monotonic_ns'] <= last['monotonic_ns'] <= ready
                pid_events = [e for e in events if e['pid'] == first['pid'] and e['container_hostname'] == first['container_hostname']]
                before = [e for e in pid_events if e['name'] == 'device_setup_including_communication_end']
                after = [e for e in pid_events if e['name'] == 'kv_cache_allocation_begin']
                qbegin = [e for e in pid_events if e['name'] == 'quantization_postprocess_begin']
                qend = [e for e in pid_events if e['name'] == 'quantization_postprocess_end']
                warmup = [e for e in pid_events if e['name'] == 'compile_graph_and_warmup_begin']
                assert [len(x) for x in (before, after, qbegin, qend, warmup)] == [1]*5
                assert before[0]['monotonic_ns'] <= first['monotonic_ns'] <= qbegin[0]['monotonic_ns'] <= qend[0]['monotonic_ns'] <= last['monotonic_ns'] <= after[0]['monotonic_ns']
                assert last['monotonic_ns'] <= warmup[0]['monotonic_ns']
                stamps = {e['process_start_ticks'] for e in raw if e.get('pid') == first['pid'] and 'process_start_ticks' in e}
                boots = {e['boot_id'] for e in raw if e.get('pid') == first['pid'] and 'boot_id' in e}
                assert len(stamps) <= 1 and len(boots) <= 1
                row.update(pid=first['pid'], process_start_ticks=next(iter(stamps), None), rank_boot_id=next(iter(boots), None),
                           begin_ns=first['monotonic_ns'], end_ns=last['monotonic_ns'],
                           source_path=absolute_root+event_path, source_sha256=f['sha256'],
                           begin_line=raw.index(first)+1, end_line=raw.index(last)+1,
                           device_setup_end_ns=before[0]['monotonic_ns'], quant_postprocess_begin_ns=qbegin[0]['monotonic_ns'],
                           quant_postprocess_end_ns=qend[0]['monotonic_ns'], kv_begin_ns=after[0]['monotonic_ns'],
                           recorded_warmup_begin_ns=warmup[0]['monotonic_ns'],
                           rank_identity_verified=bool(stamps and boots),
                           source_pipeline_begin_count=sum(e['name'] == 'weights_pipeline_including_allocation_begin' for e in raw),
                           source_pipeline_end_count=sum(e['name'] == 'weights_pipeline_including_allocation_end' for e in raw))
                observed.append(row)
            endpoints.append(row)
        distinct_pids = len({x['pid'] for x in observed})
        span_value = span(min(x['begin_ns'] for x in observed), max(x['end_ns'] for x in observed)) if len(observed) == distinct_pids == 8 else None
        if span_value is not None:
            assert abs(span_value-result['metrics']['phase_weights_pipeline_including_allocation_span_s']) < 1e-9
        attempts.append(dict(attempt_id=state['attempt_id'], block=state['block'], condition=state['condition'],
                             valid_endpoint_ranks=len(observed), verified_rank_identities=sum(x['rank_identity_verified'] for x in observed),
                             distinct_rank_pids=distinct_pids, contextual_single_backend_window=True,
                             weight_load_host_all_rank_span_s=span_value,
                             max_rank_host_duration_s=max(span(x['begin_ns'], x['end_ns']) for x in observed) if len(observed) == 8 else None,
                             strict_unified_eligible=False, strict_unified_value_s=None,
                             reason='C: rank startticks/direct clock identity and actual GPUModelRunner body binding incomplete; outer worker RPC cannot be promoted by field name.',
                             result_sha256=state['result_sha256'], phases_path=absolute_root+phase_path, phases_sha256=phase_source['sha256']))
    assert len(attempts) == 24 and len(endpoints) == 192
    expected = dict(zip(('safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit'),
                        (393.624687, 131.216366, 123.572061, 7.889165)))
    summary = {}
    for c, target in expected.items():
        values = [x['weight_load_host_all_rank_span_s'] for x in attempts if x['condition'] == c and x['weight_load_host_all_rank_span_s'] is not None]
        mean = statistics.mean(values) if values else None
        if len(values) == 6:
            assert abs(mean-target) < 0.0000005
        summary[c] = dict(requested_n=6, endpoint_valid_n=len(values), observed_mean_s=mean,
                          strict_unified_valid_n=0, strict_unified_mean_s=None)
    for name, rows in [('loading-boundary-endpoints.csv', endpoints), ('loading-boundary-attempts.csv', attempts)]:
        with (r/name).open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator='\n')
            writer.writeheader()
            writer.writerows({k: 'null' if v is None else v for k, v in row.items()} for row in rows)
    result = dict(verdict='C', scope='Strict common-boundary admission not proven; observed timestamp envelopes remain valid historical arithmetic, not zero or replacement trials.',
                  runtime=dict(code_commit='f176e05607d04999ab28e8ea8cd49b41270cb528',
                               vllm_base=manifest['selected_vllm_commit'], sllm_base=manifest['selected_sllm_commit'], image_id=manifest['image_id']),
                  source_code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), summary=summary,
                  missing_rank_startticks=sum(x['process_start_ticks'] is None for x in endpoints),
                  missing_direct_rank_boot_ids=sum(x['rank_boot_id'] is None for x in endpoints),
                  model_binding='One backend create/ready window, exact private container, one PID-pair per rank. No actor startticks substitution or invented rank model UUID.',
                  sources_snapshot_sha256=hashlib.sha256((r/'loading-boundary-sources.json').read_bytes()).hexdigest(),
                  input_files=used, attempts=attempts)
    (r/'loading-boundary-summary.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    note = ['# 图5完整加载总跨度：历史边界核验', '',
            '**结论C：严格共同边界与逐rank进程身份链尚未闭合，不能判A进入统一主图。** 24项的TP8时间端点可重算，不等于24项已通过统一功能边界。', '',
            '## 实际源码与范围', '',
            '运行代码f176e05607d04999ab28e8ea8cd49b41270cb528；vLLM基线43a9b1afbb7ed88e63ebeefeb55df8d445aa1992。worker替换文件由C23冻结launch挂载到镜像vLLM路径；本次取executed-source-f176e05，不用已不同的当前launch文件。来源字节、挂载源码及SHA见loading-boundary-sources.json。', '',
            '| 源码 | 位置（1起） | SHA256 |', '|---|---|---|',
            '| gpu_worker.py | 320 decorator；321–326 load_model | 86f5606fa76ab71b78699e22b70319cc9c85ead31761dba2e0c1187e984c8182 |',
            '| event_trace.py | 55发begin；57调用函数；63发end；11–43写时间/进程 | e1ab869bc51cbc42f46430f2296d1a9e4a48fc45aec57cf7908d88ae565c6c6c |',
            '| base_loader.py | 44–94 load_model，57构造、91后处理 | d26f6561e4a3480591f63d1b653b0efd170ae09c03ee18397996289e35285ab7 |',
            '| Store vllm_loader.py | 127–168 load_model，144构造、166后处理 | 36a40e20f8d2d6b2e7b3346ec5e1c54d5598359b1b1756348ef97723e34fa502 |', '',
            'begin由traced包装器在调用GPUWorker.load_model前发出；end在该函数成功返回后发出，异常路径发_failed而非_end。范围包含weights内存池/config上下文及model_runner.load_model，begin记录写文件本身也处于该包络内；它不是只围住load_weights。end证明host调用返回，不等于独立记录了所有异步CUDA工作完成。',
            'SF的Base loader与Store loader源码均在其内部创建模型并执行加载及必要后处理；Store还有骨架storage释放/恢复工作。192个实际rank区间均验证device_setup end≤pipeline begin≤quantization begin≤quantization end≤pipeline end≤KV begin，因此这些已记录的设备/NCCL初始化与KV阶段在外，后处理在内。不能把通信buffer等所有通信相关工作也据此称为在外。',
            'runner对象本身在gpu_worker.py 297–312的init_device内先行构造，pipeline从其后才开始。Store的fresh骨架分配在本段内有直接事件；SF有Base loader构造源码。要严格排除实际runner构造时更早的权重storage分配，还缺实际runner构造函数版本/映射证明，不能仅凭pipeline字段名宣告fresh边界已闭合。',
            'GPUModelRunner实际worker映射文件SHA及V1/V2实际分支缺少历史证明。固定43a的V1官方源码只作参考：4730–4887含内存记账、可选LoRA/drafter/aux、4826–4831模型专属通信buffer、4856–4885可选compile/graph包装、4887 offloader收尾。MTP配置null不能代替所有这些条件分支的逐worker证据。192个已记录compile/warmup begin均在pipeline end后；Worker的内存profile是另一入口，不能据此把runner内所有条件编译/初始化也说成完全没有额外工作。', '',
            '## 192行逐rank证据与缺口', '',
            'loading-boundary-endpoints.csv逐行列attempt/rank/PID、startticks、begin/end、时钟上下文、原始进程文件路径与SHA、端点行号及phases来源。每个非空配对均在同一PID/容器、唯一源文件记录及同一个backend create→ready窗口内；每项8个不同PID、rank0–7。逐rank声明与上下文证明分开。',
            f"在本轮读取的事件与小型身份归档中：rank startticks缺失{result['missing_rank_startticks']}/192，rank直接boot标记缺失{result['missing_direct_rank_boot_ids']}/192。actor的PID/startticks/boot另列，绝不填给GPU worker。没有重新扫描大监控来补这些值，也不以请求时间窗推造startticks；不声称未读取的大监控中同样不存在身份信息。CLOCK_MONOTONIC来源由固定event_trace.py确定，boot绑定来自actor/容器，不声称每rank独立时钟身份已取证。", '',
            '## 逐attempt公式与原6项均值', '',
            '`weight_load_host_all_rank_span_s = (max(rank_end_ns)-min(rank_begin_ns))/1e9`；另列max_rank_host_duration_s，不替代此值。缺失端点返回null。下面是完整端点包络的复算，不是严格共同边界已验收的均值。', '',
            '| 条件 | 端点有效n/原n | 包络均值/s | 严格统一有效n | 严格统一均值 |', '|---|---:|---:|---:|---|']
    for c, x in summary.items():
        mean_text = f"{x['observed_mean_s']:.9f}" if x['observed_mean_s'] is not None else 'null'
        note.append(f"| {c} | {x['endpoint_valid_n']}/6 | {mean_text} | 0 | null |")
    note += ['', '24行逐attempt数值在loading-boundary-attempts.csv。四个均值复核匹配393.624687 / 131.216366 / 123.572061 / 7.889165秒；原ID、B5–B10与四条件未变。', '',
             '本轮不重画图5：当前证据只能保留观测包络，不能将其升级成统一主比较。ingestion/postprocess只作专项说明，不与V4同名阶段直接横比；其专家copy所在遍历与GLM工作范围未证相同。图4/helper及资源补充不修改。', '',
             '复算：`python glm/tools/historical_costs.py --c23-loading-boundary`。来源SHA清单为loading-boundary-inputs.json；无监控重扫、无模型/容器/Store运行。官方参考：[固定43a GPUModelRunner](https://raw.githubusercontent.com/vllm-project/vllm/43a9b1afbb7ed88e63ebeefeb55df8d445aa1992/vllm/v1/worker/gpu_model_runner.py)，不是实际映射文件证明。']
    (r/'loading-boundary-note.md').write_text('\n'.join(note)+'\n', encoding='utf-8')
    outputs = {name:dict(bytes=(r/name).stat().st_size, sha256=hashlib.sha256((r/name).read_bytes()).hexdigest())
               for name in ['loading-boundary-endpoints.csv', 'loading-boundary-attempts.csv', 'loading-boundary-summary.json', 'loading-boundary-sources.json', 'loading-boundary-note.md']}
    (r/'loading-boundary-inputs.json').write_text(json.dumps(dict(inputs=used, outputs=outputs), indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(verdict='C', ranks=len(endpoints), attempts=len(attempts), summary=summary)))


def controller_runtime_reference():
    """Recheck small frozen source assets on qjhs003, never weights or an environment copy."""
    import subprocess
    import urllib.request
    base = ROOT/'results/reanalysis-20261106'
    pinned = {'inputs/C23-run-manifest.json':'bda7616e8310ab08178fc52bf2c2e51cf4d51544915de1b7f199a287bf8cf87d',
              'inputs/historical-review-manifest-v11.json':'da681f8d83ee3416faeffad30f230365e9956c8c6763aa30487d4bb809be81b4',
              'inputs/canonical-INDEX.json':'037b347e22c589b06883a20ca7ccb8805101324f7bdd7001e28497bf837d9978',
              'deployment-binding.json':'a99e4dd85a58101fda3d2dc906f45321177b9f7d31a3f57910379b18698d313d'}
    documents = {}
    for name, expected in pinned.items():
        raw = (base/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == expected
        documents[name] = json.loads(raw)
    config, history, index, binding = [documents[name] for name in pinned]
    canonical = '/data/brick1/boyjiang/glm5.1-fp8'
    old = index['old_path_compatibility_only'].rstrip('/')+'/'

    def mapped(relative):
        for entry in sorted(index['entries'], key=lambda r:len(r['historical_path']), reverse=True):
            prefix = entry['historical_path'].removeprefix(old)
            if relative == prefix or relative.startswith(prefix+'/'):
                return entry['actual_path']+relative[len(prefix):]
        return canonical+'/archive/original-run/'+relative

    selected = [row for row in history['files'] if row['path'].startswith('source-v7/sllm_store/sllm_store/')
                or row['path'].startswith('source/ServerlessLLM/sllm/')]
    assert len(selected) == 23
    files = [dict(path=mapped(row['path']), bytes=row['bytes'], sha256=row['sha256']) for row in selected]
    for row in binding['files']:
        if row['repository'] == 'vllm':
            files.append(dict(path=row['canonical_path'], sha256=row['history_sha256']))
    for name in ('native-gold.json', 'store-restore-validation.json', 'native-weight-contract.json'):
        files.append(dict(path=canonical+'/validation/'+name, sha256=config['reference_sha256'][name]))
    reference = dict(historical_inputs_sha256=pinned, asset_files=files,
                     reference_sha256=config['reference_sha256'], store_import_sha256=config['store_import_sha256'],
                     event_trace_sha256=config['event_trace_sha256'], runtime_versions=binding['runtime_versions'],
                     selected_sllm_commit=config['selected_sllm_commit'], immutable_image_id=binding['immutable_image_id'])
    imports_path = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313/formal-exact/4876764a585b--runtime-imports.json'
    raw_imports = imports_path.read_bytes()
    assert hashlib.sha256(raw_imports).hexdigest() == '215311152be60ce334526bdfb13db46c9343f7b39f1f652cb3899c4a840ed455'
    reference['backend_reference'] = json.loads(raw_imports)['backend_reference']
    reference['backend_reference_source_sha256'] = hashlib.sha256(raw_imports).hexdigest()
    files.append(dict(path=canonical+'/store/tp8/raw-disk-verification.json', sha256=config['reference_sha256']['raw-disk-verification.json']))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def github_metadata(url):
        request = urllib.request.Request(url, headers={'User-Agent':'GLM-CPU-reference-audit'})
        with opener.open(request, timeout=25) as response:
            raw = response.read(2*1024**2+1)
        if len(raw) > 2*1024**2:
            raise RuntimeError('official metadata exceeds2MiB')
        return json.loads(raw), dict(url=url, sha256=hashlib.sha256(raw).hexdigest())

    api = 'https://api.github.com/repos/ServerlessLLM/ServerlessLLM'
    commit, commit_source = github_metadata(api+'/commits/'+config['selected_sllm_commit'])
    assert commit['sha'] == config['selected_sllm_commit']
    tree, tree_source = github_metadata(api+'/git/trees/'+commit['commit']['tree']['sha']+'?recursive=1')
    assert tree['sha'] == commit['commit']['tree']['sha'] and not tree.get('truncated')
    reference['official_sllm_tree'] = dict(commit=config['selected_sllm_commit'], tree=tree['sha'],
        sources=[commit_source, tree_source], files=[r for r in tree['tree'] if r['type']=='blob' and r['path'].startswith('sllm/')])
    remote = '''import pathlib,json,hashlib,subprocess
reference=json.loads(PAYLOAD)
for row in reference['asset_files']:
 p=pathlib.Path(row['path']); raw=p.read_bytes()
 assert hashlib.sha256(raw).hexdigest()==row['sha256'], str(p)
 assert 'bytes' not in row or row['bytes']==len(raw), str(p)
 row['bytes']=len(raw)
repo=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/code/ServerlessLLM')
changed=[]
newline_only=[]
tracked=[]
for entry in reference['official_sllm_tree']['files']:
 assert entry['mode'] in ('100644','100755'), entry['path']
 name=entry['path'];tracked.append(name);raw=(repo/name).read_bytes()
 blob=hashlib.sha1(b'blob '+str(len(raw)).encode()+bytes([0])+raw).hexdigest()
 if blob!=entry['sha']:
  normalized=raw.replace(bytes([13,10]),bytes([10]))
  normalized_blob=hashlib.sha1(b'blob '+str(len(normalized)).encode()+bytes([0])+normalized).hexdigest()
  if normalized_blob==entry['sha']:
   newline_only.append(dict(path=name,raw_bytes=len(raw),comparison_bytes=len(normalized),crlf_count=raw.count(bytes([13,10])),raw_sha256=hashlib.sha256(raw).hexdigest(),official_git_blob=entry['sha']))
  else:changed.append(name)
assert set(changed)=={'sllm/controller.py','sllm/backends/vllm_backend.py','sllm/routers/roundrobin_router.py'}, changed
code_suffixes={'.py','.so','.json','.yaml','.yml','.toml','.jinja','.jinja2'}
actual={p.relative_to(repo).as_posix() for p in (repo/'sllm').rglob('*') if p.is_file() and p.suffix in code_suffixes}
expected={name for name in tracked if pathlib.Path(name).suffix in code_suffixes}
assert actual==expected, 'undeclared SLLM code/config files'
existing={row['path'] for row in reference['asset_files']}
for name in tracked:
 p=repo/name
 if p.is_file() and str(p) not in existing:
  raw=p.read_bytes();reference['asset_files'].append(dict(path=str(p),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
reference['sllm_official_blob_proof']={'base_commit':reference['selected_sllm_commit'],'changed_paths':changed,'unexpected_code_files':[],'verified_official_files':len(tracked),'newline_only_deployment_variants':newline_only,'newline_policy':'read-only comparison only; server bytes not rewritten; actual raw SHA256 remains pinned'}
reference['code_closure']=[{'root':str(root),'paths':sorted(p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.suffix in code_suffixes)} for root in [repo/'sllm',pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/code/Store-source-v7/sllm_store')]]
reference['scope']='small actual code/native/reference assets; no weight payload, no model or GPU launch; not mapped-library proof for every rank'
print(json.dumps(reference))
'''.replace('PAYLOAD', repr(json.dumps(reference)))
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','qjhs003','python3','-'],
                            input=remote.encode(), capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError('historical runtime reference check failed: '+result.stderr.decode(errors='replace'))
    output = json.loads(result.stdout)
    path = ROOT/'evidence/cost-sources/controller-runtime-reference-verification.json'
    path.write_text(json.dumps(output, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(verified_files=len(output['asset_files']),
                         bytes=sum(row['bytes'] for row in output['asset_files']), gpu_execution=False)))


def finalize_controller(run, *, verify_only=False):
    """Finalize a new sample from kernel unit exit, never an inner self-reported end."""
    import re, subprocess
    if not re.fullmatch(r'sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}', run):
        raise ValueError('new supplementary run identity required')
    script = '''import pathlib,json,hashlib,subprocess,math,sys
run=RUN
verify_only=VERIFY_ONLY
root=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/debug')/run
names=('Id','Result','ExecMainCode','ExecMainStatus','MainPID','InvocationID','ExecMainStartTimestampMonotonic','ExecMainExitTimestampMonotonic')
raw=subprocess.check_output(['systemctl','show',run+'-controller.service','--property='+','.join(names)],text=True,timeout=15)
unit=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
if unit.get('MainPID') not in ('0',''):raise RuntimeError('controller still active; no finalization during measurement')
inputs={}
def read(name):
 p=root/name
 if not p.exists():inputs[name]={'path':str(p),'missing':True};return {}
 data=p.read_bytes();inputs[name]={'path':str(p),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()};return json.loads(data)
start=read('controller-start.json')
result=read('controller-result.json')
stack=read('stack.json')
out={'schema':'glm-independent-controller-supplement-v1','attempt_id':run,'condition':start.get('condition'),'unit':unit,'success':False,'metrics':None,'inputs':inputs,'original40_modified':False}
try:
 assert unit['Id']==run+'-controller.service' and unit['Result']=='success' and unit['ExecMainCode']=='1' and unit['ExecMainStatus']=='0'
 assert unit['InvocationID']==start['independent_controller_unit']['InvocationID']
 begin=int(unit['ExecMainStartTimestampMonotonic'])*1000;end=int(unit['ExecMainExitTimestampMonotonic'])*1000
 assert begin==start['controller_begin_ns'] and begin<end
 assert result['success'] is True and result['run_id']==stack['run_id']==start['run_id']==run
 worker=read('debug-service/result.json')
 assert worker['success'] is True and worker['run_id']==run and worker['condition']==start['condition']
 assert worker['holder']==start['holder']==stack['holder']==result['holder']
 assert worker['boot_id']==start['boot_id']==pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 prep,prepared,command,trigger,response=(worker[k] for k in ('preparation_begin_ns','preparation_complete_ns','startup_command_ns','business_trigger_ns','correct_business_response_ns'))
 deleted,removed=worker['delete_complete_ns'],result['all_owned_containers_removed_ns']
 assert begin<prep<prepared<=command<trigger<response<deleted<removed<end
 assert result['release_observation_s']>=60 and worker['idle_observation_seconds']>=60
 assert end>=result['controller_end_ns'] and (end-begin)/1e9<=3600
 for name,reported,earliest,latest in [
     ('release60.jsonl',result['release_observation_s'],removed,result['controller_end_ns']),
     ('debug-service/idle60.jsonl',worker['idle_observation_seconds'],deleted,removed)]:
  path=root/name
  with path.open('rb') as f:data=f.read(16*1024**2+1)
  assert len(data)<=16*1024**2
  inputs[name]={'path':str(path),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
  samples=[json.loads(line) for line in data.splitlines() if line.strip()];points=[row['monotonic_ns'] for row in samples]
  assert len(points)>1 and all(0<b-a<=5*10**9 for a,b in zip(points,points[1:]))
  if name=='release60.jsonl':
   expected={device['uuid'] for device in start['resource_admission']['gpu_devices']}
   assert len(expected)==8
   previous_io=None
   for row in samples:
    assert row['run_id']==run and row['boot_id']==start['boot_id']
    devices=[list(map(str.strip,line.split(','))) for line in row['gpu'].splitlines()]
    assert len(devices)==8 and [device[0] for device in devices]==list(map(str,range(8)))
    assert all(len(device) in (3,5) for device in devices) and {device[1] for device in devices}==expected
    assert all(device[3 if len(device)==5 else 2]=='0 MiB' for device in devices)
    assert not row['gpu_processes'].strip() and all(item.get('absent') for item in row['containers'].values())
    events=dict(line.split() for line in row['private_slice_memory_events'].splitlines())
    assert {'oom','oom_kill'}<=events.keys() and all(int(v)==0 for k,v in events.items() if k.startswith('oom'))
    assert set(row['retained_cgroups'])=={'head','worker'}
    current_io={}
    for role,entry in row['retained_cgroups'].items():
     if entry['absent']:continue
     values=entry['values']
     memory=dict(line.split() for line in values['memory.stat'].splitlines())
     assert dict(line.split() for line in values['cgroup.events'].splitlines()).get('populated')=='0'
     assert not values['cgroup.procs'].strip() and all(memory.get(k)=='0' for k in ('anon','shmem','unevictable'))
     current_io[role]=values['io.stat']
    assert previous_io is None or all(previous_io.get(role)==v for role,v in current_io.items())
    previous_io=current_io
  else:
   source=root/'source'
   for filename,expected in (
       ('accept_control13.py','b064d25f90d2b30a48c298faa6f3af2b8d0dce4c8850589b411153b3f9f4197c'),
       ('gpu_identity13.py','8cefc37753a7bed59f493142129b4e87e87a4121e2f901cea1557fa87aa28047'),
       ('workspace.py','c81081d04de424f532661183bd517765e7210d8a6db2d0fb84d5c3137bdc586f')):
    path=source/filename
    payload=path.read_bytes()
    assert hashlib.sha256(payload).hexdigest()==expected
    inputs['source/'+filename]={'path':str(path),'bytes':len(payload),'sha256':expected}
   sys.dont_write_bytecode=True
   sys.path.insert(0,str(source))
   from accept_control13 import validate_native_idle
   baseline=read('debug-service/auxiliary-gpu-baseline.json')
   assert baseline['helper']==worker['helper_gpu_identity'] and baseline['memory_MiB']==worker['auxiliary_gpu_baseline_MiB']
   assert all(row['delete_ack_ns']==deleted for row in samples)
   validate_native_idle(samples,baseline['memory_MiB'],baseline['helper'],run,start['boot_id'])
  observed=(points[-1]-points[0])/1e9
  assert earliest<=points[0]<points[-1]<=latest and observed>=60 and abs(observed-reported)<1e-6
 gold_path=pathlib.Path('/data/brick1/boyjiang/glm5.1-fp8/validation/native-gold.json');gold_bytes=gold_path.read_bytes()
 assert hashlib.sha256(gold_bytes).hexdigest()=='9a2db6905b69eb6a43b9f68280234c5ba3b52416f116e16d885c00c9301e12c0'
 gold=json.loads(gold_bytes)
 for number in (1,2,0):
  case=gold['requests'][number];expected=gold['results'][number]
  choice=read('debug-service/'+case['name']+'-response.json')['choices'][0]
  assert choice['token_ids']==expected['token_ids'] and choice['message']['content']==expected['text'] and choice['finish_reason']==expected['finish_reason']
  assert len(choice['token_ids'])>0 and len(choice['logprobs'])==len(choice['token_ids'])
  assert all(math.isfinite(row[str(token)]['logprob']) for token,row in zip(choice['token_ids'],choice['logprobs'],strict=True))
 cache=read('debug-service/cache-admission/summary.json')
 assert cache['passed'] is True and cache['condition']==start['condition'] and cache['files']
 if start['condition']=='store_pool_hit':assert cache['pool_confirmed_ranks']==list(range(8))
 else:
  hot=start['condition']=='safetensors_disk_hot'
  assert all(row['pages']>0 and row['resident_pages']==(row['pages'] if hot else 0) for row in cache['files'])
 phases=read('debug-service/phases.json')
 requests=read('debug-service/measurement-progress.json')['requests']
 assert requests==worker['requests'] and [row['name'] for row in requests]==[gold['requests'][n]['name'] for n in (1,2,0)]
 previous=command
 for row in requests:
  assert previous<row['begin_ns']<row['response_complete_ns']<deleted and row['gold_match'] is True
  previous=row['response_complete_ns']
 assert (requests[0]['begin_ns'],requests[0]['response_complete_ns'])==(trigger,response)
 assert (phases['begin_ns'],phases['end_ns'],phases['request_id'])==(command,response,requests[0]['request_id'])
 published=phases['control_events']['router_instance_published']['monotonic_ns']
 assert command<=published<=phases['first_token_observed_ns']<=response and trigger<=phases['first_token_observed_ns']
 metrics={'preparation_s':(prepared-prep)/1e9,'command_to_correct_business_response_s':(response-command)/1e9,
          'trigger_to_correct_business_response_s':(response-trigger)/1e9}
 assert set(phases['phases'])=={'device_setup_including_communication','communication_groups','weights_pipeline_including_allocation',
                               'weight_ingestion','quantization_postprocess','kv_cache_allocation','compile_graph_and_warmup'}
 for name,phase in phases['phases'].items():
  ranks=phase['ranks']
  assert set(ranks)==set(map(str,range(8)))
  assert all(type(row['begin_ns']) is int and type(row['end_ns']) is int and command<=row['begin_ns']<=row['end_ns']<=response for row in ranks.values())
  span=(max(row['end_ns'] for row in ranks.values())-min(row['begin_ns'] for row in ranks.values()))/1e9
  assert span==phase['min_begin_to_max_end_seconds']
  metrics['phase_'+name+'_span_s']=span
 assert metrics==worker['metrics']
 metrics['controller_s']=(end-begin)/1e9
 out.update(success=True,metrics=metrics,controller_begin_ns=begin,controller_end_ns=end,clock='systemd CLOCK_MONOTONIC microseconds converted to ns;1us resolution',code_commit=stack['source']['code_commit'],protocol_revision='independent-controller-supplement-v1',sample_n=1,scope='same-run Controller/Prep/A/phases; not historical C23 blocks; phase envelopes not proven V4 equivalent')
except BaseException as error:
 out['failure']=repr(error)
out['unit_snapshot_sha256']=hashlib.sha256(raw.encode()).hexdigest()
out['finalizer_source_sha256']=ANALYSIS_SHA
p=root/'controller-final.json'
if verify_only:
 with p.open('rb') as f:stored=f.read(1024**2+1)
 if len(stored)>1024**2 or not out['success'] or json.loads(stored)!=out:
  raise RuntimeError('stored final receipt differs from independently recomputed successful result')
 print(json.dumps(dict(attempt_id=run,success=True,final_sha256=hashlib.sha256(stored).hexdigest())))
else:
 with p.open('x') as f:json.dump(out,f,indent=2)
 print(json.dumps(out))
'''.replace('RUN', repr(run), 1).replace('VERIFY_ONLY', repr(verify_only), 1).replace(
        'ANALYSIS_SHA', repr(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
    result = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10','qjhs003','python3','-I','-'],
                            input=script.encode(), capture_output=True, timeout=90, check=True)
    output = json.loads(result.stdout)
    if verify_only:
        print(json.dumps(output))
        return
    print(json.dumps(dict(attempt_id=run, success=output['success'], metrics=output['metrics'],
                          evidence='/data/brick1/boyjiang/glm5.1-fp8/debug/'+run+'/controller-final.json')))


def c23_controller_audit():
    """Check small archived parents and per-attempt records; never rescan telemetry."""
    from final_statistics import LEDGER_SHA
    directory = ROOT/'results/reanalysis-20261106'
    packet = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261027T011726Z-28c92313'
    sources = {}

    def read(path, expected=None):
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if expected is not None:
            assert sha == expected, str(path)
        sources[path.relative_to(ROOT).as_posix()] = dict(bytes=len(raw), sha256=sha)
        return json.loads(raw)

    ledger = read(directory/'inputs/formal-ledger.json', LEDGER_SHA)
    index = read(packet/'formal-exact-index.json', 'd73b0cd9a01fc320d05054417b5c553553981cd18fe4a768565a03b5db0bf3b0')

    def archived(name):
        entry, = [r for r in index['files'] if r['path'].endswith('/'+name)]
        return read(packet/entry['local_path'], entry['sha256'])

    parents = {role: archived(name+'-supervisor-result.json')
               for role, name in [('worker', 'formal13'), ('head', 'headformal13')]}
    for role, name in [('worker', 'formal13'), ('head', 'headformal13')]:
        dispatch = archived(name+'-dispatch.json')
        parent = parents[role]
        assert all(parent[k] == dispatch[k] for k in ('boot_id', 'run_id', 'code_commit', 'manifest_sha256'))
        assert dispatch['dispatch_monotonic_ns'] <= parent['begin_ns'] < parent['end_ns']
        assert parent['child_alive_at_end'] is False
    assert parents['head']['boot_id'] == parents['worker']['boot_id']
    release = archived('release60-allstopped.json')
    release_span = span(release[0]['monotonic_ns'], release[-1]['monotonic_ns'])
    assert len(release) == 61 and release_span >= 60
    assert max(p['end_ns'] for p in parents.values()) < release[0]['monotonic_ns']
    assert all(not r['own_running'] and not r['gpu_processes'] and not r['processes'] for r in release)
    trials = []
    for row in ledger['rows']:
        if row.get('protocol_revision') != 'v13-canonical23-qualified-disk-jit-seed':
            continue
        result = read(directory/'inputs'/row['attempt_id']/'result.json', row['result_sha256'])
        assert result['execution_run_id'] == parents['worker']['run_id']
        assert result['code_commit'] == parents['worker']['code_commit']
        begin, end = result['preparation_begin_ns'], result['complete_ns']
        assert all(p['begin_ns'] < begin < end < p['end_ns'] for p in parents.values())
        assert result['idle_observation_seconds'] >= 60
        trials.append(dict(attempt_id=row['attempt_id'], condition=result['condition'], block=result['block'],
                           preparation_begin_ns=begin, helper_complete_ns=end,
                           helper_elapsed_s=span(begin, end),
                           prep_s=result['metrics']['preparation_s'],
                           A_command_to_first_arithmetic_response_s=result['metrics']['command_to_correct_business_response_s'],
                           controller_begin_ns=None, controller_end_ns=None, controller_elapsed_s=None,
                           reason='Shared parent remains alive after this helper result; no per-attempt deployment exit.'))
    assert len(trials) == 26 and sum(5 <= r['block'] <= 10 for r in trials) == 24
    counts = {s: sum(r['status'] == s for r in ledger['rows']) for s in ('success', 'failed', 'not_started')}
    assert len(ledger['rows']) == 40 and counts['success'] == 35
    return dict(schema='glm-controller-historical-audit-v1', sources=sources,
                original40=counts, c23_attempts=trials, shared_parents=parents,
                shared_final_release_observation_s=release_span,
                controller_recoverable_by_condition=False,
                limitations=['Only one shared parent lifecycle exists for 26 trials; it cannot be divided into four condition lifecycles.',
                             'Head child_returncode=1 with controlled_stop is retained; supervisor success is not normal child exit0.',
                             'Helper complete_ns is not process exit, and per-instance idle60 is not whole-deployment release60.',
                             'Only SHA-bound small archive records were read; no new monitoring scan or GPU execution.'],
                next_step='Separate four-condition batch with same-run Controller/Prep/A/loading; no historical-block splice.')


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['--c23-supplement']:
        c23_supplement()
    elif sys.argv[1:] == ['--c23-loading-boundary']:
        c23_loading_boundary()
    elif sys.argv[1:] == ['--c23-controller-audit']:
        output = c23_controller_audit()
        output['analysis_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        (ROOT/'results/reanalysis-20261106/controller-history-audit.json').write_text(
            json.dumps(output, indent=2)+'\n', encoding='utf-8')
        print(json.dumps(dict(recoverable=False, c23_attempts=len(output['c23_attempts']),
                              original40=output['original40'])))
    elif sys.argv[1:] == ['--controller-runtime-reference']:
        controller_runtime_reference()
    elif len(sys.argv)==3 and sys.argv[1]=='--finalize-controller':
        finalize_controller(sys.argv[2])
    elif len(sys.argv)==3 and sys.argv[1]=='--verify-controller':
        finalize_controller(sys.argv[2], verify_only=True)
    else:
        main()
