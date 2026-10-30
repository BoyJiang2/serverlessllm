"""Parameter/buffer slot identity, including nonpersistent registered buffers."""


def collect_state_bindings(model):
    result = {}
    for name, tensor in model.named_parameters(remove_duplicate=False):
        result[name] = dict(kind='parameter', persistent=True, requires_grad=bool(tensor.requires_grad))
    for name, tensor in model.named_buffers(remove_duplicate=False):
        if name in result:
            raise ValueError('duplicate parameter/buffer name')
        if vars(tensor):
            raise ValueError('custom buffer attributes require an explicit policy')
        prefix, _, leaf = name.rpartition('.')
        owner = model.get_submodule(prefix) if prefix else model
        result[name] = dict(kind='buffer', persistent=leaf not in owner._non_persistent_buffers_set,
                            requires_grad=bool(tensor.requires_grad))
    return result


def validate_state_bindings(bindings, names, encodings):
    if bindings is None:
        if encodings:
            raise ValueError('encoded buffers require a state binding manifest')
        return None  # Legacy CPU layout component, never eligible for slot installation.
    if not isinstance(bindings, dict) or set(bindings) != set(names):
        raise ValueError('incomplete state binding manifest')
    for name, row in bindings.items():
        if (not isinstance(row, dict) or set(row) != {'kind', 'persistent', 'requires_grad'}
                or row['kind'] not in ('parameter', 'buffer')
                or type(row['persistent']) is not bool or type(row['requires_grad']) is not bool
                or (row['kind'] == 'parameter' and not row['persistent'])
                or (name in encodings and row['kind'] != 'buffer')):
            raise ValueError('invalid state binding role: ' + name)
    return bindings
