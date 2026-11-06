"""Audit frozen C23 host-monotonic events; never operates model/runtime assets."""
import csv
import hashlib
import json
import math
from pathlib import Path
R = Path(__file__).resolve().parent
inputs = json.loads((R/'raw-event-inputs.json').read_text())
source = json.loads((R/'source-clock-evidence.json').read_text())
assert len(inputs) == 26
assert 'pool_loaded = False  # Fresh helper process' in source['formal_trials.py']['text']
assert 'for number in (1,2,0):' in source['formal_trials.py']['text']
ledger_bytes=(R/'inputs/formal-ledger.json').read_bytes()
assert hashlib.sha256(ledger_bytes).hexdigest()=='76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b'
canonical={x['attempt_id']:x for x in json.loads(ledger_bytes)['rows']}
assert len({x['result']['attempt_id'] for x in inputs})==26
state, rows = {}, []
for item in sorted(inputs, key=lambda x: x['ledger']['order']):
    ledger, result, phases = item['ledger'], item['result'], item['phases']
    name, run = result['attempt_id'], result['execution_run_id']
    assert ledger==canonical[name]
    assert result==json.loads((R/'inputs'/name/'result.json').read_text())
    assert result['protocol_revision']=='v13-canonical23-qualified-disk-jit-seed'
    assert result['code_commit'] == 'f176e05607d04999ab28e8ea8cd49b41270cb528'
    serialized=json.dumps(phases['raw_events'],sort_keys=True,separators=(',',':')).encode()
    assert hashlib.sha256(serialized).hexdigest()==phases['raw_event_sha256']
    control = phases['control_events']
    tokens = [x for x in phases['raw_events'] if x['name']=='backend_first_token_observed']
    assert len(tokens)==1 and tokens[0]['request_id']==name+'-business_arithmetic'
    token, route = tokens[0]['monotonic_ns'], control['router_instance_published']['monotonic_ns']
    stamps = [result[k] for k in ('preparation_begin_ns','preparation_complete_ns','startup_command_ns','register_complete_ns','business_trigger_ns')]+[route,token,result['correct_business_response_ns']]
    assert all(type(x) is int for x in stamps) and stamps==sorted(stamps)
    assert phases['begin_ns']==result['startup_command_ns'] and phases['end_ns']==result['correct_business_response_ns']
    assert token==result['first_token_observed_ns']==phases['first_token_observed_ns']
    begin = control['backend_engine_create_begin']
    assert begin['boot_id']==ledger['boot_id']==phases['backend_process_creation']['boot_id']==source[run]['run-manifest.json']['boot_id']
    assert abs(begin['boottime_minus_monotonic_ns'])<1000000
    worker = source[run]['formal13-identity.json']['container_id'][:12]
    head = source[run]['headformal13-identity.json']['container_id'][:12]
    assert tokens[0]['container_hostname']==begin['container_hostname']==worker
    assert control['router_instance_published']['container_hostname']==head
    assert tokens[0]['pid']==begin['pid']
    m = result['metrics']; start = result['startup_command_ns']
    measured = dict(preparation_s=(stamps[1]-stamps[0])/1e9,command_to_route_publication_s=(route-start)/1e9,command_to_first_token_s=(token-start)/1e9,command_to_correct_business_response_s=(stamps[-1]-start)/1e9,trigger_to_first_token_s=(token-result['business_trigger_ns'])/1e9,trigger_to_correct_business_response_s=(stamps[-1]-result['business_trigger_ns'])/1e9)
    assert all(math.isclose(v,m[k],abs_tol=1e-9) for k,v in measured.items())
    old = state.get(run, False)
    state[run] = result['condition'].startswith('store_')
    row = dict(attempt_id=name,order=ledger['order'],block=result['block'],condition=result['condition'],execution_run_id=run,prior_pool_loaded=old,unload_inside_preparation=old,preparation_s=measured['preparation_s'],preparation_to_startup_gap_s=(start-stamps[1])/1e9,command_to_route_s=(route-start)/1e9,route_to_backend_first_token_s=(token-route)/1e9,backend_first_token_to_correct_http_s=(stamps[-1]-token)/1e9,command_to_correct_arithmetic_http_s=(stamps[-1]-start)/1e9,trigger_to_correct_arithmetic_http_s=measured['trigger_to_correct_business_response_s'],phase_path=item['phase_path'],phase_sha256=item['phase_sha256'])
    for phase_name, phase in phases['phases'].items():
        assert set(phase['ranks'])==set(map(str,range(8)))
        ranks=list(phase['ranks'].values())
        assert all(x['end_ns']>=x['begin_ns'] for x in ranks)
        span=(max(x['end_ns'] for x in ranks)-min(x['begin_ns'] for x in ranks))/1e9
        assert math.isclose(span,phase['min_begin_to_max_end_seconds'],abs_tol=1e-9)
        assert math.isclose(span,m['phase_'+phase_name+'_span_s'],abs_tol=1e-9)
        row['phase_'+phase_name+'_span_s']=span
    assert math.isclose(sum(row[k] for k in ('command_to_route_s','route_to_backend_first_token_s','backend_first_token_to_correct_http_s')),row['command_to_correct_arithmetic_http_s'],abs_tol=1e-9)
    rows.append(row)
with (R/'event-intervals.csv').open('w',newline='',encoding='utf-8') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
receipt=dict(event_order_gate='PASS',recorded_same_host_monotonic_binding='PASS',clock_basis='Frozen formal_trials time.monotonic_ns; phases monotonic_ns; ledger/run/backend boot_id equality; recorded worker/head container identity; backend BOOTTIME-MONOTONIC absolute offset <1ms. No wall/client clocks subtracted.',n=26,phase_rank_coverage='all 8 ranks per recorded phase; cross-rank min(begin) to max(end), non-additive',arithmetic_is_first_business=True,request_order='arithmetic -> translation -> fixed32',preparation_unload_count=sum(x['unload_inside_preparation'] for x in rows),preparation_unload_ids=[x['attempt_id'] for x in rows if x['unload_inside_preparation']],preparation_no_unload_ids=[x['attempt_id'] for x in rows if not x['unload_inside_preparation']],preparation_gap_range_s=[min(x['preparation_to_startup_gap_s'] for x in rows),max(x['preparation_to_startup_gap_s'] for x in rows)],limits=['Host/backend first-token observation is not client streaming TTFT','HTTP response completion is not last-token timestamp or pure generation','phase spans can overlap across ranks; never summed into fixed overhead','pool state follows actual successful execution order, reset false at each fresh formal worker','No new workload or cache reads'],input_sha256={n:hashlib.sha256((R/n).read_bytes()).hexdigest() for n in ('raw-event-inputs.json','source-clock-evidence.json')},script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(R/'event-gate.json').write_text(json.dumps(receipt,indent=2)+'\n')
print(json.dumps(receipt))
