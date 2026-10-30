"""Reproduce two R1c budget defects with synthetic clocks and no launched child."""
import ast,datetime,hashlib,io,json,pathlib,runpy,sys,types
from unittest.mock import patch
repo=pathlib.Path(__file__).resolve().parent.parent; source=repo/'controls/candidate13c'; sys.path.insert(0,str(source))
from budget_control13 import require_budget
module=ast.parse((source/'bootstrap_control13.py').read_text())
assignment=next(v for v in module.body if isinstance(v,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='budget' for t in v.targets))
assertion=next(v for v in module.body if isinstance(v,ast.Assert) and isinstance(v.test,ast.Compare) and isinstance(v.test.left,ast.Name) and v.test.left.id=='budget')
code=compile(ast.Module(body=[assignment,assertion],type_ignores=[]),'actual bootstrap admission nodes','exec')
thresholds=[]
for remaining in (3599,3600,3720,3721):
 try: require_budget('service13a',remaining); outer=True
 except RuntimeError: outer=False
 try: exec(code,{'deadline':remaining,'time':types.SimpleNamespace(time=lambda:0),'validate_model':True}); inner=True
 except AssertionError: inner=False
 thresholds.append(dict(remaining_seconds=remaining,outer_accepts=outer,bootstrap_accepts=inner))
clock=[0]; records=[]
class Child:
 pid=12345
 returncode=None
 def poll(self):
  if clock[0]>330: self.returncode=0
  return self.returncode
class Sink(io.StringIO):
 def close(self):
  if not self.closed: records.append(json.loads(self.getvalue()))
  super().close()
def read(path,*args,**kwargs):
 return json.dumps(dict(run_id='synthetic',observers_ok=True,last_sample_ns=int(clock[0]*1e9)))
window=types.ModuleType('resource_window_control13'); window.load_window=lambda root:dict(cleanup_deadline='1970-01-02T00:00:00+00:00')
with patch.dict(sys.modules,{'resource_window_control13':window}),patch.object(sys,'argv',['supervise_control13.py','ray','ray13a']),patch('time.time',return_value=0),patch('time.monotonic_ns',side_effect=lambda:int(clock[0]*1e9)),patch('time.sleep',side_effect=lambda seconds:clock.__setitem__(0,331)),patch('subprocess.Popen',return_value=Child()),patch.object(pathlib.Path,'read_text',read),patch.object(pathlib.Path,'open',lambda *args,**kwargs:Sink()):
 runpy.run_path(str(source/'supervise_control13.py'),run_name='__main__')
record=records[0]
assert record['status']=='passed including cleanup' and record['end_ns']-record['begin_ns']>330*10**9
output=dict(code_commit='b5b48807fe04251777c324d52f9e015c5d05aad5',scope='CPU synthetic execution of actual source; Popen/clock/monitor/window/file writes mocked; no child process, Ray, Docker or SSH',threshold_cases=thresholds,supervisor_counterexample=record,defects_confirmed=['inconsistent admission','late child exit recorded passed beyond stated total budget'],runtime_G1=False)
p=repo/'reviews/supplementary/r1c-budget-counterexamples.json';p.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n')
print(json.dumps(output,indent=2))
