"""Fixed control budgets, including cleanup margin; no formal IDs."""
POOL_DIAGNOSTIC_SCOPE='monitor diagnostics with resident Store only; no checkpoint or model'
POOL_DIAGNOSTIC_BUDGET=900
POOL_DIAGNOSTIC_PHASE_BUDGET=1200
POOL_DIAGNOSTIC_WINDOW=2400

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
