import csv, hashlib, json, statistics
from pathlib import Path
S = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent
PRIMARY = [1, 2, 3, 5, 6, 7, 8, 10]
CONDITIONS = ['safetensors_disk_cold', 'safetensors_disk_hot', 'store_disk_cold', 'store_pool_hit']
INPUTS = {
 'analysis': ('kimi-four-condition-actual-analysis-v1-20261107.json', '0959cf17761ec076a07711bd010e8ab4e0f061bb3707b9cd252963fe29758df1'),
 'f7': ('kimi-existing-startup-log-runtime-results-audit-20261107.json', '9f69c5d1dad96abc25098b526d951bfdfe11d0896c18c7e2f5a21dcd956f4de4'),
 'cost': ('kimi-final-cost-evidence-audit-20261107-v1.json', '07a44e897eec5d8b6aab2c37cfaeaacedd87201939b37d8ebc308a5e2cbaf247'),
}

def save(name, value):
    with (OUT / name).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')

def summary(values):
    observed = [v for v in values if v is not None]
    return dict(raw=values, n=len(observed), mean=statistics.mean(observed) if observed else None,
                median=statistics.median(observed) if observed else None)

def main():
    inputs, provenance = {}, []
    for key, (name, expected) in INPUTS.items():
        p = S / name
        raw = p.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        assert actual == expected, (p, actual)
        inputs[key] = json.loads(raw)
        provenance.append(dict(path=str(p), sha256=actual, bytes=len(raw)))
    analysis, f7, cost = inputs['analysis'], inputs['f7'], inputs['cost']
    assert analysis['complete_block_ids'] == PRIMARY
    assert analysis['cohorts']['original']['qualified'] == 38
    assert analysis['cohorts']['original']['failed_launched'] == 2
    f7_by_id = {r['run_id']: r for r in f7['rows']}
    cost_by_id = {r['run_id']: r for r in cost['rows']}
    attempts, endpoints, controller = [], [], []
    refs_seen = set()
    for r in analysis['runs']:
        primary = r['cohort'] == 'original' and r['block'] in PRIMARY
        reason = 'No paired per-rank full-weight-load begin/end timestamps in audited historical evidence; ownership/capture/backend/f7 durations cannot supply them.'
        row = dict(run_id=r['run_id'], cohort=r['cohort'], block=r['block'], condition=r['condition'],
                   original_qualified=r['qualified'], primary=primary,
                   weight_load_host_all_rank_span_s=None, max_rank_weight_load_host_duration_s=None,
                   valid_rank_endpoint_pairs=0, status='MISSING_HISTORICAL_ENDPOINTS', reason=reason,
                   original_refs=r['refs'], specialized_f7_observation=f7_by_id.get(r['run_id']))
        attempts.append(row)
        for rank in range(8):
            endpoints.append(dict(run_id=r['run_id'], cohort=r['cohort'], block=r['block'], condition=r['condition'],
                primary=primary, expected_rank=rank, rank_begin_ns=None, rank_end_ns=None, duration_s=None,
                endpoint_pid=None, endpoint_process_startticks=None, endpoint_instance=None,
                endpoint_boot_id=None, endpoint_time_namespace=None, endpoint_clock_implementation=None,
                begin_source_path=None, begin_source_sha256=None, end_source_path=None, end_source_sha256=None,
                status='MISSING_HISTORICAL_ENDPOINTS', reason=reason))
        c = cost_by_id.get(r['run_id'])
        controller.append(dict(run_id=r['run_id'], cohort=r['cohort'], block=r['block'], condition=r['condition'],
            primary=primary, original_qualified=r['qualified'], original_controller_status=r['controller_status'],
            controller_total_reported_s=c['elapsed_s']['controller_total_reported_s'] if c else None,
            common_begin_to_verified_release_s=c['elapsed_s']['common_begin_to_verified_release_s'] if c else None,
            Prep_plus_A_s=r.get('timing',{}).get('Prep_plus_A_s'),
            event_monotonic_ns=c['event_monotonic_ns'] if c else None,
            normal_Store_stop_controller_s=c['elapsed_s']['normal_Store_stop_controller_s'] if c else None,
            clock_domain=c['clock_domain'] if c else None,
            controller_pid=c['controller_pid'] if c else None,
            cleanup_ref=r['refs'].get('cleanup'), controller_ref=r['refs'].get('controller'),
            normal_vs_forced='NORMAL_EXIT_AND_RELEASE_VERIFIED' if c else 'SEE_ORIGINAL_CLEANUP_RECEIPT_NOT_PROMOTED_TO_NORMAL_EXIT'))
        for ref in r['refs'].values():
            if not isinstance(ref, dict) or 'path' not in ref or 'sha256' not in ref:
                continue
            if ref['path'] in refs_seen:
                continue
            p = Path(ref['path']); raw = p.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == ref['sha256'], p
            provenance.append(dict(path=str(p), sha256=ref['sha256'], bytes=len(raw)))
            refs_seen.add(ref['path'])
    assert len(attempts) == 46 and sum(r['primary'] for r in attempts) == 32
    conditions = {}
    for condition in CONDITIONS:
        rows = sorted((r for r in attempts if r['primary'] and r['condition'] == condition), key=lambda r:r['block'])
        assert [r['block'] for r in rows] == PRIMARY
        conditions[condition] = dict(block_ids=PRIMARY, run_ids=[r['run_id'] for r in rows],
            weight_load_host_all_rank_span_s=summary([r['weight_load_host_all_rank_span_s'] for r in rows]),
            max_rank_weight_load_host_duration_s=summary([r['max_rank_weight_load_host_duration_s'] for r in rows]),
            specialized_single_f7_log_duration_s=summary([r['specialized_f7_observation']['C_MODEL_LOADING_LOG_SPAN_s'] for r in rows]))
    save('attempts.json', dict(original_cohorts=analysis['cohorts'], primary_complete_blocks=PRIMARY, attempts=attempts))
    save('rank-endpoints.json', dict(scope='46 listed attempts; original40, supplement1, historical5 kept separate', rows=endpoints))
    with (OUT/'rank-endpoints.csv').open('x', encoding='utf-8', newline='') as f:
        writer=csv.DictWriter(f, fieldnames=list(endpoints[0])); writer.writeheader(); writer.writerows(endpoints)
    save('condition-summaries.json', dict(primary_complete_blocks=PRIMARY, conditions=conditions,
        null_policy='JSON null / CSV empty cell means missing, never zero; all condition valid n=0 for unified metrics.'))
    save('controller-attempts.json', dict(rows=controller, warning='Prep+A is not the controller lifecycle. Failed cleanup does not imply normal exit.'))
    save('verified-inputs.json', dict(refs=provenance, verified_snapshot_ref_count=len(refs_seen),
        raw_log_sha_scope='f7 raw source SHA is transcribed from the prior root collector; raw logs are not reopened by this CPU organizer.'))
    print(json.dumps(dict(attempts=len(attempts), endpoint_rows=len(endpoints), primary_attempts=32,
        verified_snapshot_refs=len(refs_seen), unified_valid_n={c:v['weight_load_host_all_rank_span_s']['n'] for c,v in conditions.items()})))

if __name__ == '__main__':
    main()
