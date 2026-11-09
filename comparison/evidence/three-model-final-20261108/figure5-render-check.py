import ast, hashlib, json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
BASE=Path(__file__).resolve().parent
output=BASE/'derived'
source=(BASE/'reproduce.py').read_text(encoding='utf-8')
read=lambda p:json.loads(p.read_bytes())
harmonized=read(output/'harmonized_metrics.json')
CONDITIONS=['safetensors_disk_cold','safetensors_disk_hot','store_disk_cold','store_pool_hit']
LABELS=['SF cold','SF hot','Store cold','Store pool']
plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'sllm-three-model-20261108','figure.dpi':130})
export=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='export')
save_node=next(n for n in export.body if isinstance(n,ast.FunctionDef) and n.name=='save')
exec(compile(ast.Module(body=[save_node],type_ignores=[]),'actual-save','exec'))
original_save=save
checks={}

def save(fig,name,note):
    assert name in ('fig5_glm_phases','fig5_v4_phases')
    assert len(fig.axes)==1 and len(fig.axes[0].patches)==12
    assert 'not common loading' in fig.axes[0].get_title()
    for group in harmonized['primary_common_loading'].values():
        assert all(item['n']==0 and item['mean_s'] is None for item in group.values())
    checks[name]=dict(stage_bars=12,scope='Separate model-specific observation, not a common loader boundary')
    original_save(fig,name,note)

a=source.index('    for model, stem, stage_labels in [')
b=source.index('    fig, axes = plt.subplots(3, 1, figsize=(13, 5.4))',a)
block='\n'.join(line[4:] for line in source[a:b].splitlines())
exec(compile(block,'actual-figure5-consumers','exec'))
checks.update(source_sha256=hashlib.sha256((BASE/'reproduce.py').read_bytes()).hexdigest(),method='Execute actual separate-model Figure5 consumers and actual save(); common metrics remain null; CPU-only',artifacts={n:hashlib.sha256((output/n).read_bytes()).hexdigest() for n in [stem+'.'+ext for stem in ['fig5_glm_phases','fig5_v4_phases'] for ext in ['png','pdf','svg']]})
(BASE/'figure5-render-check.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps(checks,indent=2))
