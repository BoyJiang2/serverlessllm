import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Only observed head IP or the exact run slice may vary from reviewed templates."""
import hashlib,json,pathlib,subprocess,sys
from workspace import SOURCE,run_root
from budget_control13 import POOL_DIAGNOSTIC_SCOPE,POOL_DIAGNOSTIC_PHASE_BUDGET

def call(*args):
    return subprocess.check_output(args,text=True,timeout=15).strip()

def verify_sigint_probe(root,info,code,sha):
    import time
    manifest=json.loads((root/'run-manifest.json').read_text())
    head=json.loads((root/'headservice13a-identity.json').read_text())
    dispatch=json.loads((root/'headservice13a-dispatch.json').read_text())
    proof=json.loads((root/'cpu-import-preflight.json').read_text())['sigint_probe']
    expected=dict(run_id=manifest['run_id'],head_container_id=head['container_id'],image_id=manifest['image_id'],boot_id=manifest['runtime_identity']['boot_id'],code_commit=code,manifest_sha256=sha)
    if any(proof.get(k)!=v for k,v in expected.items()):raise RuntimeError('SIGINT preallocation proof binding differs')
    if head['run_id']!=manifest['run_id'] or head['code_commit']!=code or head['manifest_sha256']!=sha or dispatch['boot_id']!=expected['boot_id']:raise RuntimeError('SIGINT head identity differs')
    if info['Id']!=head['container_id'] or info['Image']!=manifest['image_id'] or not info['State']['Running'] or info['State']['Pid']!=head['pid']:raise RuntimeError('SIGINT probe head no longer active')
    if info['Config']['Labels'].get('sllm.run_id')!=manifest['run_id'] or info['Config']['Labels'].get('sllm.uid')!='2147':raise RuntimeError('SIGINT head ownership differs')
    if proof.get('signal')!=2 or proof.get('returncode')!=0 or proof.get('cleanup_marker')!='cleanup' or proof.get('interpreter')!='/experiment/.venv/bin/python' or not dispatch['dispatch_monotonic_ns']<=proof['monotonic_ns']<=time.monotonic_ns():raise RuntimeError('SIGINT cancellation proof failed or stale')


def validate_scope(root,name):
    manifest=json.loads((root/'run-manifest.json').read_text())
    diagnostic=manifest['scope']=='monitor diagnostics only; no model or pool'
    if manifest.get('grpc_shutdown_probe') and not diagnostic:raise RuntimeError('grpc probe requires CPU-only diagnostic scope')
    if manifest['scope']==POOL_DIAGNOSTIC_SCOPE:
        if manifest.get('monitor_diagnostic_trace') is not True or name not in ('monitor13','headservice13a','service13a'):raise RuntimeError('pool diagnostic forbids this launch')
    elif diagnostic:
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
    config=json.loads((root/'run-manifest.json').read_text())
    pool_diagnostic=config['scope']==POOL_DIAGNOSTIC_SCOPE
    run=config['run_id']
    if name in ('headray13a','headray13b','headservice13a'):
        spec=json.loads((SOURCE/(name+'.json')).read_text())
        if diagnostic or pool_diagnostic:
            ttl=str(POOL_DIAGNOSTIC_PHASE_BUDGET-5)+'s' if pool_diagnostic else '175s'
            spec['argv']=['/usr/bin/timeout','--signal=TERM','--kill-after=5s',ttl,*spec['argv']]
        return spec
    if name=='monitor13':
        spec=json.loads((SOURCE/'monitor13-template.json').read_text())
        spec['env']['SLLM_CONTROL_GROUP']=call('systemctl','show',run+'.slice','-p','ControlGroup','--value')
        assert pathlib.PurePosixPath(spec['env']['SLLM_CONTROL_GROUP']).name==run+'.slice'
        return spec
    assert name in ('ray13a','ray13b','service13a')
    spec=json.loads((SOURCE/(name+'-template.json')).read_text())
    if pool_diagnostic:
        spec['phase_trace']=False
        for key in ('SLLM_VALIDATE_FULL_MODEL','SLLM_SERVICE_WEIGHT_FORMAT','SLLM_VERIFY_WEIGHTS','VLLM_ALLOW_INSECURE_SERIALIZATION','SLLM_SERVICE_WEIGHT_AUDIT_DIR','SLLM_EVENT_DIR'):spec['env'].pop(key,None)
        spec['env']['SLLM_POOL_DIAGNOSTIC']='1'
        spec['env']['STORAGE_PATH']='/r/service-bootstrap-service13a/store'
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
