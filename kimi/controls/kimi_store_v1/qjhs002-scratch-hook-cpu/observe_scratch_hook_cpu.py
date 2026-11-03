import datetime,json,pathlib,subprocess,time
base=pathlib.Path('/data/brick1/boyjiang/kimi-k2.6/p0/kimi-scratch-hook-cpu-20261104-0136')
unit=base.name+'.service'; group=pathlib.Path('/sys/fs/cgroup/system.slice')/unit
ready=json.loads((base/'ready.json').read_text())
assert ready['unit']==unit
rows={}
parent=ready['parent'];rows[str(parent['pid'])+':'+str(parent['startticks'])]=parent
last=base/'last.json'
if last.exists():
 for key,value in json.loads(last.read_text()).get('owned_processes',{}).items():rows[key]=value['last']
def snap():
 states={}
 for key,row in rows.items():
  try:
   fields=pathlib.Path('/proc',str(row['pid']),'stat').read_text().rsplit(')',1)[1].split()
   states[key]={'same_identity_present':int(fields[19])==row['startticks'],'state':fields[0]}
  except FileNotFoundError:states[key]={'same_identity_present':False}
 if group.exists():
  events=dict(x.split() for x in (group/'cgroup.events').read_text().splitlines());empty=events.get('populated')=='0'
 else:events='removed';empty=True
 result=subprocess.check_output(['systemctl','show',unit,'--property=LoadState,ActiveState,MainPID,Result,ExecMainCode,ExecMainStatus'],text=True,timeout=4)
 props=dict(line.split('=',1) for line in result.splitlines())
 return states,empty,events,props
deadline=time.monotonic()+15
while True:
 states,empty,events,props=snap()
 if all(not s['same_identity_present'] for s in states.values()) and empty and props.get('MainPID')=='0':break
 if time.monotonic()>=deadline:raise RuntimeError('owned processes/cgroup did not release')
 time.sleep(1)
start=time.monotonic()
for i in range(13):
 if i:time.sleep(5)
 states,empty,events,props=snap()
 assert all(not s['same_identity_present'] for s in states.values()) and empty and props.get('MainPID')=='0'
 print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'elapsed_seconds':time.monotonic()-start,'unit':unit,'identities':states,'cgroup':events,'unit_properties':props}),flush=True)
assert time.monotonic()-start>=60
