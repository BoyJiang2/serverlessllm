"""Run only after the frozen NFS40 campaign exits; read evidence, never run models."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from v4_campaign_statistics import campaign_statistics, join_validated_reviews, sampled_resources


HEAD = '88cc71ec50c8668c26f26636d7d432c3351356fe'
PROTOCOL_SHA = 'bffa18dfe81a2c18ab31b8df51246c587c2e7abe58aea214057775a46e7a24be'
AMENDMENT_SHA = '65ee00eb18f632bd993b7e24dc27d9925ec4bcad2ac953fa0c0ca89ad0839bef'


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def live(handle):
    path = Path('/proc') / str(handle['pid']) / 'stat'
    try:
        fields = path.read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != 'Z' and int(fields[19]) == handle['start_ticks']


def collect(code, evidence, output):
    require(not sys.flags.optimize, 'Optimized validation forbidden')
    handle = read(evidence / 'nfs-temporal-formal-campaign-handle.json')
    require(not live(handle) and not live(read(evidence / 'native-service-current-handle.json')),
            'Campaign or final attempt is still running; no collection during measurement')
    require(handle['source_head'] == HEAD and handle['protocol_sha256'] == PROTOCOL_SHA
            and handle['amendment_sha256'] == AMENDMENT_SHA, 'Wrong campaign handle')
    campaign = Path(handle['campaign'])
    complete = read(campaign / 'complete.json')
    require(complete['attempts'] == 40 and complete['fully_qualified_attempts'] == 38
            and complete['all_runtime_qualified'] is False, 'Frozen campaign is not complete')
    require(not (campaign / 'failure.json').exists(), 'Campaign failed; preserve partial evidence separately')
    require(subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=code, text=True).strip() == HEAD
            and not subprocess.check_output(['git', 'status', '--porcelain'], cwd=code, text=True).strip(),
            'Inference checkout changed')
    sys.path[:0] = [str(code / 'benchmarks'), str(code / 'sllm_store'), str(code)]
    from v4_campaign import next_attempt
    from v4_results import export_results, replay
    from v4_trial_review import review_trial

    protocol = code / 'docs/evidence/v4-formal-4x10/protocol.json'
    amendment = code / 'docs/evidence/v4-sf-hot-lease-amendment/amendment-nfs-temporal.json'
    require(digest(protocol) == PROTOCOL_SHA and digest(amendment) == AMENDMENT_SHA, 'Frozen input changed')
    policy = read(amendment)
    journals = evidence / 'v4-4x10-journals-20261107'
    rows, _, entries = replay(protocol, PROTOCOL_SHA, [journals])
    require(next_attempt(entries, HEAD, evidence, policy['acknowledged_failures'],
                         policy['prior_qualified_attempts']) is None, 'Unconsumed or invalid attempt')
    reviews = {}
    resources = {}
    for row in rows:
        name = row['attempt_id']
        base = evidence / name
        if name not in policy['acknowledged_failures']:
            original = read(base / 'runtime-review.json')
            spec = read(base / 'preparation.json')['trial']
            old = name in policy['prior_qualified_attempts']
            control = (code / 'docs/evidence/v4-warm-engine-amendment/amendment.json') if old else amendment
            require(digest(control) == spec['amendment_sha256'], 'Attempt amendment differs')
            reviewed = review_trial(base, protocol, PROTOCOL_SHA, journals, row['code_commit'],
                                    publish=False, amendment_path=control, amendment_sha=spec['amendment_sha256'])
            # The re-review uses the final frozen reviewer, keeping the original report intact.
            left = {k: v for k, v in original.items() if k != 'reviewer_source_sha256'}
            right = {k: v for k, v in reviewed.items() if k != 'reviewer_source_sha256'}
            require(left == right, 'Independent re-review disagrees: ' + name)
            reviews[name] = reviewed
        telemetry = base / 'telemetry.jsonl'
        require(telemetry.is_file(), 'Missing started-attempt telemetry: ' + name)
        resources[name] = sampled_resources(json.loads(line) for line in telemetry.read_text().splitlines())
        locked = base / 'output/trial-cache-lease-locked.json'
        if locked.is_file():
            lease = read(locked)
            released = read(base / 'output/trial-cache-lease-released.json')
            resources[name]['sf_hot_page_lease'] = dict(
                acquisition_seconds=(lease['locked_ns'] - lease['begin_ns']) / 1e9,
                file_bytes=lease['bytes'], locked_rounded_bytes=lease['VmLck'],
                helper_rss_at_lock_bytes=lease['VmRSS'], released_locked_bytes=released['VmLck'],
                scope='OS file-page residency control, included in preparation; distinct from CUDA pinned Store pool')
    joined = join_validated_reviews(rows, read(protocol), reviews, policy['acknowledged_failures'], PROTOCOL_SHA)
    require(sum(row['success'] for row in joined) == 38, 'Unexpected final denominator')
    output = output.resolve()
    require(not output.is_relative_to(evidence.resolve()) and not output.is_relative_to(code.resolve()),
            'Derived output must be outside raw evidence and inference checkout')
    output.mkdir(exist_ok=False)
    export_results(protocol, PROTOCOL_SHA, [journals], output / 'raw-replay')
    for name, value in [('qualified-rows.json', joined), ('statistics.json', campaign_statistics(joined, read(protocol))),
                        ('sampled-resources.json', resources)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    columns = ['attempt_id', 'block', 'condition', 'code_commit', 'full_run_status', 'success',
               'correct_business_response_observed', 'command_to_correct_business_response_s',
               'preparation_s', 'total_elapsed_seconds', 'runtime_failure_class', 'failed_stage',
               'right_censored', 'startup_censor_elapsed_seconds', 'failure_elapsed_seconds',
               'control_amendment_sha256']
    with (output / 'attempts.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in joined:
            flat = dict(row, **row['metrics'], **row.get('runtime_observations', {}))
            if row.get('failure_ns') is not None:
                flat['failure_elapsed_seconds'] = (row['failure_ns'] - row['preparation_begin_ns']) / 1e9
            writer.writerow({key: flat.get(key) for key in columns})
    files = [protocol, amendment, campaign / 'complete.json', Path(__file__),
             Path(__file__).with_name('v4_campaign_statistics.py')]
    files += [evidence / row['attempt_id'] / 'runtime-review.json' for row in joined if row['success']]
    files += [evidence / name / 'failure.json' for name in policy['acknowledged_failures']]
    manifest = dict(kind='derived_read_only_formal_NFS40', source_head=HEAD, protocol_sha256=PROTOCOL_SHA,
                    amendment_sha256=AMENDMENT_SHA, qualified=38, original_failures=2,
                    inputs={str(path): digest(path) for path in files},
                    outputs={str(path.relative_to(output)): digest(path) for path in output.rglob('*') if path.is_file()})
    (output / 'collection-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(dict(output=str(output), qualified=38, original_failures=2)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.code, args.evidence, args.output)
