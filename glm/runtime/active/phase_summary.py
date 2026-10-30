"""Extract host phase boundaries from one completed native service request."""
import hashlib
import json
import pathlib
import sys

PHASES = ('device_setup_including_communication', 'communication_groups',
          'weights_pipeline_including_allocation', 'weight_ingestion',
          'quantization_postprocess', 'kv_cache_allocation', 'compile_graph_and_warmup')


def summarize(directory, begin_ns, end_ns, request_id):
    rows = []
    for path in sorted(pathlib.Path(directory).glob('*.jsonl')):
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if begin_ns <= row['monotonic_ns'] <= end_ns:
                rows.append(row)
    rows.sort(key=lambda row: row['monotonic_ns'])
    assert rows, 'no phase events in request window'
    ranks = {}
    for row in rows:
        if row.get('rank') is not None:
            key = (row['container_hostname'], row['pid'])
            rank = row['rank']
            assert key not in ranks or ranks[key] == rank
            ranks[key] = rank
    phases = {}
    for phase in PHASES:
        spans = {}
        for rank in range(8):
            events = [row for row in rows if row['name'] in (phase+'_begin', phase+'_end')
                      and ranks.get((row['container_hostname'], row['pid'])) == rank]
            assert len(events) == 2 and [row['name'] for row in events] == [phase+'_begin', phase+'_end'], (phase, rank)
            first, last = [row['monotonic_ns'] for row in events]
            spans[str(rank)] = {'begin_ns':first, 'end_ns':last, 'host_seconds':(last-first)/1e9}
        phases[phase] = {'ranks':spans,
            'min_begin_to_max_end_seconds':(max(row['end_ns'] for row in spans.values())-
                                          min(row['begin_ns'] for row in spans.values()))/1e9}
    tokens = [row for row in rows if row['name'] == 'backend_first_token_observed'
              and row.get('request_id') == request_id]
    assert len(tokens) == 1, 'first-token event missing or duplicated'
    control = {}
    for name in ('backend_engine_create_begin','backend_engine_ready','router_instance_published'):
        matches = [row for row in rows if row['name'] == name]
        assert len(matches) == 1, name
        control[name] = matches[0]
    creation = control['backend_engine_create_begin']
    process = None
    if 'process_start_ticks' in creation:
        start_ns = int(creation['process_start_ticks']*10**9/creation['clock_ticks_per_second'])-creation['boottime_minus_monotonic_ns']
        process = {'start_monotonic_ns_estimate':start_ns,
                   'clock_resolution_seconds':1/creation['clock_ticks_per_second'],
                   'start_to_engine_construct_begin_seconds':(creation['monotonic_ns']-start_ns)/1e9,
                   'boot_id':creation['boot_id'],
                   'scope':'backend actor process creation from proc stat; includes interpreter/import/actor scheduling; may predate register if Ray reuses a worker'}
    serialized = json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()
    return {'scope':'host function boundaries; nested/overlapping spans are not additive; no precise CUDA kernel timings',
            'request_id':request_id, 'begin_ns':begin_ns, 'end_ns':end_ns,
            'first_token_observed_ns':tokens[0]['monotonic_ns'],
            'origin_to_first_token_observed_seconds':(tokens[0]['monotonic_ns']-begin_ns)/1e9,
            'origin_to_response_window_end_seconds':(end_ns-begin_ns)/1e9,
            'phases':phases, 'control_events':control,'backend_process_creation':process,
            'raw_event_sha256':hashlib.sha256(serialized).hexdigest(), 'raw_events':rows}


def require_process(summary, boot):
    process=summary['backend_process_creation']
    if not process or process['boot_id']!=boot:
        raise ValueError('backend process creation and current boot required')


def verify_normal_phases(root, model_path, proof, boot, begin_ns, end_ns):
    """Recompute each normal cycle using the same strict formal extractor."""
    evidence=proof.get('phase_evidence',[])
    if len(evidence)!=2 or [row['cycle'] for row in evidence]!=[1,2]:
        raise ValueError('two normal cycle phase proofs required')
    events=[json.loads(line) for line in (model_path.parent/'model-events.jsonl').read_text().splitlines()]
    previous_end=begin_ns-1
    for row in evidence:
        if row['path']!=f"cycle{row['cycle']}-phases.json":
            raise ValueError('normal phase path differs')
        path=model_path.parent/row['path']
        data=path.read_bytes()
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            raise ValueError('normal phase bytes changed')
        summary=json.loads(data)
        begins=[e for e in events if e['name']=='native_register_begin' and e['cycle']==row['cycle']]
        ends=[e for e in events if e['name']=='native_business_request_complete_correct' and e['cycle']==row['cycle'] and e['case']=='business_arithmetic']
        if len(begins)!=1 or len(ends)!=1:
            raise ValueError('normal phase lifecycle boundaries missing or duplicated')
        begin,end=begins[0]['monotonic_ns'],ends[0]['response_received_ns']
        if not previous_end<begin<end<=ends[0]['monotonic_ns']<end_ns:
            raise ValueError('normal phase windows overlap or are invalid')
        if summary!=summarize(root/'events-service13a',begin,end,ends[0]['request_id']):
            raise ValueError('normal phase summary differs from raw events')
        require_process(summary,boot)
        previous_end=end
    return previous_end


if __name__ == '__main__':
    directory, begin, end, request_id, destination = sys.argv[1:]
    result = summarize(directory, int(begin), int(end), request_id)
    with pathlib.Path(destination).open('x') as output:
        json.dump(result, output, indent=2)
