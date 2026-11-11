"""Re-run the target-image CPU test cohort with the new export-memory test.

Reuses the exact command recorded for candidate-cpu-v8, swaps the output
directory, and adds test_v7_glm_export.py. No GPU, no network.
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109')
V7 = BASE / 'v7'
IMAGE = 'sha256:7d6a368950d1685904baf87e86a2aa7f367c381901cf4a475936d03f2674ad6d'
NEW_TEST = 'test_v7_glm_export.py'


def main(out_name):
    template = json.loads((V7 / 'candidate-cpu-v8/receipt.json').read_bytes())['command']
    out = V7 / out_name
    out.mkdir(parents=True, exist_ok=False)
    command = []
    for part in template:
        if part.startswith('type=bind,src=') and part.endswith(',dst=/output'):
            part = 'type=bind,src=' + str(out) + ',dst=/output'
        command.append(part)
    image_index = command.index(IMAGE)
    added = ['--mount',
             'type=bind,src=' + str(V7 / NEW_TEST) +
             ',dst=/check/tests/python/' + NEW_TEST + ',readonly']
    # test_v7_sleep_admission imports the runner from /benchmarks, which needs
    # its admission module; the v8 command predates that import.
    for name in ('v7_admission.py',):
        added += ['--mount', 'type=bind,src=' + str(V7 / name) +
                  ',dst=/benchmarks/' + name + ',readonly']
    command[image_index:image_index] = added
    sources = {}
    for part in command:
        if part.startswith('type=bind,src=') and ',dst=/check/tests/python/' in part:
            name = part.rsplit('/', 1)[-1].split(',')[0]
            sources[name] = (V7 / name).read_bytes()
    import hashlib
    receipt = dict(
        output=str(out), image=IMAGE,
        sources={name: hashlib.sha256(body).hexdigest()
                 for name, body in sorted(sources.items())},
        command=command,
    )
    result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
    receipt['exit'] = result.returncode
    (out / 'stdout.log').write_text(result.stdout)
    (out / 'stderr.log').write_text(result.stderr)
    receipt['summary'] = result.stderr.strip().splitlines()[-4:]
    (out / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(dict(exit=result.returncode, summary=receipt['summary'],
                          sources=len(sources)), indent=2))
    return 0 if result.returncode == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1]))
