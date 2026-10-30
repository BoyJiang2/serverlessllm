"""Source-worker deadline arithmetic; never grants resource authorization."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
import time

PHASES = ('preflight', 'image_restore', 'engine_initialize_and_export', 'fixed_token_business',
          'natural_eos_business', 'worker_shutdown', 'release_observe', 'cleanup_evidence', 'safety_margin')
TAIL = ('worker_shutdown', 'release_observe', 'cleanup_evidence', 'safety_margin')


def _utc(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone-qualified time required')
    return result.astimezone(timezone.utc)


def load_source_plan(path, expected_sha256):
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise ValueError('external source plan SHA required')
    with open(path, 'rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError('source plan length or SHA differs')
    return json.loads(raw)


def source_schedule(plan, source_receipt, *, now):
    required = {'schema', 'run_id', 'holder', 'code_commit', 'spec_sha256', 'source_manifest_sha256',
                'resource_id', 'booking_id', 'window_start', 'window_end', 'cleanup_complete_by', 'seconds'}
    if (not isinstance(plan, dict) or set(plan) != required
            or plan['schema'] != 'kimi-source-window-v1' or plan['resource_id'] != 'h20/qjhs003'
            or not isinstance(plan['booking_id'], str) or not plan['booking_id'].startswith('bk-')):
        raise ValueError('exact source-window schema and node required')
    for name, receipt_name in (('run_id', 'run_id'), ('holder', 'holder'), ('code_commit', 'code_commit'),
            ('spec_sha256', 'spec_sha256'), ('source_manifest_sha256', 'manifest_sha256')):
        if plan[name] != source_receipt[receipt_name]:
            raise ValueError('source window identity differs: ' + name)
    seconds = plan['seconds']
    if (not isinstance(seconds, dict) or set(seconds) != set(PHASES)
            or any(type(v) is not int or not 0 < v <= 86400 for v in seconds.values())
            or seconds['release_observe'] < 60):
        raise ValueError('all finite phase maxima and at least 60-second observation budget required')
    start, end, cleanup = (_utc(plan[k]) for k in ('window_start', 'window_end', 'cleanup_complete_by'))
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('timezone-qualified current time required')
    current = now.astimezone(timezone.utc)
    total = sum(seconds.values())
    if not start < cleanup <= end - timedelta(minutes=10):
        raise ValueError('cleanup must finish at least ten minutes before booking end')
    if current < start:
        raise ValueError('source window has not started')
    if current + timedelta(seconds=total) > cleanup:
        raise TimeoutError('remaining window cannot cover full source budget and cleanup')
    run_cleanup = current + timedelta(seconds=total)
    guard_fire = run_cleanup - timedelta(seconds=sum(seconds[n] for n in TAIL))
    cursor, stages = current, {}
    for name in PHASES:
        cursor += timedelta(seconds=seconds[name])
        stages[name] = cursor.isoformat()
    return dict(total_max_seconds=total, compute_max_seconds=total-sum(seconds[n] for n in TAIL),
                guard_fire_by_utc=guard_fire.isoformat(), cleanup_complete_by_utc=run_cleanup.isoformat(),
                window_cleanup_deadline_utc=cleanup.isoformat(),
                phase_deadlines_utc=stages, minimum_release_observe_seconds=60, timing_fits=True,
                booking_verified=False, guard_verified=False, runtime_admitted=False)


def phase_remaining_seconds(schedule, phase, *, entered, elapsed, now):
    """Use the original batch anchor, never a new per-phase start time."""
    if phase not in PHASES:
        raise ValueError('unknown source phase')
    if (type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0
            or entered.tzinfo is None or entered.utcoffset() is None
            or now.tzinfo is None or now.utcoffset() is None):
        raise ValueError('finite elapsed time and timezone-qualified clocks required')
    deadline = _utc(schedule['phase_deadlines_utc'][phase])
    if now < entered:
        raise TimeoutError('source wall clock regressed')
    remaining = min((deadline - entered).total_seconds() - elapsed,
                    (deadline - now).total_seconds())
    if remaining <= 0:
        raise TimeoutError('source phase deadline exhausted: ' + phase)
    return remaining


def main():
    import argparse
    from source_bundle import verify_source_run
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('code', 'manifest', 'manifest-sha', 'spec', 'spec-sha', 'plan', 'plan-sha'):
        parser.add_argument('--' + name, required=True)
    args = parser.parse_args()
    entered = datetime.now(timezone.utc)
    started = time.monotonic()
    receipt = verify_source_run(args.code, args.manifest, args.manifest_sha, args.spec, args.spec_sha)
    plan = load_source_plan(args.plan, args.plan_sha)
    result = source_schedule(plan, receipt, now=entered)
    elapsed = time.monotonic() - started
    finished = datetime.now(timezone.utc)
    try:
        phase_remaining_seconds(result, 'preflight', entered=entered, elapsed=elapsed, now=finished)
    except TimeoutError as error:
        raise TimeoutError('source verification consumed preflight budget or clock regressed') from error
    result['validation_elapsed_seconds'] = elapsed
    print(json.dumps(dict(result, run_id=receipt['run_id'], holder=receipt['holder']), sort_keys=True))


if __name__ == '__main__':
    main()
