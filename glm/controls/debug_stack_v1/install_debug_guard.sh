#!/bin/bash
# One-time root install of the independent debug-stack guard: a systemd timer running debug_guard.py every minute.
# Debug GPU runs refuse to start unless this timer is active and runs exactly the debug_guard.py shipped with them.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'run as root: sudo bash install_debug_guard.sh' >&2; exit 1; }
if [[ "${1:-}" == --supplement-probe-start || "${1:-}" == --supplement-probe-verify ]]; then
/usr/bin/python3 -I - "$0" "$@" <<'PY'
import hashlib,json,os,pathlib,re,signal,subprocess,sys,time
script,mode,run,holder=sys.argv[1:]
if not re.fullmatch(r'sllm-debug-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}',run) or not re.fullmatch('[0-9a-f]{32}',holder):
    raise RuntimeError('new exact supplement run/holder required')
root=pathlib.Path('/run')/run
image='sha256:aa161e3800353473bf1c4bfc312cef77eb16fdf85e45d45d274246e6de582b6f'
boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
digest=hashlib.sha256(pathlib.Path(script).read_bytes()).hexdigest()
def call(*argv):
    return subprocess.check_output(argv,text=True,timeout=30).strip()
def props(unit):
    text=call('systemctl','show',unit,'-p','Id,MainPID,Result,ExecMainStatus,ControlGroup,ExecStart,ActiveState,InvocationID,MemoryMax,MemorySwapMax,TasksMax')
    return dict(line.split('=',1) for line in text.splitlines() if '=' in line)
def process(pid):
    p=pathlib.Path('/proc')/str(pid)
    return dict(pid=pid,stat=(p/'stat').read_text(),status=(p/'status').read_text(),cgroup=(p/'cgroup').read_text())
def same_alive(p):
    path=pathlib.Path('/proc')/str(p['pid'])/'stat'
    return path.exists() and path.read_text().rsplit(')',1)[1].split()[19]==p['stat'].rsplit(')',1)[1].split()[19]
def write(name,value):
    p=root/name
    with p.open('x') as f:json.dump(value,f,indent=2)
    p.chmod(0o444)
def empty_slice(unit):
    cg=props(unit)['ControlGroup']
    p=pathlib.Path('/sys/fs/cgroup')/cg.lstrip('/') if cg else None
    return not p or not p.exists() or 'populated 0' in (p/'cgroup.events').read_text().splitlines()
def user_service(name,slice_name,*argv,extra=()):
    call('systemd-run','--unit='+name,'--uid=2147','--gid=2147','--slice='+slice_name,
         '--property=MemoryMax=64M','--property=CPUQuota=10%','--property=TasksMax=32',
         '--property=KillMode=control-group','--property=SendSIGKILL=yes',*extra,*argv)
    value=props(name+'.service');pid=int(value['MainPID'])
    if pid<=1:raise RuntimeError('probe service lacks live MainPID')
    return dict(unit=value,process=process(pid))
if mode=='--supplement-probe-start':
    if root.exists():raise RuntimeError('refuse reused root probe identity')
    root.mkdir(mode=0o755)
    if call('docker','image','inspect',image,'--format','{{.Id}}')!=image:raise RuntimeError('original image unavailable')
    host_slice=run+'-host-probe.slice';slice_probe=run+'-slice-probe.slice'
    registration=dict(run_id=run,holder=holder,boot_id=boot,installer_sha256=digest,started_ns=time.monotonic_ns(),
                      host_slice=host_slice,probe_slice=slice_probe)
    control=user_service(run+'-contrast-probe','system.slice','/bin/sleep','90')
    registration['contrast']=control
    sentinel=user_service(run+'-slice-sentinel',slice_probe,'/bin/sleep','90')
    registration['slice_sentinel']=sentinel
    cid=call('docker','create','--pull=never','--runtime=runc','--network=none','--memory=32m','--cpus=0.1',
             '--pids-limit=32','--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
             '--user=2147:2147','--cgroup-parent='+slice_probe,'--name='+run+'-cpu-probe',
             '--label=sllm.run_id='+run,'--label=sllm.holder='+holder,'--label=sllm.purpose=GLM-CPU-GUARD-PROBE',
             '--entrypoint=/bin/sleep',image,'90')
    call('docker','start',cid)
    container=json.loads(call('docker','inspect',cid))[0]
    if container['HostConfig']['Runtime']!='runc' or container['HostConfig'].get('DeviceRequests'):
        raise RuntimeError('CPU probe unexpectedly exposes GPU')
    registration['container_before']=container
    registration['container_process']=process(container['State']['Pid'])
    code='import signal,subprocess,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);subprocess.Popen([sys.executable,"-c","import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(90)"],start_new_session=True);time.sleep(90)'
    host=user_service(run+'-host-probe',host_slice,'/usr/bin/python3','-c',code,
                      extra=('--property=RuntimeMaxSec=5s','--property=TimeoutStopSec=2s'))
    registration['host']=host
    child_file=pathlib.Path('/proc')/str(host['process']['pid'])/'task'/str(host['process']['pid'])/'children'
    for _ in range(20):
        children=child_file.read_text().split()
        if children:break
        time.sleep(.05)
    if len(children)!=1:raise RuntimeError('setsid child probe not registered')
    registration['setsid_child']=process(int(children[0]))
    for item in (registration['container_process'],sentinel['process']):
        if slice_probe+'/' not in item['cgroup']:raise RuntimeError('CPU probe escaped exact slice')
    for item in (host['process'],registration['setsid_child']):
        if host_slice+'/' not in item['cgroup']:raise RuntimeError('host probe escaped exact slice')
    call('systemd-run','--unit='+run+'-slice-probe-deadline','--on-active=5s','--timer-property=AccuracySec=1ms',
         '--property=Type=oneshot','--property=RemainAfterExit=yes','/usr/bin/systemctl','kill',
         '--kill-who=all','--signal=SIGKILL',slice_probe)
    write('guard-probe-start.json',registration)
    print(json.dumps(dict(status='CPU probes started; disconnect this SSH before independent verification',run_id=run)))
else:
    if root.stat().st_uid!=0 or root.is_symlink():raise RuntimeError('untrusted root registration directory')
    registration=json.loads((root/'guard-probe-start.json').read_text())
    if any(registration[k]!=v for k,v in dict(run_id=run,holder=holder,boot_id=boot,installer_sha256=digest).items()):
        raise RuntimeError('probe registration identity changed')
    if not 8<= (time.monotonic_ns()-registration['started_ns'])/1e9 <=120:
        raise RuntimeError('probe verification must follow SSH disconnect, within8-120seconds')
    for item in [registration['host']['process'],registration['setsid_child'],registration['slice_sentinel']['process'],registration['container_process']]:
        if same_alive(item):raise RuntimeError('owned probe process survived deadline')
    if not same_alive(registration['contrast']['process']):raise RuntimeError('wrong-slice contrast did not survive')
    host=props(run+'-host-probe.service');killed=props(run+'-slice-probe-deadline.service')
    if host['Result']!='timeout' or killed['Result']!='success' or killed['ExecMainStatus']!='0':
        raise RuntimeError('actual root deadline results failed')
    if not empty_slice(registration['host_slice']) or not empty_slice(registration['probe_slice']):
        raise RuntimeError('probe cgroup remains populated')
    final=json.loads(call('docker','inspect',registration['container_before']['Id']))[0]
    if final['State']['Running'] or final['State']['ExitCode']!=137 or final['State']['OOMKilled']:
        raise RuntimeError('CPU container final state not exact-slice SIGKILL')
    call('docker','rm',final['Id'])
    call('systemctl','stop',run+'-contrast-probe.service')
    # Hold only this empty new slice until its controller service is started.
    hold=user_service(run+'-slice-hold',run+'.slice','/bin/sleep','60')
    call('systemctl','set-property','--runtime',run+'.slice','MemoryMax=1825361100800','MemorySwapMax=0','TasksMax=8192')
    armed_ns=time.monotonic_ns()
    call('systemd-run','--unit='+run+'-deadline','--on-active=3240s','--timer-property=AccuracySec=1s',
         '--property=Type=oneshot','--property=RemainAfterExit=yes','/usr/bin/systemctl','kill',
         '--kill-who=all','--signal=SIGKILL',run+'.slice')
    timer_path='/org/freedesktop/systemd1/unit/'+''.join(c if c.isalnum() else '_%02x'%ord(c) for c in run+'-deadline.timer')
    next_usec=json.loads(call('busctl','--system','--json=short','get-property','org.freedesktop.systemd1',timer_path,
                             'org.freedesktop.systemd1.Timer','NextElapseUSecMonotonic'))['data']
    proof=dict(registration,host_probe_final=host,slice_probe_final=killed,container_final=final,
               host_slice_empty=True,probe_slice_empty=True,contrast_survived=True,main_slice=props(run+'.slice'),
               main_hold=hold,deadline_timer=props(run+'-deadline.timer'),deadline_service=props(run+'-deadline.service'),
               armed_ns=armed_ns,deadline_delay_seconds=3240,deadline_next_monotonic_usec=next_usec,completed_unix=time.time(),
               guard_sha256=hashlib.sha256(pathlib.Path('/usr/local/lib/sllm-debug-guard/debug_guard.py').read_bytes()).hexdigest(),
               removed_by_guard=True,scope='actual CPU root-service and exact-slice deadline probes; not GPU/model proof')
    write('guard-verification.json',proof)
    print(json.dumps(dict(status='root CPU probe verified; exact private-slice timer armed',run_id=run,
                          proof=str(root/'guard-verification.json'))))
PY
exit 0
fi
here=$(cd "$(dirname "$0")" && pwd)
dest=/usr/local/lib/sllm-debug-guard
install -d -o root -g root -m 0755 "$dest"
install -o root -g root -m 0644 "$here/debug_guard.py" "$dest/debug_guard.py"
install -d -o root -g root -m 0755 /var/lib/sllm-debug-guard  # heartbeat of the last completed sweep
cat > /etc/systemd/system/sllm-debug-guard.service <<EOF
[Unit]
Description=Remove expired DEBUG-UNREVIEWED debug-stack containers
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I $dest/debug_guard.py
EOF
cat > /etc/systemd/system/sllm-debug-guard.timer <<EOF
[Unit]
Description=Run the debug-stack guard every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now sllm-debug-guard.timer
systemctl start sllm-debug-guard.service  # one sweep now; fails here if root cannot reach Docker
sha256sum "$dest/debug_guard.py"
cat /var/lib/sllm-debug-guard/heartbeat.json
systemctl is-active sllm-debug-guard.timer
