from pathlib import Path
import json,hashlib,collections,statistics,math
root=Path(__file__).resolve().parent.parent; audit=json.loads((root/'evidence/g0/handoff-current-audit-v2.json').read_text()); prereg=root/'protocol/measurement-preregistration.json'
assert hashlib.sha256(prereg.read_bytes()).hexdigest()=='90d4c91bf8b0c9495e0cb95376a55f84d17351308541414d58dd650d8e2dd37c'
rows=[]; groups=collections.defaultdict(list)
for state in audit['attempts']:
 row=dict(state)
 if state['status']!='unstarted':
  p=root/'evidence/formal-history'/(state['attempt_id']+'--result.json'); raw=p.read_bytes(); assert hashlib.sha256(raw).hexdigest()==state['result_sha256']
  result=json.loads(raw); assert result['attempt_id']==state['attempt_id']
  row.update(raw_revision=result.get('protocol_revision'),revision_provenance='raw result if present; otherwise G0 historical attribution',metrics=result.get('metrics'),latency_eligible=result.get('success') is True,error=result.get('error'),error_type=result.get('error_type'),right_censored=result.get('right_censored'))
  groups[(state['protocol_revision'],state['condition'])].append(row)
 rows.append(row)
summary=[]
for (revision,condition),items in sorted(groups.items()):
 values=collections.defaultdict(list)
 for row in items:
  if row['latency_eligible']:
   for key,value in (row['metrics'] or {}).items(): values[key].append(dict(attempt_id=row['attempt_id'],value=value))
 metrics={}
 for key,raw in values.items():
  v=sorted(r['value'] for r in raw); metrics[key]=dict(raw=raw,n=len(v),mean=statistics.mean(v),median=statistics.median(v),p90_nearest_rank=v[math.ceil(.9*len(v))-1],uncertainty=None,uncertainty_reason='partial historical stratum; paired-block CI deferred to reviewed R3; no numeric interval for missing pairs')
 summary.append(dict(protocol_revision=revision,condition=condition,terminal=len(items),successful=sum(i['latency_eligible'] for i in items),metrics=metrics))
output=dict(scope='interim historical descriptive recomputation only; no revision pooling/no new results/no R3 approval',all_ids=rows,strata=summary,terminal=9,successful=5,failed=4,unstarted=31,final_success_rate=None,final_success_rate_reason='planned10 per condition incomplete; pending are not failures')
p=root/'evidence/formal-history/interim-recomputation.json';p.write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n');print(dict(strata=len(summary),terminal=9,success=5,failed=4,unstarted=31))
