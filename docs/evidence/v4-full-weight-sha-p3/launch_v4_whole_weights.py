from pathlib import Path
import datetime,subprocess,json,shutil
root=Path('/home/boyjiang/sllm-v4-pro/evidence')
assert not list(root.glob('full-weight-sha-*/handle.json')),'Inspect existing verifier before any new launch'
job=root/('full-weight-sha-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'));job.mkdir()
src=root/'v4-weight-provenance-20261106'
for name in ('expected-weight-files.json','expected-whole-files.sha256','official-model-info.json'):
 shutil.copyfile(src/name,job/name)
shutil.copyfile(root/'verify_v4_whole_weights.py',job/'verify_v4_whole_weights.py')
(job/'authorization.json').write_text(json.dumps(dict(scope='User explicitly approved CPU serial full-weight SHA-256 verification; no inference install/GPU',approved_answer='纳入范围，执行 CPU 串行完整校验（推荐）'),ensure_ascii=False)+'\n',encoding='utf8')
with (job/'supervisor.log').open('xb') as log:
 p=subprocess.Popen(['python3',str(job/'verify_v4_whole_weights.py'),str(job)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True,stdin=subprocess.DEVNULL)
print(json.dumps(dict(job=str(job),supervisor_pid=p.pid)))
