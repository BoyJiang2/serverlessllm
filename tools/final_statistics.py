"""Offline descriptive strata and the reviewed exploratory C23 six-block analysis."""
import collections
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics

ROOT = Path(__file__).resolve().parent.parent
RUN = 'sllm-glm51fp8-20261027T011726Z-28c92313'
PACKET = ROOT/'reviews/actual-controls'/RUN
PLAN_SHA = 'c7034b1dcf35b006dba43d8cbb4681dc659ed41ee13949aff452f9db6f641482'
LEDGER_SHA = '76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b'
STRATUM_FIELDS = ('node', 'boot_id', 'code_commit', 'source_manifest_sha256',
                  'protocol_revision', 'preparation_revision', 'resource_window_revision')
CONDITIONS = ('store_pool_hit', 'store_disk_cold', 'safetensors_disk_cold', 'safetensors_disk_hot')


def checked_json(path, sha=None):
    data = path.read_bytes()
    if sha is not None:
        assert hashlib.sha256(data).hexdigest() == sha, str(path)
    return json.loads(data)


def describe(values):
    assert values and all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values)
    ordered = sorted(values)
    return dict(n=len(values), mean=statistics.mean(values), median=statistics.median(values),
                p90_nearest_rank=ordered[math.ceil(.9*len(values))-1],
                minimum=ordered[0], maximum=ordered[-1])


def wilson(success, total):
    assert type(success) is int and type(total) is int and 0 <= success <= total and total > 0
    z = 1.959963984540054
    p = success/total
    denominator = 1+z*z/total
    center = (p+z*z/(2*total))/denominator
    half = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0., center-half), min(1., center+half)]


def percentile(ordered, probability):
    pos = probability*(len(ordered)-1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo]+(ordered[hi]-ordered[lo])*(pos-lo)


def paired_analysis(rows, accepted_ids):
    planned = [(block, condition) for block in range(5, 11) for condition in CONDITIONS]
    by_key = collections.defaultdict(list)
    for row in rows:
        if row['execution_run_id'] == RUN:
            by_key[(row['block'], row['condition'])].append(row)
    missing = [f'{condition}_{block:02}' for block, condition in planned
               if len(by_key[(block, condition)]) != 1
               or by_key[(block, condition)][0]['status'] != 'success'
               or by_key[(block, condition)][0]['attempt_id'] not in accepted_ids]
    out = dict(scope='Exploratory conditional analysis specified during collection, not original ten-block completion.',
               blocks=list(range(5, 11)), intervals=None, ineligible_ids=missing)
    if missing:
        return out
    chosen = [by_key[key][0] for key in planned]
    # Every paired vector must represent the same actual execution stratum.
    assert len({tuple(row[f] for f in STRATUM_FIELDS) for row in chosen}) == 1
    metric_keys = set(chosen[0]['metrics'])
    assert metric_keys and all(set(row['metrics']) == metric_keys for row in chosen)
    rng = random.Random(20261024)
    draws = [[rng.randrange(6) for _ in range(6)] for _ in range(10000)]
    draw_bytes = json.dumps(draws, separators=(',', ':')).encode()
    out.update(seed=20261024, replicates=10000,
               draw_indices_sha256=hashlib.sha256(draw_bytes).hexdigest(),
               draw_serialization='UTF-8 json.dumps(draws,separators=(comma,colon)); no newline',
               draw_rule='random.Random(seed); [[rng.randrange(6) for _ in range(6)] for _ in range(10000)]',
               interval_method='Marginal nominal95% percentile; linear p*(10000-1); no simultaneous coverage.',
               limitations='Six serial blocks; P90 is sample maximum. Resampling does not cure dependence or small n.',
               intervals={})
    for condition in CONDITIONS:
        cohort = [by_key[(block, condition)][0] for block in range(5, 11)]
        metrics = {}
        for metric in sorted(metric_keys):
            values = [row['metrics'][metric] for row in cohort]
            estimates = describe(values)
            replicates = {name: [] for name in ('mean', 'median', 'p90_nearest_rank')}
            for indices in draws:
                sample = sorted(values[i] for i in indices)
                replicates['mean'].append(statistics.mean(sample))
                replicates['median'].append(statistics.median(sample))
                replicates['p90_nearest_rank'].append(sample[-1])
            metrics[metric] = dict(raw=[dict(attempt_id=row['attempt_id'], value=value)
                                       for row, value in zip(cohort, values)], point=estimates,
                                   ci95={name: [percentile(sorted(v), .025), percentile(sorted(v), .975)]
                                         for name, v in replicates.items()})
        out['intervals'][condition] = metrics
    return out


def load_rows():
    ledger = checked_json(ROOT/'results/formal-ledger.json', LEDGER_SHA)
    prereg = checked_json(ROOT/'protocol/measurement-preregistration.json',
                          ledger['preregistration_sha256'])
    assert [r['attempt_id'] for r in ledger['rows']] == [r['attempt_id'] for r in prereg['attempts']]
    assert len(ledger['rows']) == len({r['attempt_id'] for r in ledger['rows']}) == 40
    snapshots = {}
    for run in {r['execution_run_id'] for r in ledger['rows']} - {None}:
        base = ROOT/'reviews/actual-controls'/run
        for pattern in ('*result.json', '*origin.json'):
            for path in sorted(base.rglob(pattern)):
                snapshots.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
    rows = []
    for state in ledger['rows']:
        row = dict(state)
        if row['execution_run_id'] is None:
            base = ROOT/'evidence/formal-history'
            result_path = base/(row['attempt_id']+'--result.json')
            origin_path = base/(row['attempt_id']+'--origin.json')
        else:
            result_path = snapshots[row['result_sha256']]
            origin_path = snapshots[row['origin_sha256']]
        raw = checked_json(result_path, row['result_sha256'])
        checked_json(origin_path, row['origin_sha256'])
        assert raw['attempt_id'] == row['attempt_id'] and raw['condition'] == row['condition']
        assert raw['block'] == row['block'] and raw['status'] == row['status']
        assert (raw.get('success') is True) == (row['status'] == 'success')
        for field in STRATUM_FIELDS[2:]:
            assert raw.get(field) == row[field], (row['attempt_id'], field)
        row.update(metrics=raw.get('metrics') if row['status'] == 'success' else None,
                   raw_metrics=raw.get('metrics'), error=raw.get('error'), error_type=raw.get('error_type'),
                   right_censored=raw.get('right_censored'), local_result_path=result_path.relative_to(ROOT).as_posix())
        rows.append(row)
    return rows


def summarize(rows, accepted_ids):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[tuple(row[f] for f in STRATUM_FIELDS)+(row['condition'],)].append(row)
    strata = []
    for key, group in groups.items():
        metrics = collections.defaultdict(list)
        for row in group:
            if row['status'] == 'success':
                for metric, value in row['metrics'].items():
                    metrics[metric].append(dict(attempt_id=row['attempt_id'], value=value))
        strata.append(dict(stratum=dict(zip(STRATUM_FIELDS+('condition',), key)),
                           attempted=len(group), statuses=dict(collections.Counter(r['status'] for r in group)),
                           acceptance_scope='C23 actual postrun review' if key[2] == rows[-1]['code_commit'] else 'Historical raw-success description; see separate original reviews.',
                           metrics={metric: dict(raw=raw, **describe([r['value'] for r in raw]),
                                                 ci95=None, ci_reason='All-observed stratum description; C23 matched cohort reported separately.')
                                    for metric, raw in sorted(metrics.items())}))
    historical = {}
    for condition in CONDITIONS:
        group = [r for r in rows if r['condition'] == condition]
        assert len(group) == 10 and all(r['status'] in ('success', 'failed') for r in group)
        success = sum(r['status'] == 'success' for r in group)
        historical[condition] = dict(n=10, success=success, failed=10-success,
                                     raw_success_fraction=success/10, wilson95=wilson(success, 10),
                                     scope='Mixed historical revisions: descriptive completion, not current-code reliability.')
    return dict(rows=rows, historical_condition_outcomes=historical, strata=strata,
                c23_matched=paired_analysis(rows, accepted_ids))


def main():
    assert checked_json(PACKET/'postrun-statistical-plan-review.json')['verdict'] == 'approved scope'
    assert hashlib.sha256((PACKET/'postrun-statistical-plan.md').read_bytes()).hexdigest() == PLAN_SHA
    review = checked_json(PACKET/'postrun-evidence-review.json')
    assert review['verdict'] == 'approved' and review['run_id'] == RUN
    audit = checked_json(PACKET/'formal-raw-audit-summary.json')
    assert audit['run_id'] == RUN and audit['code_commit'] == review['code_commit']
    assert audit['manifest_sha256'] == review['manifest_sha256']
    assert audit['full_report']['sha256'] == review['audit_stdout_sha256']
    accepted = {r['attempt_id'] for r in audit['trials']}
    rows = load_rows()
    current = [r for r in rows if r['execution_run_id'] == RUN]
    assert {r['attempt_id'] for r in current} == accepted and len(accepted) == 26
    audited = {r['attempt_id']: r for r in audit['trials']}
    assert all(row['metrics'] == audited[row['attempt_id']]['metrics'] for row in current)
    output = summarize(rows, accepted)
    output.update(schema='sllm-final-statistics-v1', python=platform.python_version(),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  ledger_sha256=LEDGER_SHA, interpretation_plan_sha256=PLAN_SHA,
                  evidence_review_sha256=hashlib.sha256((PACKET/'postrun-evidence-review.json').read_bytes()).hexdigest(),
                  status='Candidate output; independent implementation and numerical-result review required.',
                  phase_warning='Nested phase spans are not additive. Backend first-token event is not client wire delivery.')
    (ROOT/'results/final-statistics.json').write_text(json.dumps(output, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(dict(rows=len(rows), strata=len(output['strata']), matched_blocks=output['c23_matched']['blocks']))


if __name__ == '__main__':
    main()
