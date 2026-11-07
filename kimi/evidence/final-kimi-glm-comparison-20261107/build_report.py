import pathlib,json,statistics,csv,hashlib,shutil,time
s=pathlib.Path('/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104')
o=s/'kimi-glm-comparison-20261107-v1'
k=json.loads((s/'kimi-four-condition-actual-analysis-v1-20261107.json').read_text())
g=json.loads((s/'kimi-glm-existing-plot-input-20261107.json').read_text())
gl=json.loads((s/'comparison-all40-glm-ledger.json').read_text())
C=('safetensors_disk_cold','safetensors_disk_hot','store_disk_cold','store_pool_hit')
labels=('SF 冷盘','SF 热缓存','Store 冷盘','Store 池命中')
P={c:[r for r in k['runs'] if r['cohort']=='original' and r['qualified'] and r['block'] in k['complete_block_ids'] and r['condition']==c] for c in C}
means=lambda rows,key:statistics.mean(r['timing'][key] for r in rows)
summary={'metric':'registration call begin to arithmetic HTTP parsed/context-closed received_ns before gold assertion','primary_complete_blocks':{'Kimi':k['complete_block_ids'],'GLM':list(range(5,11))},'models':{}}
for model in ('Kimi-K2.6','GLM-5.1-FP8'):
 summary['models'][model]={}
 for c in C:
  if model=='Kimi-K2.6':
   rows=P[c];vals=[r['timing']['A_s'] for r in rows]
   costs={x:means(rows,x) for x in ('Prep_s','Gap_s','Prep_plus_A_s','contiguous_s','D_fixed32_s')}
   full=[json.loads(pathlib.Path(r['refs']['controller']['path']).read_text())['elapsed'] for r in rows]
  else:
   q=g['primary_complete_blocks_5_to_10'][c];vals=q['A_s']
   costs={'Prep_s':statistics.mean(q['Prep_s']),'Gap_s':q['event_segment_means_s']['preparation_to_startup_gap_s'],'Prep_plus_A_s':statistics.mean(q['Prep_plus_A_s']),'D_fixed32_s':None};costs['contiguous_s']=costs['Prep_plus_A_s']+costs['Gap_s'];full=None
  summary['models'][model][c]={'n':len(vals),'A_mean_s':statistics.mean(vals),'A_median_s':statistics.median(vals),'A_sample_sd_s':statistics.stdev(vals),'A_values_s':vals,**costs,'controller_elapsed_mean_s':statistics.mean(full) if full else None}
with (o/'comparison-summary.json').open('x') as f:json.dump(summary,f,indent=2);f.write('\n')
fields=['model','cohort','block','condition','run_id','status','qualified','source_commit','protocol_revision_or_sha','A_s','Prep_s','Gap_s','fixed32_whole_HTTP_s','result_path','result_sha256']
with (o/'all-runs-ledger.csv').open('x',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
 for r in k['runs']:
  w.writerow(dict(model='Kimi-K2.6',cohort=r['cohort'],block=r['block'],condition=r['condition'],run_id=r['run_id'],status=r['controller_status'],qualified=r['qualified'],source_commit=(r['version'] or [None,None])[0],protocol_revision_or_sha=(r['version'] or [None,None])[1],A_s=r['timing']['A_s'],Prep_s=r['timing']['Prep_s'],Gap_s=r['timing']['Gap_s'],fixed32_whole_HTTP_s=r['timing']['D_fixed32_s'],result_path=(r['refs']['controller'] or {}).get('path'),result_sha256=(r['refs']['controller'] or {}).get('sha256')))
 for r in gl['rows']:
  w.writerow(dict(model='GLM-5.1-FP8',cohort='original_C23' if r['is_C23'] else 'original_earlier_protocol',block=r['block'],condition=r['condition'],run_id=r['attempt_id'],status=r['status'],qualified=r['status']=='success',protocol_revision_or_sha=r['protocol_revision'],A_s=r['A_s'],result_path=r['result_path'],result_sha256=r['result_sha256']))
receipts=['kimi-four-analysis-consumer-source-cpu-verification-v2-20261107.json','kimi-four-analysis-consumer-source-independent-review-v2-20261107.json','kimi-four-condition-actual-analysis-v1-execution-20261107.json','kimi-four-condition-actual-analysis-v1-independent-review-20261107.json','kimi-received-glm-reanalysis-readonly-verification-20261107.json','kimi-four-analysis-input-manifest-v2-independent-freeze-review-20261107.json']
for name in receipts:
 b=(s/name).read_bytes()
 with (o/name).open('xb') as f:f.write(b)
lines=['# Kimi 与 GLM 四条件实验对比（2026-11-07）','',
'## 结论',
'两模型各自的 Store 路径均有更低的注册后首业务等待时间。Kimi 在 8 个完整 block 上，Store 冷盘相对 SF 冷盘为 5.27× 加速，池命中为 8.02×；GLM 在 6 个完整 C23 block 上分别为 2.51×、7.12×。这些是各模型内部的均值之比，不能据此声称 Kimi 比 GLM 更快或实现更优。','',
'加入实测准备成本后，Kimi 池命中相对 Store 冷盘的均值优势从 82.656 秒收窄为 17.579 秒（Prep+A）；GLM 的池命中 Prep+A 反而比 Store 冷盘高约 5.5 秒。GLM 准备阶段可能包含前一池卸载，Kimi 是目标条件构造与准入，不能将这个差异全归因于加载实现。','',
'## 图 1：论文式主对比',
'![主对比](fig1_startup_means.png)','',
'参照 [ServerlessLLM / OSDI 2024 Figure 6a](https://www.usenix.org/system/files/osdi24-fu.pdf#page=11) 的分组均值柱图形式；本图指标是注册到首道算术回复，论文 Figure 6a 是 checkpoint loading。仅借鉴形式，不复用其数值，不宣称同指标复现或 10–200× 系统结果。',
'柱为算术均值，黑点为各 block 的实际值；右图在模型内部除以自身 SF 冷盘均值。未画未经定义的跨模型配对检验。','',
'| 条件 | Kimi 主集 n | Kimi 均值/秒 | GLM 主集 n | GLM 均值/秒 |',
'|---|---:|---:|---:|---:|']
for c,label in zip(C,labels):
 a,b=summary['models']['Kimi-K2.6'][c],summary['models']['GLM-5.1-FP8'][c]
 lines.append(f"| {label} | {a['n']} | {a['A_mean_s']:.6f} | {b['n']} | {b['A_mean_s']:.6f} |")
lines+=['','## 样本范围与失败',
'- Kimi 原计划 40 项全部启动：38 合格、2 失败、0 未启动。主完整 block 为 1/2/3/5/6/7/8/10，共 32 个样本；b04 和 b09 整块不进入任何主配对比较。',
'- b04 首次 SF 冷盘缓存准入失败、b09 SF 热缓存 worker 身份发现权限错误均保留。b04 额外 SF 冷盘 retry 成功单列，不替换原失败；b04 第四项池命中已执行成功，计入原 40 项的描述集。',
'- Kimi 额外补跑 1/1 合格；更早历史 5 次启动 1 合格/4 失败按旧标准保留。三类不混样本；累计形式上的 46 次/40 合格/6 失败也不冒充当期 40 项成功率。',
'- GLM 原 40 项保留 35 成功/5 失败；全量图标记不同协议。当前 C23 描述集 26 条（6/6/7/7），主完整 block 5–10 共 24 条；其余旧协议成功不混入主推断。',
'- 全量结果的 38/40 与 35/40 是描述性运行通过比例，试验边界、修复历史与故障机制不同，不能作为两模型可靠性的因果比较。','',
'![全部原40项](appendix_original40_outcomes.png)','',
'全量逐行表：[all-runs-ledger.csv](all-runs-ledger.csv)，共 Kimi 46 行（当期/补跑/历史明确分层）与 GLM 原 40 行。GLM 失败 A 为空，热图写 FAIL；Kimi 历史旧 schema 的 A 不重算，保留原始嵌套 metrics。',
'','## 图 2：全部当前有效样本的分布',
'![ECDF](fig2_qualified_ecdf.png)','',
'Kimi 当期描述集 38 条、各条件 n=9/9/10/10；GLM C23 描述集 26 条、各条件 n=6/6/7/7。部分 block 的成功只用于此描述图，不用于补成主配对。失败没有赋值为 0，也没有丢失其原始终态。',
'','## 计时对齐与图 3：成本窗口',
'- A：实际 HTTP 客户端发起 /register 之前 → 首题 17+25 响应读完、JSON 解析及 context 退出完成的 received_ns，早于金标断言。独立注册/结束事件与 HTTP metrics 的时钟、requestID、client PID/CID、整数 ns 端点三方核对。父进程轮询、校验、打印和落盘不计入 A。',
'- GLM 179.112278228 秒是 cold_09 的该 A 值，恰为 C23 Store 冷盘 n=7 中位数；描述均值 180.874626875 秒。主完整 n=6 的均值为 178.399300519 秒，不与描述 n=7 混用。',
'- 请求顺序：算术 → cat 翻译 → 天空 fixed32。两模型使用相同文本、各自 tokenizer/chat template。Kimi thinking=false / preserve_thinking=false，正式温度 0、seed 20261024；Kimi 显式 top_p=1/top_k=0/min_p=0/n=1，GLM 未显式发送的参数不冒充已核实同值。',
'- 算术与翻译 max_tokens=128、ignore_eos=false，严格模型自身金标。天空 max_tokens=32、ignore_eos=true，按采样前选定的新标准要求实际 32 tokens/length，文本差异单列；旧逐 token 金标失败不改写。',
'- Prep 是实测目标缓存条件构造/准入；Gap 是准备结束到 HTTP 客户端发起注册；连续窗口 = Prep+Gap+A。Prep+A 是预定次指标，明确排除 Gap。公用环境与 640 GiB Store 池初始化、最终关停及释放都不在这些窗口内。',
'','![准备与响应窗口](fig3_preparation_gap_startup.png)','',
'| 模型 | 条件 | Prep/秒 | Gap/秒 | Prep+A/秒 | 连续窗口/秒 |',
'|---|---|---:|---:|---:|---:|']
for model in summary['models']:
 for c,label in zip(C,labels):
  q=summary['models'][model][c]
  lines.append(f"| {model} | {label} | {q['Prep_s']:.6f} | {q['Gap_s']:.6f} | {q['Prep_plus_A_s']:.6f} | {q['contiguous_s']:.6f} |")
lines+=['',
'GLM Prep 在部分条件转换中包含前一池卸载（描述集 14/26）。上图是各自已测成本范围，不表示相同纯 I/O 工作。没有把相互嵌套的设备初始化、权重流水线、量化后处理阶段重复堆叠。',
'',
'## Kimi 配对推断',
'原始 ns 重推、独立数学复算通过。每个比较用相同 8 个完整 block；双侧精确符号翻转 abs(mean delta)，枚举 256 个符号，四项一族 Holm。Bootstrap 为成对 block 重采样 10000 次、MT19937 seed=20261024，每个统计重置，95% marginal percentile（索引 249/9749）。不是同时置信区间。','',
'| 左 − 右 | 均差/秒 | 均差95%区间 | 左/右均值比 | 原始 p | Holm4 p |',
'|---|---:|---|---:|---:|---:|']
for q in k['primary_A_Holm4']:
 ci=q['difference_percentile95']
 lines.append(f"| {q['left']} − {q['right']} | {q['mean_difference_s']:.6f} | [{ci[0]:.6f}, {ci[1]:.6f}] | {q['ratio_of_means']:.6f} | {q['exact_two_sided_p']:.7f} | {q['holm4_adjusted_p']:.5f} |")
lines+=['',
'四项 Kimi 主比较在该族 alpha=.05 下均拒绝零差异；Prep+A 的四项是独立第二族，也均 Holm=.03125，不声称八项联合 FWER 控制。GLM 已核验主族前 3 项 Holm=.125、第四项=.5625；Prep+A 四项 Holm=.125。在 n=6 的四项精确检验中，最小可达首位 Holm p=.125，因此不显著不等于没有效果。Kimi 与 GLM 的显著性差异也不是模型间效果差异的显著检验。',
'Bootstrap RNG 等实现细节是在统计前补充冻结，不虚称全部实现细节均在采样前预注册。完整原数组、版本分层与区间见 [actual analysis](kimi-four-condition-actual-analysis-v1-20261107.json)。',
'',
'## fixed32 与数值差异',
'Kimi 主集四条件已就绪 fixed32 整个 HTTP 请求的均值分别为：'+', '.join(f"{labels[i]} {summary['models']['Kimi-K2.6'][c]['D_fixed32_s']:.6f}s" for i,c in enumerate(C))+'。这不是纯 decode/TPOT，也不是首业务启动时间。',
'GLM 执行产物未保存 fixed32 独立开始/结束，留为缺测，不从文件 mtime、整体 elapsed 或算术 A 反推。Kimi backend 首个非空输出事件也不等同客户端流式 TTFT 或 GPU 第一次 sampling。',
'已观察 fixed32 文本/实际 token 差异，按新协议不作为性能失败；原因仍是未定位的数值差异，不擅称浮点误差、权重完全一致或路径无关。额外 top2 历史诊断保留其独立身份，不回填正式 logprobs。',
'',
'## 版本、存储与复现',
'- GPU 运行源：完整 block 1/2/3 为 86fd23bff8c7a78735776d7085f7fcb2b0a4efad/v8；5/6/7/8/10 为 835e2b5e992dd4098f013ddfdf5de4415db373f1/v9。v9 增加缓存拒绝与 traceback 取证；可能引入微小准备落盘成本，不能抹掉版本差别。',
'- 统计源码：a43734f11e84a06577b3de3833d0e9895e8ab323，已发布并核验 GitHub 分支 kimi-qjhs002-aligned-tail。该提交只新增 analyzer 与其测试，不是重新部署 GPU 服务。',
'- Store ELF SHA256：95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd；其 manifest 59d70f4710ca19d95515e5ca99c5f14ddabd963b2051720908c748e766c2ee2d。',
'- Kimi qjhs003 本地 /dev/nvme1n1 XFS：复用 64 shard SF checkpoint；Store 一次本地化 617029596501 字节且完整校验。源码和本 evidence 在 NFS /home；不能称全栈本地 NVMe。GLM 也使用该节点本地 NVMe，但模型、量化、资源与实现配置不同，未做跨模型控制变量实验。',
'- Kimi 镜像本地 ID sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27（RepoDigest 缺失）；vLLM 0.19.1、Torch2.10.0+cu129、TP8/eager、GPU memory utilization .8、worker32CPU/1536GiB/SHM8GiB、Store640GiB/64MiB chunk/4threads。没有为了对齐盲调容量。',
'- 新正式 Store 正常关停窗口固定 60 秒，之后至少 60 秒释放观察；没有使用强杀或后台释放冒充正常退出。早期 30 秒失败保留；已接受 L1 功能成果不为本图重复加载。早期 raw 诊断的 formal L1 标签未被本图重写。',
'- 预算每条件7200秒、每block28920秒、原40项最大289200秒（80小时20分）；不是10–13小时的最大预算。新增一次SF补跑单列，不再开启第11个block。以上是准入上限，不是图中的实测性能。',
'- 只做 CPU 分析与绘图；没有新模型运行、GLM 补跑、Store 转换、R24 或新40项实验。已知源/小文件 SHA 不等于新做全量 SF 权重值比对。 GLM 部署接收仅覆盖既有17文件 gate 加 Store overlay；未重新核验整个部署、各 rank 实际机器码、GitHub 远端 ref 或大监控原始数据，详见只读接收回执。',
'',
'单次转换控制器完整 678.128942504 秒包含退出和释放，不是纯转换 I/O；Store 本地化加目标校验 2019.017771686 秒也不是纯复制时间。本轮没有 N>1 保留池的实测、稳态吞吐、实时迁移或有效带宽上限数据，因此不画伪实测摊销/吞吐曲线，也不从本图声称论文全系统收益。',
'',
'复现 CPU 统计（原始路径在服务器，输出需用新文件名以保留本次产物）：',
'```bash',
'python3 -B /home/boyjiang/kimi-new/ServerlessLLM/benchmarks/analyze_kimi_four_condition.py \\',
'  /home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-four-analysis-input-snapshot-v1-20261107/input-manifest-v3.json \\',
'  /absolute/new-output.json \\',
'  --manifest-sha 4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801',
'```',
'',
'绘图脚本在本目录；现有 Windows Python3.13.1 / Matplotlib3.10.8 只读取约380KB输入生成图，再上传服务器逐SHA校验。未安装新依赖/环境。PNG已人工视觉核对，PDF/SVG提供矢量导出。',
'',
'输入快照459小文件（1,648,410字节），v1/v2/v3均保留。v3 SHA：4dd7d68f5409941c362f28e937c2dce21770a5829677dc1dcde78b2f5f70f801；真实统计输出 SHA：0959cf17761ec076a07711bd010e8ab4e0f061bb3707b9cd252963fe29758df1。GLM原文件只读。',
'',
'## 可直接使用的论文图注（英文）',
'**Figure 1.** Time from the registration call to completion of the first arithmetic HTTP response under four storage states. Bars show arithmetic means and dots show observations from complete qualified blocks (Kimi: eight; GLM C23: six). The right panel normalizes each model to its own Safetensors disk-cold mean. The visual organization follows ServerlessLLM Figure 6a, but our metric includes service startup through the arithmetic response and is not checkpoint-only loading.',
'',
'**Figure 3.** Observed contiguous preparation-to-response intervals, decomposed into target-condition preparation, the preparation-to-registration gap, and registration-to-arithmetic-response time. Preparation semantics differ: GLM may include unloading the preceding pool, whereas Kimi records target-condition construction and admission. Common initialization and final cleanup are excluded; these are not full lifecycle costs.',
'',
'所有图、表与验证回执由 manifest.json 列出 SHA；历史原始目录未改写。']
with (o/'REPORT.md').open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
print(json.dumps({'report':str(o/'REPORT.md'),'report_bytes':(o/'REPORT.md').stat().st_size,'summary':summary},ensure_ascii=False))
