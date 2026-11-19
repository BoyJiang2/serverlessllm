"""Verify the standalone GLM-5.3 Sleep L2 receipt and remeasure host RAM."""

import argparse
import json
import statistics
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def measure(samples, begin, end):
    assert begin < end
    before = [row for row in samples if begin - 10 * 10**9 <= row['ns'] < begin]
    window = [row for row in samples if begin <= row['sample_begin_ns'] <= row['ns'] <= end]
    active = [row for row in window if row['cgroup_status'] == 'active']
    if not before or not active:
        return None
    host = [row['host_available_bytes'] for row in window]
    entry = int(statistics.median(row['host_available_bytes'] for row in before))
    file_refaults = [row['workingset_refault_file_pages'] for row in (active[0], active[-1])]
    delta = None if None in file_refaults else file_refaults[1] - file_refaults[0]
    assert delta is None or delta >= 0
    return dict(begin_ns=begin, end_ns=end, samples=len(window), active_samples=len(active),
                sample_begin_gap_s=(window[0]['sample_begin_ns'] - begin) / 1e9,
                sample_end_gap_s=(end - window[-1]['ns']) / 1e9,
                seconds=(end-begin)/1e9, at_entry_bytes=entry, host_min_bytes=min(host),
                host_available_loss_bytes=max(0, entry-min(host)),
                cgroup_anon_peak_bytes=max(row['anon_bytes'] for row in active),
                cgroup_nonfile_peak_bytes=max(row['nonreclaimable_bytes'] for row in active),
                file_refault_delta_pages=delta)


def inspect(root):
    qualified = read(root / 'qualification.json')
    assert qualified['qualified'] is True and qualified['level'] == 2
    release = qualified['native']['release_seconds']
    assert qualified['native']['qualified'] and release >= 60
    worker = root / 'glm53-sf-cold'
    responses = [read(worker / f'{phase}-response.json') for phase in ('before-sleep', 'after-wake', 'after-clear')]
    assert [row['num_cached_tokens'] for row in responses] == [0, 512, 0]
    assert all((row['text'], row['token_ids'], row['finish_reason']) ==
               (responses[0]['text'], responses[0]['token_ids'], responses[0]['finish_reason']) for row in responses)
    initial = {row['rank']: row for row in read(worker / 'initial-ranks.json')}
    rebound = {row['rank']: row for row in read(worker / 'rebound-ranks.json')}
    assert initial.keys() == rebound.keys() == set(range(8))
    identity = ('pid', 'process_start', 'instance_id', 'cuda_device', 'view_signature', 'groups')
    assert all(all(initial[rank][key] == rebound[rank][key] for key in identity) for rank in initial)
    rank_by_device = {row['cuda_device']: row['rank'] for row in rebound.values()}
    assert set(rank_by_device.values()) == set(range(8)) and len(rank_by_device) == 8
    groups = {str(rank): {} for rank in rank_by_device.values()}
    for row in read(worker / 'after-wake-h2d.json'):
        assert row['direction'] == 'H2D' and row['host_objects'] * 256 == 512
        assert row['skip'] == 0 and row['begin_ns'] <= row['completed_ns']
        for group in row['groups']:
            rank_groups = groups[str(rank_by_device[row['device']])]
            assert group['id'] not in rank_groups and group['logical_block'] == 64 and group['blocks'] == 8
            assert group['payload_bytes'] > 0
            rank_groups[group['id']] = group['layers']
    assert all(row == {0: 21, 1: 78} for row in groups.values())
    coverage = read(root / 'worker-memory-coverage.json')
    assert coverage['phase_clock_matches'] and not coverage['observer_errors'] and not coverage['phase_read_errors']
    samples = records(root / 'worker-memory.jsonl')
    assert all(a['ns'] < b['sample_begin_ns'] for a, b in zip(samples, samples[1:]))
    active = [row for row in samples if row['cgroup_status'] == 'active']
    assert len({(row['container'], row['pid'], row['cgroup']) for row in active}) == 1
    assert all(dict(line.split() for line in row['events'].splitlines())['oom_kill'] == '0'
               and row['swap_current_bytes'] == 0 for row in active)
    intervals = {key: (value['begin_ns'], value['end_ns']) for key, value in coverage['phases'].items()}
    events = {row['event']: row['ns'] for row in records(worker / 'memory-phases.jsonl')}
    intervals['sleeping'] = (events['sleep_end'], events['wake_begin'])
    intervals['wake_through_rebind'] = (events['wake_begin'], events['rebind_end'])
    host = [row for row in samples if row['host_available_bytes'] is not None]
    measured = {key: measure(host, begin, end) for key, (begin, end) in intervals.items()}
    assert all(value is not None for key, value in measured.items() if key != 'sleep')
    opening = read(root / 'preflight.json')['available_bytes']
    load_floor = min(row['host_available_bytes'] for row in host if row['ns'] <= events['sleep_end'])
    return dict(run_id=root.name, qualified=True, release_seconds=release, groups_by_rank=groups,
                full_first_load_host_drop_bytes=max(0, opening-load_floor), memory=measured,
                samples=len(host), qp_source=str(root))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.root), indent=2) + '\n', encoding='utf-8')
