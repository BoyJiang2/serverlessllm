from pathlib import Path
import ast,tarfile,json,datetime,os
os.umask(0o002);base=Path('/logs');receipt=json.loads((base/'python-archive-receipt.json').read_bytes());rel='lib/python3.12/_sysconfigdata__linux_x86_64-linux-gnu.py'
with tarfile.open(receipt['archive'],'r:gz') as tar:original=tar.extractfile('python/'+rel).read().decode()
def variables(s):
 tree=ast.parse(s);node=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='build_time_vars' for t in n.targets));return ast.literal_eval(node.value)
a=variables(original);b=variables((Path('/opt/python')/rel).read_text());changes={n:dict(original=a.get(n),installed=b.get(n)) for n in sorted(set(a)|set(b)) if a.get(n)!=b.get(n)}
critical=['SOABI','ABIFLAGS','Py_DEBUG','SIZEOF_VOID_P','WITH_PYMALLOC','MULTIARCH'];assert all(a[n]==b[n] for n in critical)
report=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),critical_abi_fields={n:b[n] for n in critical},abi_fields_unchanged=True,changed_sysconfig_variables=changes,scope='Inspect exact UV relocation/sysconfig adjustments; binary/libpython/header identity compared separately')
(base/'python-sysconfig-relocation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report))
