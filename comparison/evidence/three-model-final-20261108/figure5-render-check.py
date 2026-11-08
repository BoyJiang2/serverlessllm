import ast, csv, hashlib, json, statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
BASE=Path(__file__).resolve().parent
output=BASE/'derived'
source=(BASE/'reproduce.py').read_text(encoding='utf-8')
read=lambda p:json.loads(p.read_bytes())
primary=read(BASE/'derived/comparison.json')['arrays']
CONDITIONS=['safetensors_disk_cold','safetensors_disk_hot','store_disk_cold','store_pool_hit']
COLORS=['#3267A5','#7EAED0','#C57935','#4D9568']
labels45=['SF cold','SF hot','Store cold','Store pool']
labels45=['SF\u51b7\u8bfb','SF\u70ed\u8bfb','Store\u51b7\u8bfb','Store\u6c60\u547d\u4e2d']
mean=statistics.fmean
plt.rcParams.update({'font.sans-serif':['Microsoft YaHei','DejaVu Sans'],'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'sllm-three-model-20261108','figure.dpi':130})
export=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='export')
save_node=next(n for n in export.body if isinstance(n,ast.FunctionDef) and n.name=='save')
exec(compile(ast.Module(body=[save_node],type_ignores=[]),'actual-save','exec'))
original_save=save
checks={}
def save(fig,name,note):
    assert name=='fig5_loading'
    assert len(fig.axes[0].patches)==0
    assert 'n=0' in fig.axes[0].get_title()
    assert len(fig.axes[0].get_yticks())==0
    for ax in fig.axes[1:]:assert len(ax.patches)==12
    checks.update(no_Kimi_zero_or_f7_bars=True, Kimi_valid_n=0, GLM_stage_bars=12,V4_stage_bars=12)
    original_save(fig,name,note)
a=source.index('    fig, axes = plt.subplots(1, 3, figsize=(13, 4.7))')
b=source.index("    plt.rcParams['font.sans-serif'] = original_font",a)
block='\n'.join(line[4:] for line in source[a:b].splitlines())
exec(compile(block,'actual-figure5-consumer','exec'))
checks.update(source_sha256=hashlib.sha256((BASE/'reproduce.py').read_bytes()).hexdigest(),method='Execute actual Figure5 source block and actual save() with unchanged historical arrays; CPU-only; no model imports',artifacts={n:hashlib.sha256((output/n).read_bytes()).hexdigest() for n in ['fig5_loading.png','fig5_loading.pdf','fig5_loading.svg']})
(BASE/'figure5-render-check.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
