"""Read-only qjhs003 Docker/resource snapshot; never grants runtime admission."""
import argparse
import json
from pathlib import Path
import subprocess

REMOTE = r'''
import csv,datetime,io,json,pathlib,subprocess
image='sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
def call(args):return subprocess.run(args,text=True,capture_output=True,timeout=15)
gpu=call(['nvidia-smi','--query-gpu=index,uuid,memory.used','--format=csv,noheader,nounits'])
if gpu.returncode:raise RuntimeError('GPU snapshot failed')
rows=[dict(index=int(r[0]),uuid=r[1].strip(),used_MiB=int(r[2])) for r in csv.reader(io.StringIO(gpu.stdout))]
processes=call(['nvidia-smi','--query-compute-apps=pid','--format=csv,noheader,nounits'])
if processes.returncode:raise RuntimeError('GPU process snapshot failed')
observed=call(['docker','image','inspect',image,'--format','{{.Id}}'])
mem={line.split(':')[0]:int(line.split()[1])*1024 for line in pathlib.Path('/proc/meminfo').read_text().splitlines() if line.startswith(('MemTotal:','MemAvailable:'))}
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),hostname=call(['hostname']).stdout.strip(),
 boot_id=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),gpus=rows,
 gpu_compute_pids=processes.stdout.strip().splitlines(),memory=mem,
 expected_image_id=image,observed_image_id=observed.stdout.strip(),image_inspect_exit=observed.returncode)))
'''


def assess(snapshot):
    blockers = []
    if snapshot['hostname'] != 'qjhs3':
        blockers.append('unexpected physical host')
    if snapshot['image_inspect_exit'] or snapshot['observed_image_id'] != snapshot['expected_image_id']:
        blockers.append('historical image absent or mismatched')
    cards = snapshot['gpus']
    if len(cards) != 8 or sorted(c['index'] for c in cards) != list(range(8)) or len({c['uuid'] for c in cards}) != 8:
        blockers.append('eight-card identity snapshot incomplete')
    if snapshot['gpu_compute_pids'] or any(c['used_MiB'] != 0 for c in cards):
        blockers.append('GPU resources occupied; do not start model')
    return dict(snapshot=snapshot, blockers=blockers,
                status='BLOCKED' if blockers else 'RESOURCE_SNAPSHOT_ONLY',
                admission=False, unchecked=['effective authorization window','independent guard/probe',
                                           'reviewed run identity/config','NUMA/locked/shared memory budget'])


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,help='New small JSON evidence file; never overwritten')
    args=parser.parse_args()
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12','qjhs003','python3','-'],
                          input=REMOTE,text=True,capture_output=True,check=True,timeout=60)
    report=assess(json.loads(result.stdout))
    payload=json.dumps(report,indent=2)+'\n'
    if args.output:
        with args.output.open('x',encoding='utf-8') as stream:stream.write(payload)
    print(payload,end='')
