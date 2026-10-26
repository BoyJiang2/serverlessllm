"""Fixed control budgets, including cleanup margin; no formal IDs."""
POOL_DIAGNOSTIC_SCOPE='monitor diagnostics with resident Store only; no checkpoint or model'
POOL_DIAGNOSTIC_BUDGET=900
POOL_DIAGNOSTIC_WORK_BUDGET=540
STORE_SHUTDOWN_GRACE=240
G1_SERVICE_WORK_BUDGET=3060
POOL_DIAGNOSTIC_PHASE_BUDGET=1200
POOL_DIAGNOSTIC_WINDOW=2400
G1_PHASE_BUDGETS={'prepare':300,'ray13a':720,'ray13b':720,'service13a':3900}

def g1_required_window(phase):
    phases=tuple(G1_PHASE_BUDGETS)
    return sum(G1_PHASE_BUDGETS[name] for name in phases[phases.index(phase):])+180

def require_budget(name,remaining,diagnostic=False,pool_diagnostic=False):
    if diagnostic and pool_diagnostic:raise RuntimeError('conflicting diagnostic scopes')
    if pool_diagnostic:
        if name not in ('monitor13','headservice13a','service13a'):raise RuntimeError('pool diagnostic forbids this identity')
        if remaining<POOL_DIAGNOSTIC_WINDOW:raise RuntimeError('insufficient pool diagnostic and release reserve')
        return 0 if name=='monitor13' else POOL_DIAGNOSTIC_BUDGET
    if diagnostic:
        if name not in ('monitor13','headservice13a'):raise RuntimeError('diagnostic scope forbids this budget')
        if remaining<900:raise RuntimeError('insufficient window for diagnostic and release reserve')
        budget=0 if name=='monitor13' else 180
    elif name in ('ray13a','ray13b','headray13a','headray13b'): budget=330
    elif name in ('service13a','headservice13a'): budget=3420
    elif name=='monitor13': budget=0
    else: raise RuntimeError('unknown control identity')
    if remaining<budget+180: raise RuntimeError('insufficient window for control budget plus cleanup')
    return budget

# Prepared formal rules; isolated until the complete entry passes R1/R2.
FORMAL_BATCH_MAX_SECONDS=14400
NORMAL_BATCH_MAX_SECONDS=3900
FORMAL_INITIALIZATION_SECONDS=900
FORMAL_NORMAL_CLEANUP_SECONDS=330
FORMAL_EMERGENCY_CLEANUP_SECONDS=25
FORMAL_HEAD_STOP_SECONDS=10
FORMAL_RELEASE_SECONDS=180
FORMAL_ATTEMPT_EVIDENCE_SECONDS=180

def formal_batch_budget(begin_ns,wall_anchor_ns,resource_deadline_ns,mode='formal'):
    if any(type(value) is not int or value<=0 for value in (begin_ns,wall_anchor_ns,resource_deadline_ns)):
        raise ValueError('formal budget clock anchors must be integer nanoseconds')
    if mode not in ('normal','formal'):raise ValueError('unknown formal batch mode')
    maximum=NORMAL_BATCH_MAX_SECONDS if mode=='normal' else FORMAL_BATCH_MAX_SECONDS
    available=resource_deadline_ns-wall_anchor_ns-FORMAL_RELEASE_SECONDS*1000000000
    head_end=min(begin_ns+maximum*1000000000,begin_ns+available)
    total_end=head_end-FORMAL_HEAD_STOP_SECONDS*1000000000
    evidence_end=total_end-(FORMAL_EMERGENCY_CLEANUP_SECONDS+FORMAL_NORMAL_CLEANUP_SECONDS)*1000000000
    work_end=evidence_end-FORMAL_ATTEMPT_EVIDENCE_SECONDS*1000000000
    if work_end<=begin_ns:raise TimeoutError('no formal work budget after cleanup reserves')
    return dict(mode=mode,begin_ns=begin_ns,wall_anchor_ns=wall_anchor_ns,resource_deadline_ns=resource_deadline_ns,
                head_end_ns=head_end,total_end_ns=total_end,work_end_ns=work_end,evidence_end_ns=evidence_end,
                initialization_end_ns=min(begin_ns+FORMAL_INITIALIZATION_SECONDS*1000000000,work_end))

def arm_formal_timer(end_ns):
    import signal,time
    if type(end_ns) is not int:raise ValueError('absolute formal deadline must be integer nanoseconds')
    remaining=end_ns-time.monotonic_ns()
    if remaining<=0:raise TimeoutError('absolute formal work budget exhausted')
    signal.setitimer(signal.ITIMER_REAL,remaining/1000000000)

def formal_attempt_fits(prereg,work_end_ns,resource_deadline):
    import time
    seconds=prereg['attempt_timeout_seconds']+prereg['post_delete_observation_seconds']+FORMAL_ATTEMPT_EVIDENCE_SECONDS
    return work_end_ns-time.monotonic_ns()>=seconds*1000000000 and resource_deadline-time.time()>=seconds

def iso_time_ns(value):
    import calendar,datetime
    stamp=datetime.datetime.fromisoformat(value)
    if stamp.tzinfo is None:raise ValueError('budget timestamp must include timezone')
    return calendar.timegm(stamp.utctimetuple())*1000000000+stamp.microsecond*1000

def load_formal_batch(root,label):
    import json,pathlib
    from resource_window_control13 import load_window
    if label not in ('service13a','formal13'):raise ValueError('unknown formal worker role')
    config=json.loads((root/'run-manifest.json').read_text())
    deployment=json.loads((root/'deployment.json').read_text())
    dispatch=json.loads((root/('head'+label+'-dispatch.json')).read_text())
    boot=pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if config['scope']!='formal continuation of original forty IDs' or config['execution_purpose']!='formal-continuation':
        raise ValueError('formal budget used outside formal entry')
    if deployment['run_id']!=config['run_id'] or dispatch['spec']['name']!='head'+label or dispatch['run_id']!=config['run_id'] or dispatch['boot_id']!=config['runtime_identity']['boot_id'] or boot!=dispatch['boot_id'] or dispatch['code_commit']!=deployment['code_commit'] or dispatch['manifest_sha256']!=deployment['input_manifest_sha256']:
        raise ValueError('formal batch dispatch identity differs')
    observed=dispatch['batch_budget']
    resource_ns=iso_time_ns(load_window(root)['cleanup_deadline'])
    if resource_ns!=iso_time_ns(config['cleanup_deadline']):raise ValueError('formal resource window changed')
    if observed['wall_anchor_ns']//1000!=iso_time_ns(dispatch['dispatch_utc'])//1000:
        raise ValueError('formal wall anchor differs from original dispatch timestamp')
    expected=formal_batch_budget(dispatch['dispatch_monotonic_ns'],observed['wall_anchor_ns'],resource_ns,'normal' if label=='service13a' else 'formal')
    if observed!=expected:raise ValueError('formal batch deadline differs from issued constraints')
    return expected
