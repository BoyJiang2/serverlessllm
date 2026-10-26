import sys,os
if sys.flags.optimize or os.environ.get('PYTHONOPTIMIZE'):
    raise RuntimeError('optimized Python is forbidden')
"""A4 native HTTP create/infer/delete/recreate, after A2 and A3 gates."""
import hashlib
import json
import math
import os
import pathlib
import subprocess
import time
import urllib.request
from weight_contract import weight_state
from gpu_identity13 import require_helper_binding,require_helper_only,require_monitor_ready


def run(out, audit_weights=True):
    from workspace import run_root,manifest as run_manifest
    root = pathlib.Path('/experiment')
    assert json.loads((root/'validation/store-restore-validation.json').read_text())['status'] == 'full Store component restore validation passed'
    if not audit_weights:
        assert os.environ.get('SLLM_NORMAL_FLAGS_SMOKE') == '1'
        proof = json.loads(pathlib.Path(os.environ['SLLM_FULL_A4_PROOF_FILE']).read_text())
        assert proof['status'] == 'A4 native HTTP lifecycle checks passed' and proof['cycles'] == 2
        assert proof['weight_format'] == os.environ.get('SLLM_SERVICE_WEIGHT_FORMAT','serverless_llm')
        assert all(not os.environ.get(key) for key in ('SLLM_VERIFY_WEIGHTS',
            'SLLM_SERVICE_WEIGHT_AUDIT_DIR','VLLM_ALLOW_INSECURE_SERIALIZATION'))
    data = (root/'validation/native-gold.json').read_bytes()
    assert hashlib.sha256(data).hexdigest() == run_manifest()['reference_sha256']['native-gold.json']
    gold = json.loads(data)
    model = 'glm51-fp8'
    backend = dict(gold['engine_config'])
    backend.pop('model')
    backend.pop('load_format')
    backend.update(pretrained_model_name_or_path='/experiment/checkpoint',torch_dtype='bfloat16')
    weight_format = os.environ.get('SLLM_SERVICE_WEIGHT_FORMAT','serverless_llm')
    assert weight_format in ('serverless_llm','safetensors')
    if weight_format == 'safetensors':
        backend['load_format'] = 'safetensors'
    config = {'model':model,'backend':'vllm','num_gpus':8,'backend_config':backend,
              'placement_config':{'local_disk':['0'],'memory_pool':[]},
              'auto_scaling_config':{'metric':'concurrency','target':1,'min_instances':0,
                                     'max_instances':1,'keep_alive':3600},'router_config':{}}
    (out/'model-config.json').write_text(json.dumps(config,indent=2))
    baseline = [int(line) for line in subprocess.check_output(
        ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
    assert len(baseline) == 8
    (out/'auxiliary-gpu-baseline.json').write_text(json.dumps(baseline))
    control=run_root()
    manifest=json.loads((control/'run-manifest.json').read_text())
    launch=json.loads((out.parent/'store-numa-launch.json').read_text())
    service=json.loads((control/'service13a-identity.json').read_text())
    def host_gpu():
        snapshot=json.loads((control/'monitor-v3-ready.json').read_text())
        require_monitor_ready(snapshot,time.monotonic_ns(),manifest['run_id'],manifest['runtime_identity']['boot_id'])
        return snapshot
    stop=time.monotonic()+5
    helper=None
    while time.monotonic()<stop:
        candidate=host_gpu()['registered_store_helper']
        if candidate is not None:
            require_helper_binding(candidate,launch,service,manifest['runtime_identity']['boot_id'])
            helper=candidate
            break
        time.sleep(.1)
    assert helper is not None, 'Store helper host/PID-namespace registration unavailable'
    (out/'helper-gpu-identity.json').write_text(json.dumps(helper,indent=2))
    events = (out/'model-events.jsonl').open('x')

    def event(name,**values):
        events.write(json.dumps({'name':name,'monotonic_ns':time.monotonic_ns(),**values})+'\n')
        events.flush()

    def http(path,body=None):
        request = urllib.request.Request('http://127.0.0.1:8343'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request,timeout=1800) as response:
            assert response.status == 200
            return json.load(response)

    audit_root = pathlib.Path(os.environ['SLLM_SERVICE_WEIGHT_AUDIT_DIR'])
    known_audits = set(audit_root.iterdir()) if audit_root.exists() else set()
    baseline_audits = len(known_audits)
    coverage=[]
    for cycle in [1,2]:
        snapshot=host_gpu(); observed_ns=time.monotonic_ns()
        require_helper_only(snapshot,helper,observed_ns,manifest['run_id'],manifest['runtime_identity']['boot_id'])
        (out/f'cycle{cycle}-gpu-before.json').write_text(json.dumps(dict(monotonic_ns=observed_ns,host_gpu_snapshot=snapshot),indent=2))
        event('native_register_begin',cycle=cycle)
        assert http('/register',config) == {'status':'ok'}
        event('native_register_complete',cycle=cycle)
        order = [1,2,0]  # Complete natural-EOS business first; fixed32 is a separate test.
        for number in order:
            case, expected = gold['requests'][number],gold['results'][number]
            request = {'model':model,'input_tokens':case['input_tokens'],'temperature':0,
                       'seed':20261024,'max_tokens':case['max_tokens'],'ignore_eos':case['ignore_eos'],
                       'logprobs':1,'return_token_ids':True,'request_id':f'A4-cycle{cycle}-{case["name"]}'}
            event('native_business_request_begin',cycle=cycle,case=case['name'])
            result = http('/v1/chat/completions',request)
            (out/f'cycle{cycle}-{case["name"]}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            choice = result['choices'][0]
            assert choice['token_ids'] == expected['token_ids']
            assert choice['message']['content'] == expected['text']
            assert choice['finish_reason'] == expected['finish_reason']
            logprobs = choice['logprobs']
            assert len(logprobs) == len(choice['token_ids'])
            assert all(math.isfinite(row[str(token)]['logprob'])
                       for token,row in zip(choice['token_ids'],logprobs,strict=True))
            event('native_business_request_complete_correct',cycle=cycle,case=case['name'])
        if audit_weights:
            audits = set(pathlib.Path(os.environ['SLLM_SERVICE_WEIGHT_AUDIT_DIR']).iterdir())
            assert len(audits) == baseline_audits+cycle
            new_audits = audits-known_audits
            assert len(new_audits) == 1
            current = new_audits.pop()
            known_audits = audits
            for rank in range(8):
                actual = weight_state(json.loads((current/f'runtime-rank{rank}.json').read_text()))
                reference = weight_state(json.loads((root/'validation'/gold['repeat_evidence'][0]/f'runtime-rank{rank}.json').read_text()))
                assert actual['tensors'] == reference['tensors'], f'native service rank {rank} differs from gold'
                assert actual['kv_runtime_excluded'] == reference['kv_runtime_excluded']
                coverage.append(dict(cycle=cycle,rank=rank,weight_scale_derived_references=len(actual['tensors']),
                    dsa_workspace_references_excluded=len(actual['runtime_indices']),kv_references_excluded=len(actual['kv_runtime_excluded'])))
            event('all_native_service_rank_weights_match_gold',cycle=cycle,audit_directory=str(current))
        event('native_delete_begin',cycle=cycle)
        assert http('/delete',{'model':model})['status'] == f'deleted model {model}'
        event('native_delete_complete',cycle=cycle)
        delete_ack_ns=time.monotonic_ns()
        listed = http('/v1/models')
        (out/f'cycle{cycle}-models-after-delete.json').write_text(json.dumps(listed,indent=2))
        assert model not in {row['id'] for row in listed['models']}
        pss_path=out/f'cycle{cycle}-pss.json'
        store_pid=json.loads((out.parent/'store-numa-launch.json').read_text())['pid']
        pss_log=(out/f'cycle{cycle}-pss.log').open('x')
        pss=subprocess.Popen(['/experiment/.venv/bin/python','-u','/experiment/code/experiment/collect_pss13.py',str(pss_path),str(store_pid)],stdout=pss_log,stderr=subprocess.STDOUT)
        # Wait for sample freshness, never for a desired GPU value.
        fresh_deadline=time.monotonic()+5
        while host_gpu()['sample_begin_ns']<delete_ack_ns:
            assert time.monotonic()<fresh_deadline,'no post-delete host GPU sample within5s'
            time.sleep(.1)
        with (out/f'cycle{cycle}-idle60.jsonl').open('x') as samples:
            for second in range(61):
                shm = os.statvfs('/dev/shm')
                gpu_receipt=host_gpu(); observed_ns=time.monotonic_ns()
                samples.write(json.dumps({'second':second,'monotonic_ns':observed_ns,
                    'delete_ack_ns':delete_ack_ns,
                    'gpu':gpu_receipt['gpu'],
                    'gpu_processes':gpu_receipt['gpu_processes'],
                    'memory_current':int(pathlib.Path('/sys/fs/cgroup/memory.current').read_text()),
                    'pids_current':int(pathlib.Path('/sys/fs/cgroup/pids.current').read_text()),
                    'host_gpu_snapshot':gpu_receipt,
                    'memory_events':pathlib.Path('/sys/fs/cgroup/memory.events').read_text(),
                    'cpu_stat':pathlib.Path('/sys/fs/cgroup/cpu.stat').read_text(),
                    'shm_used_bytes':(shm.f_blocks-shm.f_bfree)*shm.f_frsize,
                    'memory_stat':pathlib.Path('/sys/fs/cgroup/memory.stat').read_text()})+'\n')
                samples.flush()
                if second<60: time.sleep(1)
        if pss.poll() is None:
            pss.kill()
            stop=time.monotonic()+5
            while pss.poll() is None and time.monotonic()<stop: time.sleep(.1)
        pss_log.close()
        assert pss.poll()==0 and json.loads(pss_path.read_text())['status']=='completed', 'bounded PSS probe incomplete; no next lifecycle started'
        from accept_control13 import validate_native_idle
        idle_rows=[json.loads(line) for line in (out/f'cycle{cycle}-idle60.jsonl').read_text().splitlines()]
        validate_native_idle(idle_rows,baseline,helper,manifest['run_id'],manifest['runtime_identity']['boot_id'])
        event('post_delete_observation_complete',cycle=cycle,seconds=60)
        remaining = [int(line) for line in subprocess.check_output(
            ['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],text=True).splitlines()]
        assert len(remaining) == 8 and all(after <= before+1024 for before,after in zip(baseline,remaining,strict=True)), 'model GPU memory retained after native delete'
    events.close()
    (out/'model-result.json').write_text(json.dumps({'status':'A4 native HTTP lifecycle checks passed' if audit_weights else 'normal-flags gold smoke lifecycle checks passed',
        'weight_audit_performed':audit_weights,'rank_coverage':coverage,
        'exclusions':'234 DSA top-k scratch references per rank are not checkpoint weights/scales; fixed count, dtype, shape and byte size checked, scratch values not compared. KV runtime and opaque kernel allocations are excluded from weight equality.',
        'weight_format':weight_format,
        'cycles':2,'tests_per_cycle':['natural-EOS arithmetic','natural-EOS translation','greedy fixed32'],
        'timing_phase':'correctness only; no B attempt consumed','host_process_resources':'resource-monitor*.jsonl; align by monotonic_ns'},indent=2))

