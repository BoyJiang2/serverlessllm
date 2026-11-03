#!/usr/bin/env bash
set -euo pipefail
base=/data/brick1/boyjiang/kimi-k2.6/p0/cuda-micro-20261103-2045
guard=/data/brick1/boyjiang/kimi-k2.6/p0/exact-container-gpu-deadline-5905ecd5.py
python3 - "$base" <<'PY'
import hashlib,json,pathlib,subprocess,sys,time,xml.etree.ElementTree as ET
p=pathlib.Path(sys.argv[1]);r=json.loads((p/'prepared.json').read_text())
assert time.time()+180 < 1793721600
for name,expected in r['script_sha256'].items():assert hashlib.sha256((p.parent/name).read_bytes()).hexdigest()==expected
assert subprocess.check_output(['git','-C','/data/brick1/boyjiang/ServerlessLLM','rev-parse','HEAD'],text=True).strip()==r['source_commit']
row=json.loads(subprocess.check_output(['/usr/bin/docker','--host','unix:///var/run/docker.sock','inspect',r['cid']],text=True))[0]
assert not row['State']['Running'] and row['Image']==r['image']
xml=subprocess.check_output(['/usr/bin/nvidia-smi','--id='+r['gpu_uuid'],'-q','-x'],text=True,timeout=3)
gpu=ET.fromstring(xml).find('gpu')
assert gpu.findtext('uuid')==r['gpu_uuid'] and gpu.findtext('fb_memory_usage/used')=='0 MiB'
assert not gpu.findall('processes/process_info')
(p/'gpu-baseline.xml').write_text(xml)
m={'cid':row['Id'],'created':row['Created'],'image':row['Image'],'nonce':r['nonce'],'endpoint':'unix:///var/run/docker.sock','gpu_uuid':r['gpu_uuid'],'gpu_stop_threshold_mib':2048,'deadline':time.time()+25}
with (p/'manifest.json').open('x') as out:json.dump(m,out,indent=2)
with (p/'manifest.sha256').open('x') as out:out.write(hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest()+'  manifest.json\n')
print(json.dumps({'manifest':m,'overall_budget_seconds':180,'evidence_commit':'555550114ed2499bc4778fea2a08fb59e5e0829c','booking_id':'bk-20261103T0831-183d71bd'}),flush=True)
PY
sudo -n systemd-run --unit=kimi-cuda-micro-20261103-2045 --property=User=2147 --property=Group=2147 --property=SupplementaryGroups=docker --property=Restart=no --property=RuntimeMaxSec=65 --property=MemoryMax=256M --property=TasksMax=64 --property="StandardOutput=append:$base/guard.log" --property="StandardError=append:$base/guard.log" /usr/bin/python3 -I "$guard" "$base/manifest.json"
systemctl show kimi-cuda-micro-20261103-2045 --property=ActiveState,MainPID,ExecStart,User,Group,RuntimeMaxUSec
