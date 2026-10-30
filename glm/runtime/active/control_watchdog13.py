"""Bound one private control child, reserving25s for TERM/KILL inside total budget."""
import subprocess,time

def supervise_child(child,begin_ns,budget,heartbeat,absolute_end_ns=None,cleanup_seconds=25,stop_requested=None):
    problem=None
    controlled_stop=False
    forced_kill=False
    terminate_sent_ns=None
    end_limit=begin_ns+int(budget*1e9)
    if absolute_end_ns is not None:
        if type(absolute_end_ns) is not int:raise ValueError('absolute deadline must be integer nanoseconds')
        end_limit=min(end_limit,absolute_end_ns)
    if cleanup_seconds not in (10,25):raise ValueError('unreviewed cleanup reserve')
    work_limit=end_limit-cleanup_seconds*1000000000
    if budget<=cleanup_seconds:raise ValueError('control budget must exceed cleanup reserve')
    try:
        while True:
            # Recheck elapsed even when poll reports a late successful exit.
            if time.monotonic_ns()>=work_limit: raise TimeoutError('control work budget exhausted')
            code=child.poll()
            if code is not None:
                if stop_requested is not None:raise RuntimeError('head exited before controlled stop')
                if code: raise RuntimeError('control child failed: '+str(code))
                break
            heartbeat()
            if stop_requested is not None and stop_requested():
                controlled_stop=True
                break
            time.sleep(min(2,max(0,(work_limit-time.monotonic_ns())/1e9)))
    except BaseException as error:
        problem=repr(error)
    finally:
        try:
            if child.poll() is None:
                terminate_sent_ns=time.monotonic_ns()
                child.terminate()
                try: child.wait(timeout=min(cleanup_seconds-5,max(0,(end_limit-time.monotonic_ns())/1e9)))
                except subprocess.TimeoutExpired:
                    forced_kill=True
                    child.kill()
                    child.wait(timeout=min(5,max(0,(end_limit-time.monotonic_ns())/1e9)))
        except BaseException as error:
            problem=(problem+'; ' if problem else '')+'cleanup: '+repr(error)
    if controlled_stop and (terminate_sent_ns is None or child.poll() not in (0,1,-15)):
        problem=(problem+'; ' if problem else '')+'head exited before signal or with unreviewed exit code'
    if controlled_stop and forced_kill:problem=(problem+'; ' if problem else '')+'controlled head required forced kill'
    end=time.monotonic_ns()
    alive=child.poll() is None
    if end>end_limit or alive: problem=(problem+'; ' if problem else '')+'total budget exceeded or child still live; private PID1 exit/root guard required'
    return dict(status='failed' if problem else 'passed including cleanup',error=problem,end_ns=end,child_alive_at_end=alive,cleanup_reserve_seconds=cleanup_seconds,total_budget_seconds=budget,absolute_end_ns=absolute_end_ns,controlled_stop=controlled_stop,forced_kill=forced_kill,child_returncode=child.poll(),terminate_sent_ns=terminate_sent_ns)
