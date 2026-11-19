import hashlib, inspect, itertools
from types import SimpleNamespace
import torch
from torch.utils._python_dispatch import TorchDispatchMode
import vllm
from vllm.model_executor.layers.fused_moe.layer import FusedMoE
from vllm.model_executor.model_loader.reload.meta import get_numel_loaded, to_meta_tensor

assert not torch.cuda.is_available()
for function in (FusedMoE.weight_loader, get_numel_loaded):
    path = inspect.getsourcefile(function)
    print(path, hashlib.sha256(open(path, 'rb').read()).hexdigest())
print('runtime', vllm.__version__, torch.__version__)

class Owner:
    quant_config = None
    quant_method = type('CompressedTensorsWNA16MarlinMoEMethod', (), {})()
    _expert_map = None
    def __init__(self, rank, fused):
        self.tp_rank = rank
        self.moe_config = SimpleNamespace(is_act_and_mul=fused)
    def __getattr__(self, name):
        return inspect.getattr_static(FusedMoE, name).__get__(self, type(self))

class HostCloneBytes(TorchDispatchMode):
    def __init__(self):
        self.total = 0
    def __torch_dispatch__(self, f, types, args=(), kwargs=None):
        if f is torch.ops.aten.clone.default and args[0].device.type == 'cpu':
            self.total += args[0].numel() * args[0].element_size()
        return f(*args, **(kwargs or {}))

import base64
from vllm.model_executor.model_loader.reload import layerwise, meta
namespace = vars(meta).copy()
exec(compile(base64.b64decode(PATCHED_META_B64), 'meta.candidate.py', 'exec'), namespace)
patched_count = namespace['get_numel_loaded']


def count(owner, target, source, name, shard, eid, fn):
    loader = owner.weight_loader
    bound = inspect.signature(loader).bind(to_meta_tensor(target), source, name, shard, eid, return_success=True)
    bound.apply_defaults()
    with HostCloneBytes() as count:
        out = fn(loader, bound)
    assert bound.arguments['loaded_weight'] is source and not source.is_meta
    return out, count.total

baseline_clones = candidate_clones = cases = 0
for rank, shard, kind, fused, padded, noncontig in itertools.product(
    range(8), ('w1','w2','w3'), ('packed','scale','replicated_scale'), (True,False), (True,False), (True,False)
):
    if (kind == 'replicated_scale' and shard != 'w2') or (shard == 'w3' and not fused):
        continue
    owner = Owner(rank, fused)
    dst_shard=12
    rank_axis=96 if not padded else 83
    other=64 if not padded else 61
    source_dims=(rank_axis,other) if shard!='w2' else (other,rank_axis)
    dtype=torch.int32 if kind=='packed' else torch.bfloat16
    source = torch.arange(source_dims[0] * source_dims[1] * (2 if noncontig else 1),
                          dtype=torch.int32).to(dtype).reshape(source_dims[0],-1)
    if noncontig:
        source = source[:,::2]
    if shard=='w2':
        dims=(2, max(rank_axis,dst_shard) if kind=='replicated_scale' else dst_shard,64)
    else:
        dims=(2,64,dst_shard * (2 if fused else 1))
    target=torch.nn.Parameter(torch.full(dims,-3,dtype=dtype),requires_grad=False)
    target.is_transposed=True
    target.quant_method='group'
    target.load_full_w2=kind=='replicated_scale'
    name=f'layer.w{"2" if shard=="w2" else "13"}_weight_{"packed" if kind=="packed" else "scale"}'
    out1, bytes1 = count(owner,target,source,name,shard,1,get_numel_loaded)
    out2, bytes2 = count(owner,target,source,name,shard,1,patched_count)
    assert out1==out2 and out1[1] is True and bytes1==source.nbytes and bytes2==0, (out1,out2,bytes1,bytes2)
    got=owner.weight_loader(target,source,name,shard,1,return_success=True)
    assert got and int((target[1] != -3).count_nonzero())==out2[0]
    assert target[0].eq(-3).all()
    baseline_clones+=bytes1
    candidate_clones+=bytes2
    cases+=1

owner=Owner(7,True)
owner._expert_map=torch.tensor([0,-1])
source=torch.ones(12,64,dtype=torch.int32)
param=torch.nn.Parameter(torch.empty(1,64,24,dtype=torch.int32),requires_grad=False)
param.is_transposed=True
param.quant_method='group'
for fn in (get_numel_loaded,patched_count):
    assert count(owner,param,source,'layer.w13_weight_packed','w1',1,fn)==((0,False),0)
    try:
        count(owner,param,source,'layer.w13_weight_packed','invalid',0,fn)
    except ValueError as e:
        assert 'shard_id' in str(e)
    else:
        raise AssertionError('missing invalid shard error')

print(dict(exact_loader_cases=cases,baseline_host_clone_bytes=baseline_clones,candidate_host_clone_bytes=candidate_clones,
           tp8=True,gate_up=True,down=True,padded=True,group_scale=True,replicated=True,noncontiguous=True,nonlocal_expert=True,invalid_shard=True))

class Layer(torch.nn.Module, Owner):
    weight_loader = FusedMoE.weight_loader

    def __init__(self):
        torch.nn.Module.__init__(self)
        Owner.__init__(self, rank=5, fused=True)

    def __getattr__(self, name):
        try:
            return torch.nn.Module.__getattr__(self, name)
        except AttributeError:
            return Owner.__getattr__(self, name)

for count_fn in (get_numel_loaded, patched_count):
    mod = Layer()
    inputs = (torch.arange(96*64,dtype=torch.int32).reshape(96,64),
              torch.arange(96*64,dtype=torch.int32).reshape(96,64)+7000,
              torch.arange(64*96,dtype=torch.int32).reshape(64,96)+14000)
    expected = (torch.cat([v.narrow(0,60,12).t() for v in inputs[:2]], dim=1)[None],
                inputs[2].narrow(1,60,12).t()[None])
    postprocess_events = []
    class QuantMethodSpy:
        def process_weights_after_loading(self, target):
            for value, wanted in zip(target.parameters(), expected):
                assert torch.equal(value, wanted)
            postprocess_events.append('ran after both real copies')
    mod.quant_method = type('CompressedTensorsWNA16MarlinMoEMethod', (QuantMethodSpy,), {})()
    for name, shape in [('w13_weight_packed',(1,64,24)),('w2_weight_packed',(1,12,64))]:
        p = torch.nn.Parameter(torch.zeros(shape,dtype=torch.int32),requires_grad=False)
        p.weight_loader = mod.weight_loader
        p.is_transposed = True
        mod.register_parameter(name,p)
    original_object_ids = [id(t) for t in mod.parameters()]
    original_count, original_quant = layerwise.get_numel_loaded, layerwise.QuantizeMethodBase
    layerwise.get_numel_loaded, layerwise.QuantizeMethodBase = count_fn, QuantMethodSpy
    try:
        layerwise.record_metadata_for_reloading(mod)
        layerwise.initialize_layerwise_reload(mod)
        for i, (shard, param_name, input) in enumerate(zip(
                ('w1','w3','w2'),('w13_weight_packed','w13_weight_packed','w2_weight_packed'),inputs)):
            param = getattr(mod,param_name)
            assert param.is_meta
            assert param.weight_loader(param,input,'layer.'+param_name,shard,0,return_success=True)
            if i < 2:
                info = layerwise.get_layerwise_info(mod)
                assert info.can_load() and info.load_numel == 768*(i+1)
                assert info.loaded_weights[-1][1].arguments['loaded_weight'] is input
                assert not postprocess_events
        assert postprocess_events == ['ran after both real copies']
        assert [id(t) for t in mod.parameters()] == original_object_ids
        for value, wanted in zip(mod.parameters(),expected):
            assert torch.equal(value,wanted)
    finally:
        layerwise.get_numel_loaded, layerwise.QuantizeMethodBase = original_count, original_quant
print('layerwise_completion=identical, real_postprocess=once-after-3-leaf-copies, kernel_refs=intact')
