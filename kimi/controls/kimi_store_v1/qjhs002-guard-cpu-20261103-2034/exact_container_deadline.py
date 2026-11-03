"""One-shot systemd deadline guard for one pre-created owned container."""
import datetime,json,pathlib,re,subprocess,sys,time

manifest=json.loads(pathlib.Path(sys.argv[1]).read_text())
assert re.fullmatch('[0-9a-f]{64}',manifest['cid'])
assert manifest['endpoint']=='unix:///var/run/docker.sock'
assert 0 < manifest['deadline']-time.time() <= 1800
monotonic_deadline=time.monotonic()+manifest['deadline']-time.time()
base=['/usr/bin/docker','--host',manifest['endpoint']]
def event(kind,**data):
    print(json.dumps({'event':kind,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'nonce':manifest['nonce'],**data}),flush=True)
def inspect():
    result=subprocess.run(base+['inspect',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
    row=json.loads(result.stdout)[0]
    assert row['Id']==manifest['cid'] and row['Created']==manifest['created']
    assert row['Image']==manifest['image']
    assert row['Config']['Labels'].get('sllm.kimi.run')==manifest['nonce']
    assert row['HostConfig']['RestartPolicy']['Name']=='no'
    return row
try:
    row=inspect()
    assert not row['State']['Running']
    event('ready',cid=row['Id'],pid=__import__('os').getpid(),deadline=manifest['deadline'])
    while time.time()<manifest['deadline'] and time.monotonic()<monotonic_deadline:
        inspect()
        time.sleep(min(1,max(0,min(manifest['deadline']-time.time(),monotonic_deadline-time.monotonic()))))
    row=inspect()
    if row['State']['Running']:
        subprocess.run(base+['kill',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
        event('killed',cid=manifest['cid'])
    row=inspect()
    assert not row['State']['Running']
    event('stopped',cid=manifest['cid'],state=row['State'])
except BaseException as error:
    event('failed',error_type=type(error).__name__,error=str(error))
    # A verified owned container can be stopped on a guard failure; a changed
    # identity is never sufficient authority to kill another container.
    try:
        row=inspect()
        if row['State']['Running']:
            subprocess.run(base+['kill',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
            event('failure_cleanup_killed',cid=manifest['cid'])
    except BaseException as cleanup_error:
        event('failure_cleanup_failed',error_type=type(cleanup_error).__name__,error=str(cleanup_error))
    raise
