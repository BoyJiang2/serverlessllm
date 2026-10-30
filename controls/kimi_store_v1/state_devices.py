"""Exact named-state device declarations; no inference or tensor movement."""


def state_devices(device, device_map, names):
    import torch
    names = list(names)
    def explicit(value):
        result = torch.device(value)
        if (result.type not in ('cpu', 'cuda')
                or (result.type == 'cpu' and result.index is not None)
                or (result.type == 'cuda' and result.index is None)):
            raise ValueError('explicit CPU or indexed CUDA device required')
        return result
    default = explicit(device)
    if device_map is None:
        return dict.fromkeys(names, default)
    if not isinstance(device_map, dict) or set(device_map) != set(names):
        raise ValueError('device map must cover exactly all named state')
    return {name: explicit(value) for name, value in device_map.items()}
