"""Bind the one persistent Store CUDA helper to a live host/PID-namespace identity."""
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

def require_helper_only(snapshot,helper,now_ns,run,boot,allow_engine=False):
    if snapshot['run_id']!=run or snapshot['boot_id']!=boot or not snapshot['observers_ok'] or not 0<=now_ns-snapshot['last_sample_ns']<=5_000_000_000: raise RuntimeError('stale or wrong host GPU identity receipt')
    if not helper or snapshot['registered_store_helper']!=helper or helper['boot_id']!=boot: raise RuntimeError('Store helper birth/namespace identity changed')
    if snapshot['foreign_gpu_pids']: raise RuntimeError('external GPU process detected')
    if not allow_engine and not set(snapshot['gpu_pids'])<={helper['host_pid']}: raise RuntimeError('GPU engine/unknown process retained')

def require_helper_binding(helper,launch,service,boot):
    expected=dict(namespace_pid=launch['pid'],pid_namespace=launch['pid_namespace'],start_ticks=launch['start_ticks'],boot_id=boot,container_id=service['container_id'],cgroup=service['cgroup'],uid=2147)
    if not helper or helper.get('host_pid',0)<=0 or any(helper.get(key)!=value for key,value in expected.items()): raise RuntimeError('helper differs from Store/container birth registration')
