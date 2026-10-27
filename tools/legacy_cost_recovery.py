"""Reconcile retained early costs and the independent reclaim probe without rerunning either."""
import calendar
import datetime
import hashlib
import json
from pathlib import Path
import re

from final_statistics import ROOT, load_rows
from final_costs import span


def docker_ns(stamp):
    assert stamp.endswith('Z')
    seconds, dot, fraction = stamp[:-1].partition('.')
    assert not dot or fraction.isdecimal() and len(fraction) <= 9
    return calendar.timegm(datetime.datetime.fromisoformat(seconds).timetuple())*10**9+int((fraction or '0').ljust(9,'0'))


def validate_probe_rows(rows):
    assert len(rows) == 8 and {r['device'] for r in rows} == set(range(8))
    for row in rows:
        assert row['allocation_bytes'] == 100*1024**3
        assert row['free_before_bytes'] >= row['allocation_bytes']+4*1024**3
        assert row['free_before_bytes']-row['free_while_allocated_bytes'] >= row['allocation_bytes']
        assert row['free_after_bytes'] >= row['free_before_bytes']-64*1024**2
        assert row['end_monotonic_ns'] >= row['begin_monotonic_ns']
    return max(r['free_before_bytes']-r['free_after_bytes'] for r in rows)


def main():
    sources = {}

    def read(relative):
        p = ROOT/relative
        data = p.read_bytes()
        sources[relative] = dict(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
        return json.loads(data)

    base = 'evidence/cost-sources/'
    legacy = read(base+'legacy-cost-ledger.json')
    snapshot = read(base+'legacy-container-cost-snapshot.json')
    run = snapshot['run_id']
    assert run == 'sllm-glm51fp8-20261024T051606Z-deb76e57'
    assert len(snapshot['containers']) == len({r['container_id'] for r in snapshot['containers']}) == 121
    intervals = []
    for raw in snapshot['containers']:
        assert raw['selection_label'] == run and raw['name'].startswith(run+'-')
        state = raw['state']
        assert state['Running'] is False
        valid = not state['StartedAt'].startswith('0001-') and not state['FinishedAt'].startswith('0001-')
        elapsed = docker_ns(state['FinishedAt'])-docker_ns(state['StartedAt']) if valid else None
        assert elapsed is None or elapsed >= 0
        intervals.append(dict(role=raw['name'].removeprefix(run+'-'),container_id=raw['container_id'],
                              started_at=state['StartedAt'],finished_at=state['FinishedAt'],
                              elapsed_ns=elapsed,elapsed_wall_s=elapsed/1e9 if elapsed is not None else None,
                              exit_code=state['ExitCode'],oom_killed=state['OOMKilled'],
                              automatic_restart_count=raw['restart_count'],configured_limits=raw['limits']))
    by_role = {r['role']:r for r in intervals}
    shared = {}
    for role, recorded in legacy['shared_init'].items():
        raw = read(base+'legacy-shared-init-'+role+'.json')
        elapsed = span(raw['origin_ns'],raw['common_ready_ns'])
        assert raw['observations'] == 1 and elapsed == raw['elapsed_seconds'] == recorded
        assert raw['worker_identity']['container_id'] == by_role[role]['container_id']
        shared[role] = dict(elapsed_s=elapsed,boot_id=raw['boot_id'],scope='One initialization per bootstrap, not per attempt.')
    states = {r['attempt_id']:r for r in load_rows()}
    mapping = dict(prep_s='preparation_s',weight_ing_s='weight_ingestion_span_s',cmd_to_first_tok_s='command_to_first_token_s',
                   cmd_to_correct_s='command_to_correct_business_response_s',trig_to_first_tok_s='trigger_to_first_token_s',
                   backend_construct_s='backend_construct_s',route_publication_s='command_to_route_publication_s')
    assert len(legacy['attempts']) == 9
    for row in legacy['attempts']:
        state = states[row['attempt']]
        raw = read(state['local_result_path'])
        assert state['status'] == row['status'] and state['condition'] == row['condition']
        assert Path(raw['bootstrap_directory']).name == 'service-bootstrap-'+row['bootstrap']
        assert row['shared_init_s'] == shared[row['bootstrap']]['elapsed_s']
        assert all(row.get(old) == (raw.get('metrics') or {}).get(new) for old,new in mapping.items())
        assert row['idle_s'] == raw.get('idle_observation_seconds') and row['post_del_gpu'] == raw.get('post_delete_gpu_MiB')
    probe_base = 'evidence/reclaim-probe/'
    probe = read(probe_base+'result.json')
    dispatch = read(probe_base+'memoryreclaimprobe1-dispatch.json')
    identity = read(probe_base+'memoryreclaimprobe1-identity.json')
    launch = read(probe_base+'source-and-container.json')
    source_path = ROOT/probe_base/'probe_memory_reclaim.py'
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == launch['source']['sha256']
    assert probe['run_id'] == dispatch['run_id'] == identity['run_id'] == run
    assert identity['container_id'] == launch['container_id'] == by_role['memoryreclaimprobe1']['container_id']
    assert probe['status'] == 'eight GPU allocations and releases passed'
    deficit = validate_probe_rows(probe['devices'])
    own = by_role['memoryreclaimprobe1']
    assert own['exit_code'] == 0 and not own['oom_killed']
    pbegin, pend = docker_ns(own['started_at']),docker_ns(own['finished_at'])
    formal = [r for r in intervals if re.fullmatch(r'formal\d+',r['role']) and r['elapsed_ns'] is not None]
    assert formal and all(pend <= docker_ns(r['started_at']) or pbegin >= docker_ns(r['finished_at']) for r in formal)
    gaps = {r['role']:(docker_ns(r['started_at'])-pend)/1e9 for r in formal if docker_ns(r['started_at'])>=pend}
    output = dict(scope='Recovered legacy evidence; no new model/probe/attempt execution. Latest retained container intervals, not a complete historical resource integral.',
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),sources=sources,
                  shared_initializations=shared,legacy_attempts=legacy['attempts'],containers=intervals,
                  original_legacy_container_summary=legacy['containers'],
                  probe=dict(status='retained independent probe evidence passed',run_id=run,boot_id=dispatch['boot_id'],
                       container_id=own['container_id'],elapsed_container_wall_s=own['elapsed_wall_s'],allocation_bytes_perdevice=100*1024**3,
                       max_same_context_free_deficit_bytes=deficit,tolerance_bytes=64*1024**2,devices=probe['devices'],
                       isolated_from_all_retained_formal_worker_intervals=True,gaps_to_later_formal_workers_s=gaps,
                       scope='Sequential100GiB touched perdevice after service teardown; samecontext64MiB tolerance, not current GPU availability or zero driver-cache proof.'),
                  total_cost=None,limits=['Automatic RestartCount does not prove absence of manual restart; only latest retained lifecycle shown.',
                      'Configured limits/device access are not observed CPU/GPU consumption; zero limits can mean unlimited.',
                      'Container wall intervals overlap and cannot be summed as total deployment cost.',
                      'Failed-attempt metrics are retained as cost evidence, not promoted to accepted performance samples.',
                      'Early phases remain separate from C17/C21/C23; unknown formal metadata is not filled from bootstrap identities.'])
    (ROOT/'results/legacy-costs-supplement.json').write_text(json.dumps(output,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(dict(containers=len(intervals),legacy_attempts=9,shared_initializations=len(shared),probe_deficit_MiB=deficit/1024**2))


if __name__ == '__main__':
    main()
