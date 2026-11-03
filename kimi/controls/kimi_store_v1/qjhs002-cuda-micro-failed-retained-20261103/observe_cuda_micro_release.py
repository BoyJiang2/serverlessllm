import datetime,json,pathlib,subprocess,time,xml.etree.ElementTree as ET
cid='c6ae802f821246c41cf8982688ee6e68e412cdba9182cc11a1ec85ee64c056a7'
uuid='GPU-736d7f37-f647-b82d-ab11-693753ac6822'
first=time.monotonic()
for number in range(13):
    if number:time.sleep(5)
    raw=subprocess.check_output(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect',cid],text=True,timeout=4)
    row=json.loads(raw)[0]
    assert row['Id']==cid and not row['State']['Running'] and row['State']['Pid']==0 and row['RestartCount']==0
    xml=subprocess.check_output(['/usr/bin/nvidia-smi','--id='+uuid,'-q','-x'],text=True,timeout=2)
    gpu=ET.fromstring(xml).find('gpu')
    assert gpu.findtext('uuid')==uuid and gpu.findtext('fb_memory_usage/used')=='0 MiB'
    assert not gpu.findall('processes/process_info')
    assert all(not pathlib.Path('/proc/'+str(pid)).exists() for pid in (1368008,1368025,1369571))
    print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-first,'cid':cid,'state':row['State']['Status'],'running':False,'pid':0,'restart_count':0,'gpu_uuid':uuid,'used_mib':0,'gpu_processes':[],'owned_runtime_pids_absent':True}),flush=True)
assert time.monotonic()-first>=60
