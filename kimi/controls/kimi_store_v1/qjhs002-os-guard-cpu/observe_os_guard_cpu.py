"""Observe one known transient unit and exact recorded CPU process identities."""
import datetime,json,pathlib,subprocess,time
base=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/os-guard-cpu-20261104')
ready=json.loads((base/'ready.json').read_text())
unit=ready['unit'];group=pathlib.Path(ready['unit_cgroup'])
assert unit=='kimi-os-guard-cpu-20261104.service'
assert str(group)=='/sys/fs/cgroup/system.slice/'+unit
def snapshot():
    states={}
    for role,row in ready['processes'].items():
        path=pathlib.Path('/proc')/str(row['pid'])/'stat'
        try:
            fields=path.read_text().split()
            states[role]={'pid':row['pid'],'same_identity_present':int(fields[21])==row['startticks'],'state':fields[2]}
        except FileNotFoundError:states[role]={'pid':row['pid'],'same_identity_present':False}
    if group.exists():
        events=dict(line.split() for line in (group/'cgroup.events').read_text().splitlines())
        empty=events.get('populated')=='0';cgroup_state=events
    else:empty=True;cgroup_state='removed'
    result=subprocess.check_output(['systemctl','show',unit,'--property=ActiveState,MainPID,Result,ExecMainCode,ExecMainStatus'],text=True,timeout=4)
    properties=dict(line.split('=',1) for line in result.splitlines())
    return states,empty,cgroup_state,properties
deadline=time.monotonic()+25
while True:
    states,empty,cgroup_state,properties=snapshot()
    if all(not row['same_identity_present'] for row in states.values()) and empty and properties.get('MainPID')=='0':break
    if time.monotonic()>=deadline:raise RuntimeError('exact owned unit subtree did not clear')
    time.sleep(1)
assert properties.get('Result')=='timeout',properties
assert properties.get('ExecMainStatus')=='9' and properties.get('ExecMainCode')=='2',properties
start=time.monotonic()
for number in range(13):
    if number:time.sleep(5)
    states,empty,cgroup_state,properties=snapshot()
    assert all(not row['same_identity_present'] for row in states.values()) and empty
    assert properties.get('Result')=='timeout' and properties.get('MainPID')=='0'
    print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-start,'unit':unit,'identities':states,'cgroup':cgroup_state,'unit_properties':properties}),flush=True)
assert time.monotonic()-start>=60
