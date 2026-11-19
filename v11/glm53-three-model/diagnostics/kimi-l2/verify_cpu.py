
import ast
from pathlib import Path
from types import SimpleNamespace
from enum import Enum
from typing import Literal
import torch
from torch.nn.parameter import UninitializedParameter
from torch.utils._python_dispatch import TorchDispatchMode

ROOT=Path("/opt/sllm-v4/lib/python3.12/site-packages")
REL=Path("vllm/model_executor/layers/fused_moe/layer.py")
wanted={
    "weight_loader","_load_model_weight_or_group_weight_scale","_load_w13","_load_w2",
    "_load_per_channel_weight_scale","_get_hidden_dim","_narrow_expert_data_for_padding",
    "_load_single_value","_load_g_idx","_map_global_expert_id_to_local_expert_id",
}
def load_harness(path):
    tree=ast.parse(path.read_bytes(),filename=str(path))
    layer=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="FusedMoE")
    enum=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=="FusedMoeWeightScaleSupported")
    methods=[n for n in layer.body if isinstance(n,ast.FunctionDef) and n.name in wanted and not any(isinstance(d,ast.Name) and d.id=="overload" for d in n.decorator_list)]
    assert set(n.name for n in methods)==wanted
    layer.bases=[]
    layer.keywords=[]
    layer.decorator_list=[]
    layer.body=methods
    ns=dict(torch=torch,Enum=Enum,Literal=Literal,UninitializedParameter=UninitializedParameter)
    exec(compile(ast.Module(body=[enum,layer],type_ignores=[]),str(path),"exec"),ns)
    return ns["FusedMoE"],ns["FusedMoeWeightScaleSupported"]

runs=[]
for source in (ROOT/REL,Path("/check/layer.candidate.py")):
    runs.append(load_harness(source))

class Clones(TorchDispatchMode):
    def __init__(self):
        self.sizes=[]
    def __torch_dispatch__(self, func, types, args=(), kwargs=None):
        if func==torch.ops.aten.clone.default:
            self.sizes.append(args[0].numel()*args[0].element_size())
        return func(*args, **(kwargs or {}))

def call(code,rank,source,shape,shard,field,*,flags=None,transposed=True,
         method="CompressedTensorsWNA16MarlinMoEMethod",tp=8,gated=True,target_device="cpu",
         ep=None):
    cls,_=runs[code]
    layer=cls()
    layer.quant_config=None
    layer.quant_method=type(method,(),{})()
    layer.tp_rank=rank
    layer.tp_size=tp
    layer.moe_config=SimpleNamespace(is_act_and_mul=gated)
    layer._expert_map=ep
    result=torch.nn.Parameter(torch.zeros(shape,dtype=source.dtype,device=target_device),requires_grad=False)
    result.is_transposed=transposed
    for k,v in (flags or {}).items():
        setattr(result,k,v)
    with torch.no_grad(),Clones() as counter:
        success=layer.weight_loader(result,source,f"model.layers.1.mlp.experts.{field}",shard,0,
                                    return_success=True)
    return result,counter.sizes,success

def same(rank,source,shape,shard,field,**kw):
    a=call(0,rank,source,shape,shard,field,**kw)
    b=call(1,rank,source,shape,shard,field,**kw)
    assert a[0].shape==b[0].shape and a[0].stride()==b[0].stride()
    if a[0].device.type!="meta":
        assert torch.equal(a[0],b[0]),(rank,shard,field,kw)
    assert a[2]==b[2],(rank,shard,field,kw)
    return a,b

torch.set_num_threads(1)
assert not torch.cuda.is_available()
payload=0
for rank in range(8):
    for shard,shape in (("w1",(2048,896)),("w3",(2048,896)),("w2",(7168,256))):
        source=torch.arange(shape[0]*shape[1],dtype=torch.int32).reshape(shape)
        dest=(1,896,512) if shard in {"w1","w3"} else (1,32,7168)
        field="w2_weight_packed" if shard=="w2" else "w13_weight_packed"
        a,b=same(rank,source,dest,shard,field)
        assert a[1]==[7340032] and b[1]==[917504],(rank,shard,a[1],b[1])
        expected=source.narrow(1,rank*32,32).t() if shard=="w2" else source.narrow(0,rank*256,256).t()
        observed=b[0][0] if shard=="w2" else b[0][0,:,256 if shard=="w3" else 0:512 if shard=="w3" else 256]
        assert torch.equal(observed,expected),(rank,shard)
        payload+=1

f32=torch.arange(32*16,dtype=torch.float32).reshape(32,16).to(torch.bfloat16)
for rank in (0,7):
    for shard in ("w1","w3"):
        a,b=same(rank,f32,(1,16,8),shard,"w13_weight_scale",flags={"quant_method":"group"})
        assert a[1]==b[1] and b[2]
    for method in ("group","channel"):
        a,b=same(rank,f32,(1,16,32),"w2","w2_weight_scale",
                 flags={"quant_method":method,"load_full_w2":True})
        assert a[1]==b[1] and b[2] and torch.equal(b[0][0],f32.t())

w=torch.arange(32*16,dtype=torch.int32).reshape(32,16)
# Strided source retains rank-local values; compound padding retains untouched tails.
a,b=same(7,w[::2,::2],(1,9,4),"w3","w13_weight_packed")
assert a[1]==[512] and b[1]==[64]
a,b=same(7,w[:30],(1,17,8),"w1","w13_weight_packed")
assert a[1]==[30*16*4] and b[1]==[2*16*4]
# No sharding under TP1, no copy if the original helper has no available rank slice.
a,b=same(0,w,(1,16,64),"w1","w13_weight_packed",tp=1)
assert a[1]==b[1]==[2048]
a,b=same(7,w[:4],(1,16,8),"w1","w13_weight_packed")
assert a[1]==[256] and b[1]==[]
a,b=same(0,w[:4],(1,16,8),"w1","w13_weight_packed")
assert a[1]==b[1]==[256]
# Preserve untransposed/non-Marlin, meta counting and EP skipping.
for kw in (
    {"method":"CompressedTensorsWNA16MoEMethod"},
    {"target_device":"meta"},
    {"flags":{"load_full_w2":True}},
):
    a,b=same(1,w,(1,16,8),"w1","w13_weight_packed",**kw)
    assert a[1]==b[1]==[2048]
a,b=same(1,w,(1,8,16),"w1","w13_weight_packed",transposed=False,
         method="Fp8MoEMethod")
assert a[1]==b[1]==[]
a,b=same(1,w,(1,16,8),"w1","w13_weight_packed",ep=torch.tensor([-1]))
assert a[1]==b[1]==[] and not b[2]
a,b=same(1,torch.tensor([7168,2048],dtype=torch.int32),(1,2),"w1","w13_weight_shape",transposed=False)
assert a[1]==b[1]==[]

for cls,_ in runs:
    x=cls()
    x.moe_config=SimpleNamespace(is_act_and_mul=True)
    out=torch.zeros((32,16),dtype=w.dtype)
    x._load_w2(out,0,w,7,load_full=True)
    assert torch.equal(out,w)
    out=torch.zeros((32,16),dtype=w.dtype)
    x._load_w13(out,1,"w3",w[:,:8],7,load_full=True)
    assert torch.equal(out[:,8:],w[:,:8]) and not torch.count_nonzero(out[:,:8])
print(f"CPU PASS: {payload} actual-shape packings, 8 ranks, source clone 7,340,032 -> 917,504 bytes; edge/fallback cases passed")
