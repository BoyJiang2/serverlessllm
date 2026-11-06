import csv,hashlib,itertools,json,math,random,statistics
from pathlib import Path
R=Path(__file__).resolve().parent
L=R/'inputs/formal-ledger.json';raw=L.read_bytes()
assert hashlib.sha256(raw).hexdigest()=='76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b'
rows=json.loads(raw)['rows'];assert len(rows)==40
C='v13-canonical23-qualified-disk-jit-seed'
conditions=['safetensors_disk_cold','safetensors_disk_hot','store_disk_cold','store_pool_hit']
idx={};c23=[];inputs=[]
for row in rows:
 p=R/'inputs'/row['attempt_id']/'result.json';b=p.read_bytes();h=hashlib.sha256(b).hexdigest();assert h==row['result_sha256']
 x=json.loads(b);assert x['attempt_id']==row['attempt_id'] and x['status']==row['status'];inputs.append(dict(attempt_id=row['attempt_id'],authority_path=row['result_path'],sha256=h))
 if row.get('protocol_revision')==C:
  assert x['protocol_revision']==C and x['condition']==row['condition'] and x['block']==row['block'];idx[x['block'],x['condition']]=x;c23.append(x)
assert len(c23)==26 and sum(x['status']=='success' for x in rows)==35
blocks=[b for b in range(1,11) if all((b,c) in idx for c in conditions)];assert blocks==list(range(5,11))
with (R/'all40-matrix.csv').open('w',newline='') as f:
 names=['order','attempt_id','block','condition','status','protocol_revision','result_path','result_sha256'];w=csv.DictWriter(f,fieldnames=names,extrasaction='ignore');w.writeheader();w.writerows(rows)
with (R/'c23-block-matrix.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['block']+conditions)
 for b in range(1,11):w.writerow([b]+[idx[b,c]['attempt_id'] if (b,c) in idx else 'NOT_C23' for c in conditions])
original=json.loads((R/'original/paired-stats.json').read_text());comparisons=[('store_disk_cold','safetensors_disk_cold'),('store_pool_hit','safetensors_disk_cold'),('store_pool_hit','store_disk_cold'),('store_disk_cold','safetensors_disk_hot')]
def stats(a,b,bs,prep):
 def value(c,k):
  m=idx[k,c]['metrics'];return m['command_to_correct_business_response_s']+(m['preparation_s'] if prep else 0)
 av=[value(a,k) for k in bs];bv=[value(b,k) for k in bs];d=[x-y for x,y in zip(av,bv)];n=len(d);observed=statistics.fmean(d)
 rng=random.Random(20261024);boot=sorted(sum(rng.choice(d) for _ in d)/n for j in range(10000))
 p=sum(abs(sum(s*x for s,x in zip(signs,d))/n)>=abs(observed)-1e-12 for signs in itertools.product((-1,1),repeat=n))/(2**n)
 return dict(blocks=bs,n=n,mean_diff=observed,ci95_low=boot[249],ci95_high=boot[9749],p_raw=p,ratio_of_paired_means=statistics.fmean(av)/statistics.fmean(bv),cohens_dz=observed/statistics.stdev(d),diffs=d)
def holm(ps):
 order=sorted(range(len(ps)),key=lambda k:ps[k]);out=[0]*len(ps);running=0
 for rank,i in enumerate(order):running=max(running,min(1,(len(ps)-rank)*ps[i]));out[i]=running
 return out
out={};mismatches=[]
for prep in (False,True):
 window='including_preparation' if prep else 'business_only';results=[stats(a,b,blocks,prep) for a,b in comparisons]
 for i,(x,p) in enumerate(zip(results,holm([x['p_raw'] for x in results]))):
  x['p_holm']=p;ref=original['contrasts'][i]['windows'][window]
  for key in ('mean_diff','ci95_low','ci95_high','p_raw','p_holm','cohens_dz','ratio_of_paired_means'):
   if not math.isclose(x[key],ref[key],abs_tol=1e-9,rel_tol=1e-12):mismatches.append([window,i+1,key,x[key],ref[key]])
 out[window]=results
sensitivity={}
for rule,bs in [('n6_complete_block',blocks),('n7_maximal_pair',blocks+[4])]:
 sensitivity[rule]={}
 for prep in (False,True):
  window='including_preparation' if prep else 'business_only';x=stats('store_pool_hit','store_disk_cold',bs,prep);sensitivity[rule][window]=x;ref=original['contrast3_pairing_sensitivity']['rules'][rule][window]
  for key,refkey in [('mean_diff','mean'),('ci95_low','ci95_low'),('ci95_high','ci95_high')]:
   if not math.isclose(x[key],ref[refkey],abs_tol=1e-9):mismatches.append([rule,window,key,x[key],ref[refkey]])
receipt=dict(scope='Independent numerical/input gate only; raw event clocks, routing and preparation-state audit pending',ledger_sha256=hashlib.sha256(raw).hexdigest(),input_sha_verified=inputs,counts=dict(success=35,failed=5,c23=26),complete_blocks=blocks,block4_exclusion='SF conditions are C21, Store conditions C23; independent of timings',method=dict(seed=20261024,B=10000,bootstrap='paired-block percentile order statistics floor(p*(B-1)); restart RNG for each statistic',test='two-sided exact |mean paired difference|, all signs, tie tolerance1e-12',family='four contrasts separately per cost window',interval='marginal, not simultaneous'),contrasts=out,sensitivity=sensitivity,mismatches=mismatches,numerical_gate='PASS' if not mismatches else 'FAIL',event_gate='NOT_YET_VERIFIED',script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(R/'independent-audit.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(dict(numerical_gate=receipt['numerical_gate'],mismatches=mismatches,p_values={k:[(x['p_raw'],x['p_holm']) for x in v] for k,v in out.items()})))
