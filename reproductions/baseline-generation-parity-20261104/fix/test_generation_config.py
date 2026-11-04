import ast,os,tempfile,pathlib,json,torch
from transformers import AutoConfig,AutoModelForCausalLM,GenerationConfig
from accelerate import init_empty_weights

def loader(path,model):
    tree=ast.parse(pathlib.Path(path).read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='load_model')
    ns={'os':os,'GenerationConfig':GenerationConfig,'best_effort_load':lambda **kwargs:model,'fully_parallel_load':lambda **kwargs:model}
    exec('from __future__ import annotations\n'+ast.unparse(fn),ns)
    return ns['load_model']

for directory in ('/models/Qwen3-8B','/models/Llama-3.1-8B-Instruct'):
    config=AutoConfig.from_pretrained(directory,local_files_only=True)
    reference=GenerationConfig.from_pretrained(directory,local_files_only=True)
    with init_empty_weights():model=AutoModelForCausalLM.from_config(config)
    assert all(p.is_meta for p in model.parameters())
    original=loader('/parity/transformers.original.py',model)
    before=original('id',storage_path='/unused').generation_config.to_dict()
    expected=reference.to_dict()
    differences={k:{'old':before.get(k),'baseline':v} for k,v in expected.items() if not k.startswith('_') and before.get(k)!=v}
    assert reference.do_sample is True
    fixed=loader('/parity/transformers_compat.py',model)
    with tempfile.TemporaryDirectory() as temporary:
        cp=pathlib.Path(temporary)/'id';cp.mkdir()
        reference.save_pretrained(cp)
        for parallel in (False,True):
            model.generation_config=GenerationConfig.from_model_config(config)
            actual=fixed('id',storage_path=temporary,fully_parallel=parallel).generation_config
            assert actual.to_dict()==reference.to_dict(),(directory,parallel)
        (cp/'generation_config.json').write_text('{bad json')
        try:fixed('id',storage_path=temporary)
        except (OSError,ValueError):pass
        else:raise AssertionError('invalid generation config must not silently fall back')
        (cp/'generation_config.json').unlink()
        model.generation_config=GenerationConfig.from_model_config(config)
        default=model.generation_config.to_dict()
        assert fixed('id',storage_path=temporary).generation_config.to_dict()==default
    print(json.dumps({'model':directory,'old_config_differences':differences,'both_dispatch_paths_match_baseline':True,'invalid_file_rejected':True,'missing_file_preserves_model_defaults':True,'cuda_initialized':torch.cuda.is_initialized()}))
assert not torch.cuda.is_initialized()
