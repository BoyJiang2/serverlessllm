import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Only observed head IP or the exact run slice may vary from reviewed templates."""
import hashlib,json,pathlib,subprocess,sys
from workspace import SOURCE,run_root

def call(*args):
    return subprocess.check_output(args,text=True,timeout=15).strip()

def validate_scope(root,name):
    manifest=json.loads((root/'run-manifest.json').read_text())
    diagnostic=manifest['scope']=='monitor diagnostics only; no model or pool'
    if diagnostic:
        if manifest.get('monitor_diagnostic_trace') is not True or name not in ('monitor13','headservice13a'):
            raise RuntimeError('diagnostic scope forbids this launch')
    elif manifest.get('monitor_diagnostic_trace',False):
        raise RuntimeError('trace requires diagnostic-only scope')
    return diagnostic


def verify_timeout_probe(root,info):
    manifest=json.loads((root/'run-manifest.json').read_text())
    deployment=json.loads((root/'deployment.json').read_text())
    identity=json.loads((root/'monitor13-identity.json').read_text())
    proof=json.loads((root/'diagnostic-timeout-probe.json').read_text())
    expected=dict(run_id=manifest['run_id'],code_commit=deployment['code_commit'],manifest_sha256=deployment['input_manifest_sha256'],boot_id=manifest['runtime_identity']['boot_id'],container_id=identity['container_id'],image_id=manifest['image_id'],argv=['/usr/bin/timeout','--version'],returncode=0)
    if any(proof.get(k)!=v for k,v in expected.items()) or hashlib.sha256(proof['stdout'].encode()).hexdigest()!=proof['stdout_sha256'] or not proof['stdout'].startswith('timeout (GNU coreutils)'):
        raise RuntimeError('timeout probe receipt differs')
    labels=info['Config']['Labels']
    if info['Id']!=identity['container_id'] or info['Image']!=manifest['image_id'] or labels.get('sllm.run_id')!=manifest['run_id'] or labels.get('sllm.uid')!='2147' or not info['State']['Running'] or info['State']['Pid']!=identity['pid']:
        raise RuntimeError('timeout probe container identity changed')
    return proof


def expected_spec(root,name):
    diagnostic=validate_scope(root,name)
    run=json.loads((root/'run-manifest.json').read_text())['run_id']
    if name in ('headray13a','headray13b','headservice13a'):
        spec=json.loads((SOURCE/(name+'.json')).read_text())
        if diagnostic:
            spec['argv']=['/usr/bin/timeout','--signal=TERM','--kill-after=5s','175s',*spec['argv']]
        return spec
    if name=='monitor13':
        spec=json.loads((SOURCE/'monitor13-template.json').read_text())
        spec['env']['SLLM_CONTROL_GROUP']=call('systemctl','show',run+'.slice','-p','ControlGroup','--value')
        assert pathlib.PurePosixPath(spec['env']['SLLM_CONTROL_GROUP']).name==run+'.slice'
        return spec
    assert name in ('ray13a','ray13b','service13a')
    spec=json.loads((SOURCE/(name+'-template.json')).read_text())
    head=run+'-head'+name
    info=json.loads(call('docker','inspect',head))[0]
    assert info['State']['Running'] and info['Config']['Labels']['sllm.run_id']==run
    assert info['Config']['Labels']['sllm.uid']=='2147'
    ip=info['NetworkSettings']['Networks']['bridge']['IPAddress']
    import ipaddress
    assert ipaddress.ip_address(ip).is_private
    spec['env']['SLLM_PRIVATE_RAY_ADDRESS']=spec['env']['SLLM_PRIVATE_RAY_ADDRESS'].replace('PENDING_OBSERVED_HEAD_IP',ip)
    return spec

def validate_spec(root,spec):
    assert spec==expected_spec(root,spec['name']), 'derived configuration differs from reviewed constraints'

if __name__=='__main__':
    root=run_root()
    name=sys.argv[1]
    spec=expected_spec(root,name)
    validate_spec(root,spec)
    with (root/(name+'.json')).open('x') as output:
        json.dump(spec,output,indent=2)
