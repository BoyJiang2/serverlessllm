#!/usr/bin/env bash
set -euo pipefail
limit="$1"
case "$limit" in 128|512) ;; *) exit 2 ;; esac
base=/data/brick1/boyjiang/kimi-k2.6/p0/hook-cpu-pids${limit}-20261103
mkdir "$base"
cid="$(timeout 5 docker inspect --format '{{.Id}}' kimi-hook-cpu-pids${limit}-20261103)"
timeout 5 docker inspect "$cid" > "$base/container-before.json"
python3 - "$base" <<'PY'
import pathlib,json,sys,time,hashlib
p=pathlib.Path(sys.argv[1]);r=json.loads((p/'container-before.json').read_text())[0]
assert not r['State']['Running'] and r['HostConfig']['Runtime']=='runc'
assert not r['HostConfig']['DeviceRequests'] and not r['HostConfig']['Devices'] and not r['HostConfig']['Privileged']
assert r['HostConfig']['CgroupnsMode']=='private'
m={'cid':r['Id'],'created':r['Created'],'image':r['Image'],'nonce':p.name,'endpoint':'unix:///var/run/docker.sock','deadline':time.time()+25}
(p/'manifest.json').write_text(json.dumps(m,indent=2))
(p/'manifest.sha256').write_text(hashlib.sha256((p/'manifest.json').read_bytes()).hexdigest())
PY
guard=/data/brick1/boyjiang/kimi-k2.6/p0/exact-container-deadline-20261103.py
sha256sum "$guard" /data/brick1/boyjiang/kimi-k2.6/p0/hook-version-cpu-diag-20261103.py /usr/bin/nvidia-cdi-hook > "$base/scripts.sha256"
sudo -n systemd-run --unit="kimi-hook-cpu-pids${limit}-20261103" --property=User=2147 --property=Group=2147 --property=SupplementaryGroups=docker --property=Restart=no --property=RuntimeMaxSec=45 --property="StandardOutput=append:$base/guard.log" --property="StandardError=append:$base/guard.log" /usr/bin/python3 -I "$guard" "$base/manifest.json"
for attempt in {1..15}; do
  if test -s "$base/guard.log"; then break; fi
  sleep 0.2
done
python3 - "$base" <<'PY'
import json,pathlib,sys
rows=[json.loads(s) for s in (pathlib.Path(sys.argv[1])/'guard.log').read_text().splitlines() if s.startswith('{')]
assert len(rows)==1 and rows[0]['event']=='ready'
PY
timeout 5 docker start "$cid" > "$base/start.txt"
sudo -n python3 - "$cid" <<'PY' > "$base/actual-oci-fields.json"
import json,pathlib,sys
p=pathlib.Path('/run/containerd/io.containerd.runtime.v2.task/moby')/sys.argv[1]/'config.json'
d=json.loads(p.read_text());hooks=d.get('hooks',{})
assert not any('nvidia' in json.dumps(h).lower() for rows in hooks.values() for h in rows)
print(json.dumps({'hooks':hooks,'cgroupsPath':d['linux'].get('cgroupsPath'),'pids':d['linux']['resources'].get('pids'),'cpu':d['linux']['resources'].get('cpu'),'rlimits':d['process'].get('rlimits')},indent=2))
PY
timeout 25 docker wait "$cid" > "$base/wait-exit-code.txt"
timeout 5 docker logs "$cid" > "$base/worker.stdout.txt" 2> "$base/worker.stderr.txt"
date -Is > "$base/worker-finished-at.txt"
cat "$base/worker.stdout.txt"
