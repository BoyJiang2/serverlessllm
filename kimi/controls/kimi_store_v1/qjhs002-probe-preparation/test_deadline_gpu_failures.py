import contextlib,io,json,pathlib,runpy,subprocess,sys,tempfile,time
from unittest.mock import patch

script=pathlib.Path(__file__).with_name('exact_container_deadline.py')
cid='a'*64
for case in ('over_threshold','foreign_pid','query_timeout','late_start_after_timeout'):
    calls=[]
    clock=[time.time(),time.monotonic()]
    killed=[False]
    manifest={'cid':cid,'created':'fixture','image':'sha256:fixture','endpoint':'unix:///var/run/docker.sock','nonce':'fixture','deadline':time.time()+30,'gpu_uuid':'GPU-fixture','gpu_stop_threshold_mib':2048}
    def run(argv,**kwargs):
        calls.append(argv)
        if 'inspect' in argv:
            count=sum('inspect' in a for a in calls)
            running=count>1
            if case=='late_start_after_timeout':running=count>2 and not killed[0]
            row={'Id':cid,'Created':'fixture','Image':'sha256:fixture','Config':{'Labels':{'sllm.kimi.run':'fixture'}},'HostConfig':{'RestartPolicy':{'Name':'no'},'DeviceRequests':[{'DeviceIDs':['GPU-fixture'],'Count':0,'Capabilities':[['gpu']]}]},'State':{'Running':running}}
            return subprocess.CompletedProcess(argv,0,json.dumps([row]),'')
        if 'kill' in argv:
            killed[0]=True
            return subprocess.CompletedProcess(argv,0,'','')
        if 'start' in argv:raise subprocess.TimeoutExpired(argv,4)
        if case=='query_timeout':raise subprocess.TimeoutExpired(argv,2)
        used='3000' if case=='over_threshold' else '100'
        process='' if case in ('over_threshold','late_start_after_timeout') else '<process_info><pid>4242</pid><type>G</type><used_memory>100 MiB</used_memory></process_info>'
        value='<nvidia_smi_log><gpu><uuid>GPU-fixture</uuid><fb_memory_usage><used>'+used+' MiB</used></fb_memory_usage><processes>'+process+'</processes></gpu></nvidia_smi_log>'
        return subprocess.CompletedProcess(argv,0,value,'')
    original_read=pathlib.Path.read_text
    def read(path,*args,**kwargs):
        if str(path).replace('\\','/')=='/proc/4242/cgroup':return '0::/system.slice/other.scope'
        return original_read(path,*args,**kwargs)
    with tempfile.TemporaryDirectory() as directory:
        path=pathlib.Path(directory)/'fixture.json';path.write_text(json.dumps(manifest))
        output=io.StringIO()
        def sleep(seconds):
            clock[0]+=seconds;clock[1]+=seconds
        with patch.object(sys,'argv',[str(script),str(path)]),patch('subprocess.run',run),patch.object(pathlib.Path,'read_text',read),patch('time.time',lambda:clock[0]),patch('time.monotonic',lambda:clock[1]),patch('time.sleep',sleep),contextlib.redirect_stdout(output):
            try:runpy.run_path(str(script),run_name='__main__')
            except (AssertionError,subprocess.TimeoutExpired):pass
            else:raise AssertionError('failure was swallowed: '+case)
        events=[json.loads(line)['event'] for line in output.getvalue().splitlines()]
        assert 'failed' in events and 'failure_cleanup_killed' in events,(case,events)
        kills=[a for a in calls if 'kill' in a]
        assert len(kills)==1 and kills[0][-1]==cid
        assert any('start' in a for a in calls)==(case=='late_start_after_timeout')
        if case=='late_start_after_timeout':
            assert clock[0]>=manifest['deadline']+25
        print(case+': PASS exact CID cleanup, original error retained')
