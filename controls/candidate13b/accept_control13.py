import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""Post-control acceptance only; no inference or formal attempts."""
import hashlib,json,pathlib
root=pathlib.Path(__file__).resolve().parent
def load(name): return json.loads((root/name).read_text())
ends=[]
for label in ('ray13a','ray13b','service13a'):
    result=load(label+'-supervisor-result.json')
    assert result['status']=='passed including cleanup'
    ends.append(result['end_ns'])
for label in ('ray13a','ray13b'):
    assert load('ray-only-'+label+'/result.json')['status']=='passed'
for fmt in ('serverless_llm','safetensors'):
    proof=load('service-bootstrap-service13a/'+fmt+'/model-result.json')
    assert proof['status']=='A4 native HTTP lifecycle checks passed' and proof['cycles']==2
    assert len(proof['rank_coverage'])==16 and all(r['weight_scale_derived_references']==5814 and r['dsa_workspace_references_excluded']==234 for r in proof['rank_coverage'])
    assert {(r['cycle'],r['rank']) for r in proof['rank_coverage']}=={(cycle,rank) for cycle in (1,2) for rank in range(8)}
assert load('service-bootstrap-service13a/pool-ready.json')['pool_bytes']==768799145984
for stage in ('ray13a','ray13b','allstopped'):
    assert len(load('release60-'+stage+'.json'))==61
lines=(root/'resource-monitor-v12.jsonl').read_bytes().splitlines()
samples=[]
partial=None
for number,line in enumerate(lines):
    try: samples.append(json.loads(line))
    except (ValueError,UnicodeDecodeError):
        assert number==len(lines)-1, 'non-final monitor corruption'
        partial=dict(bytes=len(line),sha256=hashlib.sha256(line).hexdigest(),reason='possible final interrupted write at controlled shutdown; full raw file retained')
assert len(samples)>2
gaps=[(b['monotonic_ns']-a['monotonic_ns'])/1e9 for a,b in zip(samples,samples[1:])]
assert max(gaps)<=5, 'monitor cadence exceeded frozen5s budget'
starts=[load('head'+label+'-dispatch.json')['dispatch_monotonic_ns'] for label in ('ray13a','ray13b','service13a')]
assert samples[0]['monotonic_ns']<=min(starts) and samples[-1]['monotonic_ns']>=max(ends)
asset=pathlib.Path(load('run-manifest.json')['parent_asset_root'])
for row in load('evidence/handoff-current-audit-v2.json')['attempts']:
    directory=asset/'formal-trials'/row['attempt_id']
    if row['status']=='unstarted': assert not directory.exists(), 'formal ID unexpectedly consumed'
    else: assert hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()==row['result_sha256'], 'terminal result changed'
receipt=dict(status='G1 acceptance passed; no formal samples',monitor_samples=len(samples),max_monitor_gap_seconds=max(gaps),partial_final_monitor_row=partial,
    pool_initializations=1,ray_only_controls=2,native_lifecycles_per_format=2,formats=['serverless_llm','safetensors'],
    scope='current conditions stable in this bounded control; historical exit137 cause remains unknown')
with (root/'G1-acceptance.json').open('x') as stream: json.dump(receipt,stream,indent=2)
print(json.dumps(receipt,indent=2))
