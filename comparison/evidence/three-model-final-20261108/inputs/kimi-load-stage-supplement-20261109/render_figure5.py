"""Standalone Figure5 supplement; existing figures and source data are never overwritten."""
import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path

FIELDS=['phase_weights_pipeline_including_allocation_span_s','phase_weight_ingestion_span_s','phase_quantization_postprocess_span_s']
CONDITIONS=['safetensors_disk_cold','safetensors_disk_hot','store_disk_cold','store_pool_hit']

def check(d):
    assert d['schema']=='three-model-figure5-kimi-supplement-v1'
    assert d['conditions']==CONDITIONS and d['block_terminal']=='THREE_STAGE_EXIT300_BLOCK_RELEASE60_PASS'
    result={m:{} for m in ['Kimi-K2.6','GLM-5.1-FP8','DeepSeek-V4-Pro']}
    for c in CONDITIONS:
        x=d['kimi'][c]; rows=x['ranks']
        assert len(rows)==8 and sorted(r['tp_rank'] for r in rows)==list(range(8))
        assert len({r['host_owner']['pid'] for r in rows})==8 and len({r['device_uuid'] for r in rows})==8
        domains={(r['clock_identity']['boot_id'],r['clock_identity']['time_namespace'],r['clock_identity']['monotonic_clock_implementation']) for r in rows}
        assert len(domains)==1
        for r in rows:
            assert r['status']=='COMPLETE' and r['run_id']==x['run_id'] and r['model_instance_id']==x['model_instance_id']
            assert r['clock_identity']['process_startticks']==r['host_owner']['startticks']
            assert r['added_cuda_synchronization'] is False
            for s in r['stages'].values():
                assert s['status']=='COMPLETE' and r['begin_ns']<=s['begin_ns']<s['end_ns']<=r['end_ns']
        full=(max(r['end_ns'] for r in rows)-min(r['begin_ns'] for r in rows))/1e9
        assert full==x['weight_load_host_all_rank_span_s']==x['weight_load_host_span_s']
        assert max(r['end_ns']-r['begin_ns'] for r in rows)/1e9==x['max_rank_weight_load_host_duration_s']
        values=[full]
        for key in ['weight_ingestion','postprocessing']:
            v=(max(r['stages'][key]['end_ns'] for r in rows)-min(r['stages'][key]['begin_ns'] for r in rows))/1e9
            assert v==x['stage_spans'][key]['span_s'];values.append(v)
        result['Kimi-K2.6'][c]=dict(n=1,raw=[[v] for v in values],means=values)
        for m,n in [('GLM-5.1-FP8',6),('DeepSeek-V4-Pro',9)]:
            a=d['historical'][m][c];assert len(a['ids'])==len(set(a['ids']))==n
            raw=[a['arrays'][f] for f in FIELDS]
            assert all(len(z)==n and all(isinstance(v,(int,float)) and math.isfinite(v) and v>0 for v in z) for z in raw)
            result[m][c]=dict(n=n,raw=raw,means=[statistics.fmean(z) for z in raw])
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path,default=Path(__file__).with_name('figure5-inputs.json'));p.add_argument('--out',type=Path);p.add_argument('--check',action='store_true');a=p.parse_args()
    data=json.loads(a.input.read_bytes());values=check(data)
    if a.check:
        print(json.dumps(dict(status='INPUT_ENDPOINTS_AND_HISTORICAL_ARRAYS_CHECKED',full_seconds={c:values['Kimi-K2.6'][c]['means'][0] for c in CONDITIONS})));return
    if a.out is None:p.error('--out must name a new directory')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    a.out.mkdir(parents=False,exist_ok=False)
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'kimi-figure5-supplement-20261109'})
    fig,axes=plt.subplots(1,3,figsize=(15,5.5),sharey=True)
    colors=['#3267A5','#C57935','#60936C'];labels=['Full host-load envelope','Weight-ingestion envelope','Postprocessing envelope']
    for ax,(model,table) in zip(axes,values.items()):
        for j,(color,label) in enumerate(zip(colors,labels)):
            ys=[table[c]['means'][j] for c in CONDITIONS]
            bars=ax.bar([i+(j-1)*.24 for i in range(4)],ys,.23,color=color,label=label)
            if j==0:ax.bar_label(bars,fmt='%.1f',padding=3,fontsize=8)
        ax.set_xticks(range(4),['SF cold','SF hot','Store cold','Store pool'],rotation=20)
        ax.set_title(model+(' | new supplement n=1' if model=='Kimi-K2.6' else ' | historical n='+str(table[CONDITIONS[0]]['n'])))
        ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
    axes[0].set_ylabel('Host envelope duration (seconds); mean across attempts')
    fig.legend(*axes[0].get_legend_handles_labels(),loc='upper center',ncol=3,frameon=False)
    caption=('Kimi: new complete four-condition block (n=1 each); original eight-block primary dataset unchanged. GLM n=6 / V4 n=9 retain original arrays.\n'
             'Full endpoints differ: Kimi selected loader call; GLM/V4 Worker outer loading flow. Descriptive comparison, not identical atomic boundaries.\n'
             'Eight-rank envelope = latest end - earliest begin; no added CUDA sync. Envelopes may overlap: do not sum bars or interpret them as pure disk I/O.')
    fig.text(.05,.02,caption,fontsize=8.5,va='bottom');fig.subplots_adjust(top=.82,bottom=.28,wspace=.12)
    for ext in ['png','svg','pdf']:fig.savefig(a.out/('figure5-loading-supplement-v1.'+ext),dpi=180,bbox_inches='tight')
    plt.close(fig)
    report=dict(input_sha256=hashlib.sha256(a.input.read_bytes()).hexdigest(),script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),matplotlib_version=matplotlib.__version__,summaries=values,disclosures=data['disclosures'])
    report['artifacts']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in a.out.iterdir() if p.is_file()}
    with (a.out/'render-manifest.json').open('x') as f:json.dump(report,f,indent=2);f.write('\n')
    print(json.dumps(dict(status='NEW_FIGURE5_CREATED_OLD_FILES_UNCHANGED',output=str(a.out),artifacts=report['artifacts'])))
if __name__=='__main__':main()
