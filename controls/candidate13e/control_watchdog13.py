"""Bound one private control child, reserving25s for TERM/KILL inside total budget."""
import subprocess,time

def supervise_child(child,begin_ns,budget,heartbeat):
    problem=None
    end_limit=begin_ns+int(budget*1e9)
    work_limit=end_limit-25_000_000_000
    if work_limit<=begin_ns: raise ValueError('control budget must exceed cleanup reserve')
    try:
        while True:
            # Recheck elapsed even when poll reports a late successful exit.
            if time.monotonic_ns()>=work_limit: raise TimeoutError('control work budget exhausted')
            code=child.poll()
            if code is not None:
                if code: raise RuntimeError('control child failed: '+str(code))
                break
            heartbeat()
            time.sleep(min(2,max(0,(work_limit-time.monotonic_ns())/1e9)))
    except BaseException as error:
        problem=repr(error)
    finally:
        try:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=min(20,max(0,(end_limit-time.monotonic_ns())/1e9)))
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=min(5,max(0,(end_limit-time.monotonic_ns())/1e9)))
        except BaseException as error:
            problem=(problem+'; ' if problem else '')+'cleanup: '+repr(error)
    end=time.monotonic_ns()
    alive=child.poll() is None
    if end>end_limit or alive: problem=(problem+'; ' if problem else '')+'total budget exceeded or child still live; private PID1 exit/root guard required'
    return dict(status='failed' if problem else 'passed including cleanup',error=problem,end_ns=end,child_alive_at_end=alive,cleanup_reserve_seconds=25,total_budget_seconds=budget)
