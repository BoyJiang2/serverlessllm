"""Stage the completed L2 repeat evidence for a non-overwriting GitHub import."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil


LIMIT = 4 * 1024**2
FIELDS = ('server_path', 'bytes', 'sha256', 'published_path')
PROVENANCE = ('campaign.json', 'v11_run_b2.py', 'v11_sleep_backend.py',
              'v7_sleep_worker.py', 'sf_admission.py', 'audit_sequences.py',
              'audit_wna16.py', 'recompute_sleep.py', 'recompute_readiness.py',
              'wna16-reload.patch', 'layerwise.py', 'cumem.py', 'candidate-manifest.json')
PINNED = {
    'qualification.json', 'level-result.json', 'campaign-input.json', 'admission.json',
    'booking.json', 'instrumentation.json', 'release-wait.json', 'failure.json',
    'final-release.jsonl', 'worker-imports.json', 'constructed.json', 'ready.json', 'armed.json',
    'switch-events.csv',
}


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    sha = hashlib.sha256()
    size = 0
    with path.open('rb') as stream:
        while data := stream.read(1024**2):
            sha.update(data)
            size += len(data)
    return size, sha.hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as original, target.open('xb') as published:
        shutil.copyfileobj(original, published)


def record(source, root, published):
    size, sha = digest(source)
    if published:
        target = root / published
        copy(source, target)
        if digest(target) != (size, sha):
            raise ValueError(f'File changed during publication: {source}')
    return str(source), size, sha, published


def run_files(run):
    for path in sorted(run.rglob('*')):
        rel = path.relative_to(run)
        if {'cache', '.cache', '__pycache__'}.intersection(rel.parts):
            continue
        if path.is_file() and not path.is_symlink():
            yield path, rel


def publish_run(run, out, audited):
    qualification = run / 'qualification.json'
    if qualification.is_file():
        q = read(qualification)
        assert audited['passed'] == q['passed'], run.name
        if q['passed']:
            assert q['final_all_gpu_zero'] and q['final_release_seconds'] >= 60, run.name
    folder = out / run.name
    folder.mkdir()
    (folder / '.gitattributes').write_bytes(b'* -text\n')
    with (folder / 'evidence-index.csv').open('x', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        for path, rel in run_files(run):
            small = path.stat().st_size < LIMIT
            selected = (path.name in PINNED or path.name.endswith((
                '-phases.json', '-correctness-passed.json', '-checks.json', '-response.json',
                '-vram-gate.json', '-memory-before.json', '-memory-after.json', '-final.json'))
                or path.parent.name == 'b2-inputs' and path.name.endswith('-input.json')
                or path.parent.name == 'events' and path.suffix == '.jsonl'
                or path.name == 'summary.json' and path.parent.name == 'sf-admission')
            writer.writerow(record(path, folder, rel.as_posix() if small and selected else ''))
    print(run.name, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign', type=Path, help='source campaign with terminal L2 runs')
    parser.add_argument('audit', type=Path, help='combined audit of the historical and repeat runs')
    parser.add_argument('output', type=Path, help='new staging directory')
    args = parser.parse_args()
    campaign, out = args.campaign.resolve(), args.output.resolve()
    audited = {row['run']: row for row in read(args.audit)['rounds']}
    runs = [path for path in sorted((campaign / 'runs').glob('B2-WNA16-S*'))
            if path.name in audited]
    assert runs, f'No audited L2 runs in {campaign}'
    assert all(path.name in audited for path in (campaign / 'runs').glob('B2-WNA16-S*')
               if (path / 'qualification.json').is_file() or (path / 'failure.json').is_file())
    runners = [campaign / 'v11_run_b2.py', *campaign.glob('v11_run_b2-used-by-l2-*.py')]
    runner_pins = {digest(path)[1] for path in runners}
    for run in runs:
        pins = run / 'instrumentation.json'
        if not pins.is_file():
            continue
        for name, sha in read(pins).items():
            allowed = runner_pins if name == 'v11_run_b2.py' else {digest(campaign / name)[1]}
            assert sha in allowed, (run.name, name, sha)
    out.mkdir()
    provenance = out / 'provenance' / f'{campaign.name}-{out.name}'
    provenance.mkdir(parents=True)
    (provenance / '.gitattributes').write_bytes(b'* -text\n')
    audit_path = (provenance / 'combined-audit.json').relative_to(out).as_posix()
    audit_entry = record(args.audit.resolve(), out, audit_path)
    copy(out / audit_path, out / 'audit.json')
    controls = {campaign / name for name in PROVENANCE}
    controls.add(Path(__file__).resolve())
    for pattern in ('*L2*-controller.*.log', 'S?-controller.*.log', 'S?-r*-controller.*.log',
                    'l2-batch*.log', 'l2-r*-batch.log', 'l2-r*-handoff.log', 'run-l2-*.sh',
                    'start-l2-r*-*.sh', 'v11_run_b2-used-by-l2-*.py'):
        controls.update(campaign.glob(pattern))
    with (provenance / 'evidence-index.csv').open('x', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(FIELDS)
        writer.writerow(audit_entry)
        for path in sorted(controls):
            target = (provenance / path.name).relative_to(out).as_posix()
            writer.writerow(record(path, out, target if path.stat().st_size < LIMIT else ''))
        config = read(campaign / 'campaign.json')
        for group in ('kimi_overlays', 'expected_v4_overlay'):
            for item in config[group]:
                path = Path(item['source'])
                sha = item.get('candidate_sha256', item.get('sha256'))
                assert digest(path)[1] == sha, path
                target = (provenance / group / path.name).relative_to(out).as_posix()
                writer.writerow(record(path, out, target))
    for run in runs:
        publish_run(run, out, audited[run.name])


if __name__ == '__main__':
    main()
