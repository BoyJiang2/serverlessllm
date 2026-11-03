"""One-shot systemd deadline guard for one pre-created owned container."""
import datetime,json,pathlib,re,subprocess,sys,time
import xml.etree.ElementTree as ET

manifest=json.loads(pathlib.Path(sys.argv[1]).read_text())
assert re.fullmatch('[0-9a-f]{64}',manifest['cid'])
assert manifest['endpoint']=='unix:///var/run/docker.sock'
assert 0 < manifest['deadline']-time.time() <= 1800
monotonic_deadline=time.monotonic()+manifest['deadline']-time.time()
base=['/usr/bin/docker','--host',manifest['endpoint']]
start_attempted=False
def event(kind,**data):
    print(json.dumps({'event':kind,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'nonce':manifest['nonce'],**data}),flush=True)
def inspect():
    result=subprocess.run(base+['inspect',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
    row=json.loads(result.stdout)[0]
    assert row['Id']==manifest['cid'] and row['Created']==manifest['created']
    assert row['Image']==manifest['image']
    assert row['Config']['Labels'].get('sllm.kimi.run')==manifest['nonce']
    assert row['HostConfig']['RestartPolicy']['Name']=='no'
    if 'gpu_uuid' in manifest:
        requests=row['HostConfig']['DeviceRequests']
        assert len(requests)==1 and requests[0]['DeviceIDs']==[manifest['gpu_uuid']]
        assert requests[0]['Count']==0 and ['gpu'] in requests[0]['Capabilities']
    return row
def check_gpu():
    uuid=manifest['gpu_uuid']
    output=subprocess.run(['/usr/bin/nvidia-smi','--id='+uuid,'-q','-x'],capture_output=True,text=True,timeout=2,check=True).stdout
    gpus=ET.fromstring(output).findall('gpu')
    assert len(gpus)==1 and gpus[0].findtext('uuid')==uuid
    used_text=gpus[0].findtext('fb_memory_usage/used')
    assert re.fullmatch(r'[0-9]+ MiB',used_text)
    used=int(used_text.split()[0])
    processes=[]
    assert gpus[0].find('processes') is not None
    for info in gpus[0].findall('processes/process_info'):
        pid=info.findtext('pid')
        memory=info.findtext('used_memory')
        assert pid.isdigit() and re.fullmatch(r'[0-9]+ MiB',memory)
        cgroup=pathlib.Path('/proc/'+pid+'/cgroup').read_text()
        assert ('docker-'+manifest['cid']+'.scope') in cgroup or ('/docker/'+manifest['cid']) in cgroup
        processes.append({'pid':int(pid),'used_mib':int(memory.split()[0]),'type':info.findtext('type')})
    event('gpu_sample',used_mib=used,processes=processes)
    assert used <= manifest['gpu_stop_threshold_mib']
try:
    row=inspect()
    assert not row['State']['Running']
    if 'gpu_uuid' in manifest:
        check_gpu()
    event('ready',cid=row['Id'],pid=__import__('os').getpid(),deadline=manifest['deadline'])
    if 'gpu_uuid' in manifest:
        assert time.time()<manifest['deadline'] and time.monotonic()<monotonic_deadline
        start_attempted=True
        subprocess.run(base+['start',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
        event('started',cid=manifest['cid'])
    while time.time()<manifest['deadline'] and time.monotonic()<monotonic_deadline:
        inspect()
        if 'gpu_uuid' in manifest:
            check_gpu()
        time.sleep(min(0.5,max(0,min(manifest['deadline']-time.time(),monotonic_deadline-time.monotonic()))))
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
    cleanup_deadline=monotonic_deadline+25 if start_attempted else time.monotonic()
    while True:
        try:
            row=inspect()
            if row['State']['Running']:
                subprocess.run(base+['kill',manifest['cid']],capture_output=True,text=True,timeout=4,check=True)
                event('failure_cleanup_killed',cid=manifest['cid'])
        except BaseException as cleanup_error:
            event('failure_cleanup_failed',error_type=type(cleanup_error).__name__,error=str(cleanup_error))
        if time.monotonic()>=cleanup_deadline:
            break
        time.sleep(0.5)
    raise
