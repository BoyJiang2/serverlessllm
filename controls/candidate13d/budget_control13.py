"""Fixed control budgets, including cleanup margin; no formal IDs."""
def require_budget(name,remaining):
    if name in ('ray13a','ray13b','headray13a','headray13b'): budget=330
    elif name in ('service13a','headservice13a'): budget=3420
    elif name=='monitor13': budget=0
    else: raise RuntimeError('unknown control identity')
    if remaining<budget+180: raise RuntimeError('insufficient window for control budget plus cleanup')
    return budget
