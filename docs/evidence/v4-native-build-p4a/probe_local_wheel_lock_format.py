from pathlib import Path
import subprocess,json,datetime,ast
base=Path('/home/boyjiang/sllm-v4-pro/evidence/p4a-bootstrap-20261106');work=Path('/data/brick1/boyjiang/sllm-v4-pro-build');source=base/'local-wheel-format-probe.in';source.write_text('gputil @ '+(work/'pure-wheels/gputil-1.4.0-py3-none-any.whl').as_uri()+'\n')
cmd=[str(work/'tools/uv'),'--no-config','pip','compile',str(source),'--python',str(work/'python/cpython-3.12.3-linux-x86_64-gnu/bin/python3.12'),'--offline','--no-index','--no-build','--format','pylock.toml','-o',str(base/'pylock.local-wheel-format-probe.toml')]
with (base/'local-wheel-format-probe.log').open('x') as log:r=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
assert r.returncode==0;(base/'local-wheel-format-probe-receipt.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),exit_code=r.returncode,scope='Offline local-wheel lock format probe only; no installation or final dependency graph claim'))+'\n')
for name in ['resolve_p4a_final.py','install_p4a_final.py','check_target_engine_cpu.py','check_target_elf_sass.py']:ast.parse((base/name).read_text(),filename=name)
print((base/'pylock.local-wheel-format-probe.toml').read_text())
