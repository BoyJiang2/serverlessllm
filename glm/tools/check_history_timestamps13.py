"""Recompute historical recorded event differences; no new runtime claim."""
from pathlib import Path
import json,math,hashlib
root=Path(__file__).resolve().parent.parent
expected={r['attempt_id']:r for r in json.loads((root/'evidence/g0/handoff-current-audit-v2.json').read_text())['attempts']}
rows=[]
for p in sorted((root/'evidence/formal-history').glob('*--result.json')):
 data=p.read_bytes(); d=json.loads(data)
 assert hashlib.sha256(data).hexdigest()==expected[d['attempt_id']]['result_sha256']
 m=d.get('metrics',{}); checks=[]
 fields={'command_to_first_token_s':('first_token_observed_ns','startup_command_ns'),'command_to_correct_business_response_s':('correct_business_response_ns','startup_command_ns'),'trigger_to_first_token_s':('first_token_observed_ns','business_trigger_ns'),'trigger_to_correct_business_response_s':('correct_business_response_ns','business_trigger_ns'),'preparation_s':('preparation_complete_ns','preparation_begin_ns')}
 for metric,(end,begin) in fields.items():
  if metric in m:
   actual=(d[end]-d[begin])/1e9
   assert math.isclose(actual,m[metric],abs_tol=1e-9,rel_tol=0),p.name+' '+metric
   checks.append(dict(metric=metric,recomputed=actual,stored=m[metric],exact_within_1ns=True))
 rows.append(dict(path=p.relative_to(root).as_posix(),success=d['success'],source_sha256=hashlib.sha256(data).hexdigest(),checks=checks,scope='recorded event-to-event metrics only; phase intervals need raw phase events; failed values excluded from successful latency'))
output=dict(status='historical arithmetic checks passed',files=rows,total_metric_checks=sum(len(r['checks']) for r in rows),fresh_runtime=False,R3_approved=False)
(root/'evidence/formal-history/timestamp-arithmetic-check.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n')
print('Historical event difference checks:',output['total_metric_checks'])
