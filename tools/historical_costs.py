"""Keep known historical costs and explicit gaps; never manufacture a total from nested intervals."""
import datetime
import hashlib
import json
from pathlib import Path

from final_statistics import ROOT, checked_json
from final_costs import span


def main():
    sources = {}

    def source(path):
        data = path.read_bytes()
        key = path.resolve().as_posix()
        sources[key] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        return data

    def events(path):
        return [json.loads(line) for line in source(path).splitlines() if line.strip()]

    def event_time(rows, name):
        matches = [r['monotonic_ns'] for r in rows if r['name'] == name]
        assert len(matches) == 1, name
        return matches[0]

    early = ROOT.parent/'outputs'
    first = source(early/'a1-evidence/download-workers4-complete-log.txt').decode().splitlines()[0]
    first = json.loads(first[first.index('{'):])
    resumed = events(early/'a1-evidence/download16-complete.log')
    last = resumed[-1]
    assert first['status'] == resumed[0]['status'] == 'start'
    assert last['status'] == 'all_files_verified' and last['files'] == 150
    delta = lambda a, b: (datetime.datetime.fromisoformat(b)-datetime.datetime.fromisoformat(a)).total_seconds()
    download = dict(first_begin_utc=first['time'], resumed_begin_utc=resumed[0]['time'], all_verified_utc=last['time'],
                    full_wall_s=delta(first['time'],last['time']), resumed_wall_s=delta(resumed[0]['time'],last['time']),
                    scope='Resumed interval included in full interval. Includes retry, switching and verification, not pure network time.')
    native = []
    for name in ('native_repeat7','native_repeat8'):
        rows = events(early/'native-reference'/name/'events.jsonl')
        native.append(dict(reference=name, scope='Backend correctness reference; includes full tensor audit, not formal timing.',
                           process_to_all_correct_s=span(event_time(rows,'process_started'),event_time(rows,'all_requests_correct')),
                           engine_create_to_ready_s=span(event_time(rows,'engine_create_begin'),event_time(rows,'engine_ready'))))
    component_rows = events(early/'store-evidence/store-validation-restoredraw3/events.jsonl')
    component = dict(scope='A3 component only; does not prove native service.',
                     engine_create_to_ready_s=span(event_time(component_rows,'engine_create_begin'),event_time(component_rows,'engine_ready')),
                     engine_create_to_checks_s=span(event_time(component_rows,'engine_create_begin'),event_time(component_rows,'A3_component_checks_passed')))
    conversion = json.loads(source(ROOT/'evidence/cost-sources/rawexport-event-collection.json'))
    ranks = []
    all_rows = []
    for f in conversion['files']:
        data = f['raw_utf8'].encode()
        assert len(data) == f['bytes'] and hashlib.sha256(data).hexdigest() == f['sha256']
        rows = [json.loads(line) for line in data.splitlines()]
        rank_set = {r['rank'] for r in rows if 'rank' in r}
        assert len(rank_set) == 1
        ranks.append(dict(rank=next(iter(rank_set)), source_sha256=f['sha256'],
                          weights_pipeline_including_export_s=span(event_time(rows,'weights_pipeline_including_allocation_begin'),event_time(rows,'weights_pipeline_including_allocation_end')),
                          gap_ingestion_end_to_postprocess_begin_s=span(event_time(rows,'weight_ingestion_end'),event_time(rows,'quantization_postprocess_begin'))))
        all_rows += rows
    assert sorted(r['rank'] for r in ranks) == list(range(8))
    launch = conversion['metadata'][0]['selected_fields']
    export = dict(run_id=launch['run_id'],boot_id=launch['boot_id'],ranks=ranks,
                  dispatch_to_latest_recorded_warmup_end_s=span(launch['dispatch_monotonic_ns'],max(r['monotonic_ns'] for r in all_rows if r['name']=='compile_graph_and_warmup_end')),
                  pure_save_s=None, scope='Actual eight-rank export-run events. Gap includes unbracketed work; not a proven pure Store-save interval. Final process/cleanup cost not closed here.')
    fields = {'ray_controls','service_supervisor','supervisor','phase','monitor','whole_monitor','resource',
              'resource_release','release','final_release','pool','pool_ready','service_final_state',
              'formal_supervisor','cost_scope','sampled_cgroup_peak_bytes','kernel_peak_bytes'}
    controls = []
    for path in sorted((ROOT/'reviews/actual-controls').glob('*/*outcome.json')):
        raw = json.loads(source(path))
        row = dict(run_id=raw['run_id'],source=path.relative_to(ROOT).as_posix(),
                   code_commit=raw.get('code_commit'),manifest_sha256=raw.get('manifest_sha256'),
                   status=raw.get('status'),recorded_cost_fields={k:v for k,v in raw.items() if k in fields or k.endswith('_seconds')},
                   missing_fields='Unknown, never zero; original source remains authoritative. Budgets inside raw supervisor records are not actual elapsed times.')
        correction = path.parent/'resource-correction.json'
        if correction.exists():
            row['effective_resource_correction'] = json.loads(source(correction))
            row['superseded_resource_note'] = 'Original sampled peak0 retained in source fields; use appended776676474880B correction, kernel peak still null.'
        controls.append(row)
    assert len(controls) == len({r['run_id'] for r in controls}) == 16
    c21_path = ROOT/'reviews/actual-controls/sllm-glm51fp8-20261026T225201Z-02cd4cdc/cost-intervals.json'
    c21 = json.loads(source(c21_path))
    c23 = json.loads(source(ROOT/'results/c23-costs.json'))
    # No total is computed: early boot and current boot have different monotonic clocks.
    out = dict(schema='sllm-historical-cost-inventory-v1',source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               scope='Known stage costs and missingness; not a complete historical resource integral or fee total.',
               status='Candidate evidence inventory; independent review required.', sources=sources,
               download=download,stable_native_references=native,component_restore=component,conversion=export,
               prior_control_outcomes=controls,c21_nonadditive_intervals=c21,
               c23_costs=dict(path='results/c23-costs.json',intervals_nonadditive=c23['intervals_nonadditive']),
               total_cost=None,total_cost_reason='Overlapping intervals, different boots, incomplete early failure/compile/idle/cleanup telemetry. No defensible complete sum.',
               unresolved=['Full pure-save interval and conversion resource peaks/integrals.','Earlier native/Store failed attempts, compiler and approval idle lack closed common timelines.','CPU seconds, host/GPU memory-time and energy not comprehensively recorded.','Assets are reusable; do not charge checkpoint/Store capacity as new allocation on every trial.','All original40 formal attempts including failures remain in formal-ledger.json; original9 per-ID raw timings remain separate from current costs.'])
    (ROOT/'results/historical-costs.json').write_text(json.dumps(out,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(dict(prior_control_runs=len(controls),conversion_ranks=len(ranks),download_wall_s=download['full_wall_s'],total_cost=None))


if __name__ == '__main__':
    main()
