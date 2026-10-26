"""Fixed control budgets, including cleanup margin; no formal IDs."""
def require_budget(name,remaining,diagnostic=False):
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
