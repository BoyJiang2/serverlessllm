"""Full logical-byte audit of model tensors, outside performance timing."""
import hashlib
import json
import pathlib

import torch

def tensor_digest(tensor):
    cpu = tensor.detach().contiguous().reshape(-1).view(torch.uint8).cpu()
    return hashlib.sha256(memoryview(cpu.numpy())).hexdigest()

def audit_model(model, output_dir, phase):
    from vllm.distributed import get_tensor_model_parallel_rank
    rank = get_tensor_model_parallel_rank()
    output_dir = pathlib.Path(output_dir)
    values = {}
    values.update(('parameter:'+name, tensor) for name, tensor in model.named_parameters(remove_duplicate=False))
    values.update(('buffer:'+name, tensor) for name, tensor in model.named_buffers(remove_duplicate=False))
    for module_name, module in model.named_modules():
        for name, tensor in vars(module).items():
            if isinstance(tensor, torch.Tensor):
                key = f'attribute:{module_name}.{name}'
                values[key] = tensor
    rows, excluded = {}, {}
    torch.cuda.synchronize()
    for name, tensor in values.items():
        if name.rsplit('.',1)[-1] == 'kv_cache':
            excluded[name] = 'KV runtime state; not checkpoint weights'
            continue
        if tensor.device.type == 'meta':
            raise RuntimeError(f'uninitialized meta tensor: {name}')
        rows[name] = {'shape':list(tensor.shape), 'stride':list(tensor.stride()), 'dtype':str(tensor.dtype),
                      'bytes':tensor.numel()*tensor.element_size(), 'sha256':tensor_digest(tensor)}
    document = {'rank':rank, 'phase':phase, 'model_class':type(model).__name__, 'tensors':rows, 'excluded':excluded,
                'coverage':'all named parameters, named buffers, and direct tensor attributes of all nn.Modules; no claim about opaque kernel memory',
                'tensor_count':len(rows), 'logical_bytes':sum(row['bytes'] for row in rows.values())}
    path = output_dir / f'runtime-rank{rank}.json'
    assert not path.exists()
    data = (json.dumps(document,indent=2)+'\n').encode()
    path.write_bytes(data)
    return {'rank':rank,'path':str(path),'tensor_count':len(rows),'logical_bytes':document['logical_bytes'],
            'sha256':hashlib.sha256(data).hexdigest()}
