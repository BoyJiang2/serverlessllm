from pathlib import Path
import subprocess,os,json,sys,datetime,glob
base=Path('/logs');label=sys.argv[1];assert label in ('base','compat');os.umask(0o002)
result=json.loads((base/'build-vllm-a2-result.json').read_bytes());assert result['exit_code']==0 and result['native_vllm_built']
handle=json.loads((base/'build-vllm-a2-handle.json').read_bytes());p=Path('/proc',str(handle['builder']['pid']),'stat')
if p.exists():assert int(p.read_text().rsplit(')',1)[1].split()[19])!=handle['builder']['start_ticks']
assert not glob.glob('/dev/nvidia*');hidden=[];r=None
home=Path('/tmp/p4a-engine-smoke-'+label);home.mkdir(exist_ok=False)
env=dict(os.environ,HOME=str(home),XDG_CACHE_HOME=str(home/'cache'),HF_HOME=str(home/'hf'),HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false',VLLM_PLUGINS='',P4A_CPU_SMOKE_LABEL=label)
if label=='compat':env['LD_LIBRARY_PATH']='/usr/local/cuda/compat:'+env.get('LD_LIBRARY_PATH','')
try:
 for source in [Path('/opt/src/vllm'),Path('/opt/src/sllm')]:
  assert source.is_dir() and source.resolve()==source and source.parent==Path('/opt/src')
  backup=source.with_name(source.name+'.p4a-hidden');assert not backup.exists();source.rename(backup);hidden.append((source,backup))
 with (base/('standalone-engine-'+label+'.log')).open('x') as log:r=subprocess.run(['/opt/sllm-v4/bin/python','/logs/check_target_engine_cpu.py'],cwd='/tmp',env=env,stdout=log,stderr=subprocess.STDOUT)
finally:
 for source,backup in reversed(hidden):backup.rename(source)
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=r.returncode,label=label,both_source_trees_hidden=True,both_source_trees_restored=True,cache_home=str(home),loader_library_path=env.get('LD_LIBRARY_PATH'),scope='Fresh process from installed real wheels with both build source trees hidden; no model/engine construction or GPU execution')
(base/('standalone-engine-'+label+'-result.json')).write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report));sys.exit(r.returncode)
