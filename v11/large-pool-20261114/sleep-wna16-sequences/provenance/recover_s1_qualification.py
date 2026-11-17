import csv
import fcntl
import hashlib
import json
import pathlib
import time
from v11_run_b2 import Sampler, V11, row

run = pathlib.Path('runs/B2-WNA16-S1-r2-20261117T032950Z')
def read(path):
    return json.loads(path.read_bytes())
def put(path, data):
    with path.open('x') as f:
        json.dump(data, f, indent=2)
        f.write('\n')

with (V11/'active.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not (run/'qualification.json').exists()
    assert not (run/'failure.json').exists()
    assert read(run/'release-wait.json')['zero_reached']
    level = read(run/'L2/level-result.json')
    assert ''.join(level['models']) == 'KGDGKDK'
    assert [record['stage']['kind'] for record in level['records']] == ['load']*3 + ['wake']*4
    assert level['pid_continuity'] and level['golds_passed']
    assert all(all(x['passed'] for x in record['checks'].values()) for record in level['records'])
    for filename in sorted((run/'L2').glob('worker-*-final.json')):
        state = read(filename)['State']
        assert not state['Running'] and state['ExitCode'] == 0 and state['Pid'] == 0
    assert len(list((run/'L2').glob('worker-*-final.json'))) == 3
    sampler = Sampler(run/'recovery-unused.jsonl', {'cid':None})
    release = []
    path = run/'final-release-reobserved.jsonl'
    with path.open('x') as stream:
        while not release or release[-1]['ns'] - release[0]['ns'] < 60*10**9:
            sample = sampler.sample('final-release')
            assert sample['compute_apps'] == 0 and all(sample[f'gpu{i}'] == 0 for i in range(8))
            release.append(sample)
            stream.write(json.dumps(sample)+'\n')
            stream.flush()
            time.sleep(1)
    samples = [json.loads(line) for line in (run/'resource-samples.jsonl').read_text().splitlines()] + release
    partial = run/'resource-samples.partial-after-csv-error.csv'
    (run/'resource-samples.csv').rename(partial)
    with (run/'resource-samples.csv').open('x', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for item in samples for k in item)))
        writer.writeheader()
        writer.writerows(samples)
    admission = read(run/'admission.json')
    elapsed = (release[-1]['ns'] - release[0]['ns'])/1e9
    boot = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    put(run/'qualification-recovery.json', dict(reason='CSV field mismatch after normal teardown',
          source_level_result=str(run/'L2/level-result.json'), source_release_reobserved=str(path),
          sample_count=len(release), release_seconds=elapsed, boot_id=boot,
          original_partial_csv_sha256=hashlib.sha256(partial.read_bytes()).hexdigest()))
    put(run/'qualification.json', dict(group='B2', sequence='S1', rep=2, levels=[2],
        protocol_hash=admission['protocol_hash'], models=level['models'], results=[level], failures={},
        final_release_seconds=round(elapsed,3), final_all_gpu_zero=True, passed=True,
        qualification_recovery='qualification-recovery.json'))
    print(round(elapsed,3), flush=True)
