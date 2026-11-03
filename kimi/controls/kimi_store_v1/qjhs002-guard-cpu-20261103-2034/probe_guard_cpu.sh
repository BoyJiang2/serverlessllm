#!/usr/bin/env bash
set -euo pipefail
test "$(hostname)" = qjhs2
base=/data/brick1/boyjiang/kimi-k2.6/p0/guard-cpu-20261103-2034
mkdir "$base"
guard=/data/brick1/boyjiang/kimi-k2.6/p0/exact-container-deadline-20261103.py
sha256sum "$guard" > "$base/guard.sha256"
docker --host unix:///var/run/docker.sock create --name kimi-guard-cpu-20261103-2034 --runtime=runc --restart=no --network=none --user 2147:2147 --cpus=0.1 --memory=64m --memory-swap=64m --pids-limit=32 --read-only --label sllm.kimi.run=guard-cpu-20261103-2034 --entrypoint /usr/bin/sleep sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27 30 > "$base/cid"
docker --host unix:///var/run/docker.sock inspect "$(cat "$base/cid")" > "$base/container-before.json"
python3 - "$base" <<'PY'
import json,pathlib,sys,time
p=pathlib.Path(sys.argv[1]);row=json.loads((p/'container-before.json').read_text())[0]
assert not row['State']['Running'] and row['HostConfig']['Runtime']=='runc'
assert not row['HostConfig'].get('DeviceRequests') and not row['HostConfig']['Privileged']
assert not row['HostConfig']['Binds'] and not row['HostConfig']['Devices']
m={'cid':row['Id'],'created':row['Created']+'-wrong','image':row['Image'],'endpoint':'unix:///var/run/docker.sock','nonce':'guard-cpu-20261103-2034','deadline':time.time()+30}
(p/'negative.json').write_text(json.dumps(m))
PY
sudo -n systemd-run --unit=kimi-guard-negative-20261103-2034 --property=User=2147 --property=Group=2147 --property=SupplementaryGroups=docker --property=Restart=no --property=RuntimeMaxSec=45 --property="StandardOutput=append:$base/negative.log" --property="StandardError=append:$base/negative.log" /usr/bin/python3 -I "$guard" "$base/negative.json"
sleep 2
test "$(systemctl show kimi-guard-negative-20261103-2034 --property=ActiveState --value)" = failed
python3 - "$base" <<'PY'
import json,pathlib,sys,time
p=pathlib.Path(sys.argv[1]);text=(p/'negative.log').read_text()
events=[json.loads(s) for s in text.splitlines() if s.startswith('{')]
assert any(e['event']=='failed' for e in events) and not any(e['event']=='ready' for e in events)
m=json.loads((p/'negative.json').read_text());row=json.loads((p/'container-before.json').read_text())[0]
m['created']=row['Created'];m['deadline']=time.time()+25
(p/'positive.json').write_text(json.dumps(m))
PY
sudo -n systemd-run --unit=kimi-guard-positive-20261103-2034 --property=User=2147 --property=Group=2147 --property=SupplementaryGroups=docker --property=Restart=no --property=RuntimeMaxSec=45 --property="StandardOutput=append:$base/positive.log" --property="StandardError=append:$base/positive.log" /usr/bin/python3 -I "$guard" "$base/positive.json"
for attempt in {1..15}; do
  if test -s "$base/positive.log"; then break; fi
  sleep 0.2
done
python3 - "$base" <<'PY'
import json,pathlib,sys
p=pathlib.Path(sys.argv[1]);events=[json.loads(s) for s in (p/'positive.log').read_text().splitlines() if s.startswith('{')]
assert len(events)==1 and events[0]['event']=='ready'
assert events[0]['nonce']=='guard-cpu-20261103-2034'
PY
test "$(systemctl show kimi-guard-positive-20261103-2034 --property=ActiveState --value)" = active
systemctl show kimi-guard-positive-20261103-2034 --property=ActiveState,MainPID,User,Group,ExecStart,RuntimeMaxUSec > "$base/unit-before-disconnect.txt"
docker --host unix:///var/run/docker.sock start "$(cat "$base/cid")"
date -Is > "$base/start-time.txt"
cat "$base/unit-before-disconnect.txt"
echo GUARD_CPU_STARTED_DISCONNECT_NOW
