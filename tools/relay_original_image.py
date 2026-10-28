"""Bounded local-archive relay to qjhs003 and CPU-only Docker startup proof."""
import argparse
import datetime
import json
from pathlib import Path
import shlex
import subprocess
import time

IMAGE = 'sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12', '-o', 'ServerAliveInterval=15',
       '-o', 'ServerAliveCountMax=3', 'qjhs003']
REMOTE = r'''
import datetime,hashlib,json,pathlib,subprocess,sys
p=pathlib.Path(sys.argv[1]); expected=sys.argv[2]; image=sys.argv[3]; run=p.name
archive=p/'original-image.docker.tar.partial'
with archive.open('rb') as f: observed=hashlib.file_digest(f,'sha256').hexdigest()
if observed!=expected:raise RuntimeError('relay archive hash mismatch')
final=p/'original-image.docker.tar'
if final.exists():raise RuntimeError('backup target exists')
archive.rename(final)
with (p/'docker-load.log').open('x') as log:
 subprocess.run(['docker','image','load','--input',str(final)],stdout=log,stderr=subprocess.STDOUT,timeout=600,check=True)
actual=subprocess.check_output(['docker','image','inspect',image,'--format','{{.Id}}'],text=True,timeout=15).strip()
if actual!=image:raise RuntimeError('restored image identity mismatch')
name=run+'-cpu-probe'
cmd=['docker','run','--rm','--pull=never','--network=none','--runtime=runc','--user=2147:2147',
 '--memory=2g','--cpus=2','--name='+name,'--label=sllm.run_id='+run,'--label=sllm.purpose=DEBUG-UNREVIEWED',
 '--entrypoint=/usr/bin/timeout',image,'90','python3','-c',
 'import importlib.metadata as m,json; print(json.dumps({k:m.version(k) for k in ["vllm","torch","transformers","triton"]}))']
try:
 probe=subprocess.run(cmd,text=True,capture_output=True,timeout=120,check=True)
 versions=json.loads(probe.stdout)
 if not versions['vllm'].startswith('0.19.1.dev1') or versions['torch']!='2.10.0+cu130' or versions['transformers']!='5.4.0':
  raise RuntimeError('restored package versions mismatch')
finally:
 inspect=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
 if inspect.returncode==0:
  identity=json.loads(inspect.stdout)[0]
  if identity['Config']['Labels'].get('sllm.run_id')!=run:raise RuntimeError('probe cleanup identity mismatch')
  subprocess.run(['docker','rm','-f',identity['Id']],check=True,timeout=30,capture_output=True)
remaining=subprocess.check_output(['docker','ps','-aq','--filter','label=sllm.run_id='+run],text=True,timeout=15).strip()
if remaining:raise RuntimeError('probe container remains')
result=dict(status='ssh_docker_cpu_start_verified',utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
 image_id=actual,versions=versions,archive_sha256=observed,archive_bytes=final.stat().st_size,
 backup=str(final),probe_container_released=True,model_service_started=False,run_id=run)
with (p/'startup-proof.json').open('x') as f:json.dump(result,f,indent=2)
print(json.dumps(result))
'''


def main(output):
    registration = output/'relay-registration.json'
    if registration.exists():
        raise FileExistsError('relay already registered; inspect existing task rather than duplicate')
    run = 'image-relay-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    remote = '/data/brick1/boyjiang/glm5.1-fp8/recovery/'+run
    registration.write_text(json.dumps(dict(run_id=run, remote=remote, status='waiting_for_verified_archive'))+'\n')
    deadline = time.monotonic()+1900
    try:
        while not (output/'archive-receipt.json').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('archive readiness deadline; no automatic restart')
            time.sleep(10)
        receipt = json.loads((output/'archive-receipt.json').read_text())
        if receipt['image_id'] != IMAGE:
            raise ValueError('archive receipt image mismatch')
        archive = output/'original-image.docker.tar'
        if archive.stat().st_size != receipt['archive_bytes']:
            raise ValueError('archive size differs from receipt')
        subprocess.run(SSH+[shlex.join(['mkdir', '-m', '700', remote])],check=True,timeout=30)
        print('RELAY_BEGIN', run, flush=True)
        with (output/'scp.log').open('x') as log:
            subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=12',
                            '-o','ServerAliveInterval=15','-o','ServerAliveCountMax=3',str(archive),
                            'qjhs003:'+remote+'/original-image.docker.tar.partial'],
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1200)
        print('RELAY_COMPLETE_IMPORT_BEGIN', flush=True)
        result = subprocess.run(SSH+[shlex.join(['python3','-c',REMOTE,remote,receipt['archive_sha256'],IMAGE])],
                                capture_output=True,text=True,check=True,timeout=900)
        proof = json.loads(result.stdout)
        (output/'startup-proof.json').write_text(json.dumps(proof,indent=2)+'\n')
        print(json.dumps(proof),flush=True)
    except Exception as error:
        (output/'relay-failure.json').write_text(json.dumps(dict(run_id=run, error_type=type(error).__name__,
                                                               status='failed_no_automatic_retry'))+'\n')
        raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output.resolve())
