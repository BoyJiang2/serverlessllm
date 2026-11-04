import json
import pathlib
import subprocess

out = pathlib.Path('outputs')
for name in ('qwen3-official-fp16-probe', 'qwen3-official-random-cached', 'llama31-default-cached30'):
    folder = out / name
    inspected = json.loads((folder / 'final-inspect.json').read_text())
    assert inspected['State']['ExitCode'] == 0 and not inspected['State']['Running']
    samples = [json.loads(line) for line in (folder / 'final-release.jsonl').read_text().splitlines()]
    assert samples[-1]['elapsed'] >= 60
    assert all(row['gpu7_used_mib'] == 0 and row['pid'] == 0 and row['scope_removed'] for row in samples)

remote = r'''
import pathlib,json,hashlib,statistics
base=pathlib.Path('/data/brick1/boyjiang/ServerlessLLM/models')
cases=[
 ('Llama-3.1-8B-Instruct','random','.llama31-buffer-parity-final-20261104','meta-llama_Llama-3.1-8B-Instruct',True),
 ('Llama-3.1-8B-Instruct','cached','.llama31-default-cached30-20261104','meta-llama_Llama-3.1-8B-Instruct',False),
 ('Qwen3-8B','random','.qwen3-official-random-cached-20261104','Qwen_Qwen3-8B',False),
 ('Qwen3-8B','cached','.qwen3-official-random-cached-20261104','Qwen_Qwen3-8B',False)]
summary={'status':'ALL_DEFAULT30_GROUPS_PAIRED_VERIFIED','dtype':'float16','prompt':'The quick brown fox jumps over the lazy dog.','max_new_tokens':50,'n_per_format_per_group':30,'std_definition':'population standard deviation, ddof=0, same as official report','sdk_fix_commit':'dac95346fded9f721ae5f5f46157244b53464dbb','prepared_scripts_commit':'e5b0dbda55d65fb58b208924830497b8a9de7a21','groups':[],'raw_sha256':{}}
for model,phase,directory,stem,reused in cases:
 rows=[];paths=[]
 for fmt in ['safetensors','sllm']:
  path=base/directory/'results'/phase/f'{stem}_{fmt}_30_{phase}.json';data=path.read_bytes();values=json.loads(data);assert len(values)==30
  summary['raw_sha256'][str(path)]=hashlib.sha256(data).hexdigest();rows.append(values);paths.append(str(path))
 for left,right in zip(*rows):
  for key in ['model_name','input_token_ids','sampling_seed','generation_config','generated_token_ids']:assert left[key]==right[key],(model,phase,key,left['sampling_seed'])
  assert left['generation_config']['do_sample'] is True
  for row in [left,right]:
   assert row['buffers_match_safetensors_cpu_reference'] is True
   assert row['requested_max_new_tokens']==50 and row['generated_tokens']==len(row['generated_token_ids']) and 0<row['generated_tokens']<=50
 assert len({row['model_name'] for row in rows[0]})==(30 if phase=='random' else 1)
 group={'model':model,'mode':phase,'n_pairs':30,'reused_existing_validated_run':reused,'source_files':paths,'token_pairs_equal':True,'configuration_equal':True,'buffers_equal':True,'formats':{}}
 for fmt,values in zip(['safetensors','sllm'],rows):
  times=[row['loading_time'] for row in values]
  group['formats'][fmt]={'n':30,'mean_seconds':statistics.mean(times),'median_seconds':statistics.median(times),'std_seconds':statistics.pstdev(times),'min_seconds':min(times),'max_seconds':max(times),'mean_generation_seconds':statistics.mean(row['end_to_end_time'] for row in values),'generated_token_counts':sorted({row['generated_tokens'] for row in values})}
 group['load_speedup']=group['formats']['safetensors']['mean_seconds']/group['formats']['sllm']['mean_seconds'];summary['groups'].append(group)
for name in ['.qwen3-official-fp16-probe-20261104','.qwen3-official-random-cached-20261104','.llama31-default-cached30-20261104']:
 assert not list((base/name/'storage').iterdir()),('temporary replicas remain',name)
target=base/'.official-default-20261104';target.mkdir(exist_ok=False)
(target/'SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
lines=['# 官方仓库默认次数复现结果','','状态：全部通过。每模型、每种格式的random和cached均30次；预检及cached预热不计入正式样本。FP16，fox输入，generate(max_new_tokens=50)，各自原始生成配置保持采样；输出按相同seed配对。标准差为总体标准差（除以N）。','','| 模型 | 模式 | 格式 | N | 平均秒 | 中位数秒 | 标准差秒 |','|---|---|---|---:|---:|---:|---:|']
for group in summary['groups']:
 for fmt,stats in group['formats'].items():lines.append(f"| {group['model']} | {group['mode']} | {fmt} | 30 | {stats['mean_seconds']:.6f} | {stats['median_seconds']:.6f} | {stats['std_seconds']:.6f} |")
lines+=['','| 模型 | 模式 | 平均加载加速 |','|---|---|---:|']
for group in summary['groups']:lines.append(f"| {group['model']} | {group['mode']} | {group['load_speedup']:.3f}x |")
lines+=['','四组共120对输出逐token匹配（每对SF/SLLM），配置及缓冲区检查全部通过。Llama random30复用此前已经验证的原始运行；其余三组为本轮默认次数运行。完整参数字节SHA在两模型各自的独立GPU配对预检中验证，不声称240次正式加载全部进行了全参数字节hash。','','复用官方9f50241加载微基准和既有运行镜像/SDK。必要任务适配：离线复用本地checkpoint，预热用本地目标模型；加载入口恢复generation_config，指定参数dtype初始化以保留FP32 RoPE缓冲区，兼容当前Transformers私有接口变化。Torch2.10+cu129、Transformers5.5.1，GPU7 H20-3e，容器8CPU/128GiB，Store32GB/4IO线程/16MBchunk。运行命令不传num-replicas，清除同名环境变量后使用runner默认30。','','加载计时保留官方API墙钟区间，未在终点额外添加CUDA同步；转换、模块导入、预热、校验与生成不计入加载时间。恢复generation_config文件的IO计入SLLM加载API。生成时间额外做CUDA同步，单独记录。Random使用30份独立副本随机顺序各加载一次，不清系统page/inode cache；cached只制作一份副本，预热后重复加载30次。这是仓库协议，不是论文Figure6的20副本/清缓存协议，本机为H20，与README的H100环境不同。','','本次Qwen预检、Qwen正式组、Llama cached组均独立native scope限时，正常退出后至少60秒GPU7memory0、PID0和scope移除观察通过。临时副本清理完成。共享SDK/原checkpoint只读，Kimi代码与历史实验不变。配对一致性结论限本次输入/种子，未验证fully_parallel GPU或完整原生服务。','','## 服务端原始数据']
for group in summary['groups']:
 lines+=['',f"{group['model']} / {group['mode']}："]+['- `'+path+'`' for path in group['source_files']]
(target/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print(json.dumps({'directory':str(target),'summary':summary}))
'''
result = subprocess.check_output(['ssh', 'qjhs002', 'python3 -'], input=remote.encode())
data = json.loads(result)
folder = out / 'official-default-summary'
folder.mkdir(exist_ok=True)
(folder / 'SUMMARY.json').write_text(json.dumps(data['summary'], indent=2), encoding='utf-8')
(folder / 'server-location.json').write_text(json.dumps({'directory': data['directory']}, indent=2), encoding='utf-8')
print(json.dumps({'directory': data['directory'], 'groups': data['summary']['groups']}, indent=2))
