from pathlib import Path
import subprocess,json,hashlib,os,sys,glob,datetime
BASE=Path('/logs');WORK=Path('/data/brick1/boyjiang/sllm-v4-pro-build')
def main():
 os.umask(0o002);assert not glob.glob('/dev/nvidia*')
 out=WORK/'native-wheels/service-a2';out.mkdir(parents=True,exist_ok=False)
 cmd=['/opt/sllm-v4/bin/python','-m','build','--wheel','--no-isolation','--outdir',str(out),'/opt/src/sllm']
 with (BASE/'build-service-a2.log').open('x') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,timeout=300)
 wheels=[dict(file=str(p),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in out.glob('*.whl')]
 (BASE/'build-service-a2-result.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=r.returncode,wheels=wheels,scope='Actual pure Python service wheel; no GPU/model or service execution'),indent=2)+'\n')
 return r.returncode
if __name__=='__main__':sys.exit(main())
