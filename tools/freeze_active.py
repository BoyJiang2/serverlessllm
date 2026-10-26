"""Freeze the single active R1 source in place; CPU checks only, no deployment."""
import datetime,hashlib,json,pathlib,sys,tempfile,unittest

repo=pathlib.Path(__file__).resolve().parents[1]
source=repo/'runtime/active'
config=json.loads((source/'run-manifest.json').read_text())
revision=config['candidate_revision']
if not __import__('re').fullmatch(r'canonical[1-9][0-9]*',revision):raise RuntimeError('invalid review revision')
out=repo/'reviews'/('full-r1-'+revision)
out.mkdir(exist_ok=True)
identity=json.loads((source/'CONTROL-IDENTITY.json').read_text())
if any(identity[key]!=config[key] for key in ('run_id','cleanup_deadline','workspace_root')):raise RuntimeError('control identity differs')
if "\nrun='"+config['run_id']+"'\n" not in (source/'snapshot_root13.py').read_text():raise RuntimeError('root snapshot identity differs')
deadline=datetime.datetime.fromisoformat(config['cleanup_deadline'])
for name in ('install_guard13.sh','cleanup_guard13.sh','install_observers13.sh'):
    if '\nrun_id='+config['run_id']+'\n' not in (source/name).read_text():raise RuntimeError('guard run identity differs: '+name)
calendar=deadline.astimezone(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
if "--on-calendar='"+calendar+"'" not in (source/'install_guard13.sh').read_text():raise RuntimeError('guard calendar differs')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8',newline='\n')
window=json.loads((source/'resource-window-extension-v3.json').read_text())
window['original_manifest_sha256']=digest(source/'run-manifest.json')
window['overlay_sealed_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
save(source/'resource-window-extension-v3.json',window)
for path in source.glob('*.py'):compile(path.read_text(encoding='utf-8-sig'),path.name,'exec')
temporary=repo.parent/'work/cpu-test-tmp';temporary.mkdir(exist_ok=True)
tempfile.tempdir=str(temporary.resolve())
sys.path.insert(0,str(source))
suite=unittest.defaultTestLoader.discover(str(source),pattern='test*.py')
with (out/'cpu-tests.log').open('w',encoding='utf-8',newline='\n') as stream:
    result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
if not result.wasSuccessful():raise RuntimeError('CPU tests failed; original log retained')
sys.path.insert(0,str(repo/'tools'))
operator_suite=unittest.defaultTestLoader.discover(str(repo/'tools'),pattern='test_control_transport13.py',top_level_dir=str(repo/'tools'))
with (out/'operator-tests.log').open('w',encoding='utf-8',newline='\n') as stream:
    operator_result=unittest.TextTestRunner(stream=stream,verbosity=2).run(operator_suite)
if not operator_result.wasSuccessful():raise RuntimeError('operator transport tests failed')
syntax=json.loads((out/'shell-syntax.json').read_text())
for row in syntax['files']:
    if digest(source/row['path'])!=row['sha256']:raise RuntimeError('shell syntax receipt is stale')
rows=[]
for path in sorted(source.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts or path.name in ('control-input-manifest.json','REVIEW-PROMPT.md'):continue
    data=path.read_bytes()
    if path.is_symlink() or len(data)>1024**2 or b'\0' in data:raise RuntimeError('unexpected package input')
    rows.append(dict(path=path.relative_to(source).as_posix(),bytes=len(data),sha256=digest(path)))
repository_inputs=[pathlib.Path(__file__),repo/'protocol/CANONICAL-WORKSPACE.md',out/'cpu-tests.log',out/'shell-syntax.json',
                   repo/'tools/run_control13.py',repo/'tools/root_control13.py',repo/'tools/test_control_transport13.py',out/'operator-tests.log']
manifest=dict(gate='R1',scope='full-runtime',status='pending actual independent review; not runtime approval',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),files=rows,
              repository_inputs=[dict(path=p.relative_to(repo).as_posix(),sha256=digest(p),bytes=p.stat().st_size) for p in repository_inputs],
              local_validation=dict(cpu_tests=result.testsRun,operator_tests=operator_result.testsRun,python_compile='passed',shell_syntax='3 passed separately; scripts not executed'),
              unchanged_history=dict(success=5,failed=4,unstarted=31,next_id='store_pool_hit_03'),
              external_source_reference=dict(path='protocol/historical-review-manifest-v11.json',sha256='da681f8d83ee3416faeffad30f230365e9956c8c6763aa30487d4bb809be81b4',files=1142,scope='rehash at launch through pinned canonical mapping; no payload hash claim'))
save(source/'control-input-manifest.json',manifest)
print(json.dumps(dict(files=len(rows),tests=result.testsRun,manifest_sha256=digest(source/'control-input-manifest.json'))))
