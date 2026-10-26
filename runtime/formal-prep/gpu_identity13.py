"""Bind the one persistent Store CUDA helper to a live host/PID-namespace identity."""
def require_monitor_sample(begin,mid,end,previous_begin=None,previous_mid=None):
    if not all(type(value) is int for value in (begin,mid,end)) or not begin<=mid<=end or end-begin>5_000_000_000:
        raise RuntimeError('slow or inconsistent monitor sample')
    if (previous_begin is None)!=(previous_mid is None):raise RuntimeError('incomplete previous sample identity')
    if previous_begin is not None:
        if not 0<begin-previous_begin<=5_000_000_000:raise RuntimeError('monitor begin-to-begin gap')
        if mid<=previous_mid:raise RuntimeError('nonmonotonic monitor')
        if mid-previous_mid>5_000_000_000:raise RuntimeError('monitor coverage/cadence failed')


def require_monitor_ready(snapshot,now_ns,run,boot):
    require_monitor_sample(snapshot['sample_begin_ns'],snapshot['last_sample_ns'],snapshot['sample_end_ns'])
    if snapshot['run_id']!=run or snapshot['boot_id']!=boot or snapshot['observers_ok'] is not True or snapshot['foreign_gpu_pids'] or snapshot['history_ok'] is not True or snapshot['first_monitor_error'] is not None or snapshot['sample_end_ns']>now_ns or not 0<=now_ns-snapshot['last_sample_ns']<=5_000_000_000:
        raise RuntimeError('stale, unhealthy or wrong host monitor receipt')


def store_identity(launch,service,processes,boot):
    matches=[]
    for process in processes:
        if not all(key in process for key in ('status','stat','cgroup','pid_namespace')): continue
        status={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in process['status'].splitlines() if ':' in line}
        nested=[int(value) for value in status.get('NSpid','').split()]
        start=process['stat'].rpartition(')')[2].split()[19]
        if nested and nested[-1]==launch['pid'] and process['pid_namespace']==launch['pid_namespace'] and start==launch['start_ticks'] and process['cgroup']==service['cgroup'] and status['Uid'].split()[0]=='2147' and launch['boot_id']==boot:
            matches.append(dict(host_pid=process['pid'],namespace_pid=launch['pid'],pid_namespace=launch['pid_namespace'],start_ticks=start,boot_id=boot,container_id=service['container_id'],cgroup=service['cgroup'],uid=2147))
    if len(matches)>1: raise RuntimeError('ambiguous Store helper identity')
    return matches[0] if matches else None

def require_helper_only(snapshot,helper,now_ns,run,boot):
    require_monitor_ready(snapshot,now_ns,run,boot)
    if not helper or snapshot['registered_store_helper']!=helper or helper['boot_id']!=boot: raise RuntimeError('Store helper birth/namespace identity changed')
    if snapshot['foreign_gpu_pids']: raise RuntimeError('external GPU process detected')
    if not set(snapshot['gpu_pids'])<={helper['host_pid']}: raise RuntimeError('GPU engine/unknown process retained')

def require_helper_binding(helper,launch,service,boot):
    expected=dict(namespace_pid=launch['pid'],pid_namespace=launch['pid_namespace'],start_ticks=launch['start_ticks'],boot_id=boot,container_id=service['container_id'],cgroup=service['cgroup'],uid=2147)
    if not helper or helper.get('host_pid',0)<=0 or any(helper.get(key)!=value for key,value in expected.items()): raise RuntimeError('helper differs from Store/container birth registration')

def gpu_pids(raw):
    return sorted({int(line.split(',',1)[0]) for line in raw.splitlines()})


def registered_store_identity(output_root,processes,boot):
    import json
    matches=[]
    for label in ('service13a','formal13'):
        launch=output_root/('service-bootstrap-'+label)/'store-numa-launch.json'
        identity=output_root/(label+'-identity.json')
        if launch.exists() and identity.exists():
            helper=store_identity(json.loads(launch.read_text()),json.loads(identity.read_text()),processes,boot)
            if helper is not None:matches.append(helper)
    if len(matches)>1:raise RuntimeError('multiple live Store helpers across native batches')
    return matches[0] if matches else None
