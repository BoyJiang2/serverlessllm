"""Two bounded native SLLM HTTP lifecycles; plans only unless --execute is set."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import traceback

from sllm_store.event_trace import process_identity, read_event_window
from sllm_store.v4_service import freeze_registration_config
from sllm_store.v4_state import REVISION, file_digest, write_once
from v4_evidence import choice, response_checks, validate_business_event_order
from v4_native_reference import inputs
from v4_store_recovery import stop_daemon


ROOT=Path(__file__).resolve().parents[1]
OUTPUT=Path('/output')
CONTROL=Path('/control')


def remaining(deadline, maximum):
    seconds=min(maximum,(deadline-time.monotonic_ns())/1e9-5)
    if seconds<=0:
        raise TimeoutError('Native service guard deadline exhausted')
    return seconds


def registration(store, identity):
    return freeze_registration_config(dict(model='v4-native-service',backend='vllm',num_gpus=8,
        auto_scaling_config=dict(min_instances=0,max_instances=1),backend_config=dict(
        pretrained_model_name_or_path='/mnt/raid/models/DeepSeek-V4-Pro',
        revision=REVISION,tokenizer_revision=REVISION,tokenizer_mode='deepseek_v4',
        torch_dtype='bfloat16',kv_cache_dtype='fp8',seed=20261105,
        distributed_executor_backend='mp',attention_config=dict(use_fp4_indexer_cache=False),
        tensor_parallel_size=8,pipeline_parallel_size=1,data_parallel_size=1,
        enable_expert_parallel=False,enforce_eager=True,enable_prefix_caching=False,
        max_model_len=2048,max_num_seqs=1,max_num_batched_tokens=2048,
        load_format='sllm_v4_grouped_store',model_loader_extra_config=dict(
            store_path=store,identity=identity))))


async def observed_post(client, endpoint, payload, path, deadline):
    sent=time.monotonic_ns()
    try:
        response=await client.post(endpoint,json=payload,timeout=remaining(deadline,900))
    except BaseException as error:
        write_once(path,dict(sent_ns=sent,received_ns=time.monotonic_ns(),request=payload,
                             status=None,body=None,transport_error=repr(error)))
        raise
    received=time.monotonic_ns()
    # Preserve transport bytes even when JSON, HTTP status or model validation fails.
    write_once(path,dict(sent_ns=sent,received_ns=received,request=payload,
                         status=response.status_code,body=response.text))
    response.raise_for_status()
    return response.json(),sent,received


def validate_response(actual, reference, request):
    result=choice(actual);native=choice(reference)
    checks=response_checks(actual,reference,request['purpose'])
    if (not all(checks.values()) or result['message']['content']!=native['message']['content']
            or result.get('stop_reason')!=native.get('stop_reason')
            or actual['usage']['prompt_tokens']!=len(request['input_tokens'])):
        raise ValueError('Native service response differs from frozen request/gold')
    return checks


async def wait_file(name, deadline):
    while not (OUTPUT/name).exists():
        remaining(deadline,1)
        await asyncio.sleep(.2)
    return json.loads((OUTPUT/name).read_bytes())


async def ray_command(command, name, deadline):
    with (OUTPUT/name).open('x') as stream:
        process=await asyncio.create_subprocess_exec(*command,stdout=stream,stderr=subprocess.STDOUT)
        code=await asyncio.wait_for(process.wait(),remaining(deadline,60))
    if code!=0:
        raise RuntimeError('Native Ray command failed: '+name)
    if command[1]=='stop':
        text=(OUTPUT/name).read_text()
        if 'Stopped all ' not in text or 'SIGKILL' in text or 'forcefully' in text:
            raise RuntimeError('Native Ray stop did not report complete graceful termination')


async def start_ray(role, deadline):
    address=socket.gethostbyname(socket.gethostname())
    command=['/opt/sllm-v4/bin/ray','start','--node-ip-address='+address,
             '--num-cpus='+('2' if role=='head' else '6'),
             '--num-gpus='+('0' if role=='head' else '8'),
             '--object-store-memory=268435456','--disable-usage-stats']
    if role=='head':
        command+=['--head','--port=6379','--include-dashboard=false','--resources={"control_node":1}']
    else:
        head=await wait_file('head-ray-ready.json',deadline)
        command+=['--address='+head['address']+':6379','--resources={"worker_node":1,"worker_id_v4":1}']
    await ray_command(command,role+'-ray-start.log',deadline)
    write_once(OUTPUT/(role+'-ray-ready.json'),dict(address=address,hostname=socket.gethostname()))
    return address


async def run_worker(deadline, preparation):
    await start_ray('worker',deadline)
    command=['/opt/sllm-v4/bin/sllm-store','start','--host','0.0.0.0','--port','8073',
             '--storage-path',preparation['storage_path'],'--num-thread','8','--chunk-size','32MB',
             '--mem-pool-size',str(preparation['pool_bytes'])+'B','--registration-required','true']
    with (OUTPUT/'store-daemon.log').open('x') as log:
        daemon=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
    write_once(OUTPUT/'store-daemon-started.json',dict(pid=daemon.pid,started_ns=time.monotonic_ns()))
    checked=False
    while not (OUTPUT/'head-work-finished.json').exists():
        if daemon.poll() is not None:
            raise RuntimeError('Native Store daemon exited before service work finished')
        if not checked and (OUTPUT/'controller-ready.json').exists():
            from sllm_store.client import SllmStoreClient
            client=SllmStoreClient()
            try:
                actual=await asyncio.to_thread(client.get_server_config)
                assert actual['mem_pool_size']==preparation['pool_bytes'] and actual['chunk_size']==33554432
                write_once(OUTPUT/'store-configuration-checked.json',dict(actual=actual,checked_ns=time.monotonic_ns()))
                checked=True
            finally:
                client.channel.close()
        remaining(deadline,1)
        await asyncio.sleep(.2)
    await asyncio.to_thread(stop_daemon,daemon,deadline,OUTPUT)
    await ray_command(['/opt/sllm-v4/bin/ray','stop','--grace-period=15'],'worker-ray-stop.log',deadline)
    write_once(OUTPUT/'worker-stopped.json',dict(completed_ns=time.monotonic_ns()))


async def run_head(deadline, preparation):
    address=await start_ray('head',deadline)
    await wait_file('worker-ray-ready.json',deadline)
    import httpx
    import ray
    import uvicorn
    from sllm.app_lib import create_app
    from sllm.controller import SllmController
    assert preparation['ray_namespace']=='models'
    ray.init(address=address+':6379',namespace=preparation['ray_namespace'])
    controller=ray.remote(SllmController).options(name='controller',num_cpus=1,
                 resources={'control_node':.1}).remote(dict(enable_storage_aware=False,enable_migration=False))
    await asyncio.wait_for(controller.start.remote(),remaining(deadline,900))
    write_once(OUTPUT/'controller-ready.json',dict(ready_ns=time.monotonic_ns(),
               ray_namespace=ray.get_runtime_context().namespace))
    await wait_file('store-configuration-checked.json',deadline)
    server=uvicorn.Server(uvicorn.Config(create_app(),host='0.0.0.0',port=8343,log_level='info'))
    task=asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            await task
            raise RuntimeError('Native HTTP server exited before startup')
        remaining(deadline,1)
        await asyncio.sleep(.1)
    config=registration(preparation['store_path'],preparation['identity'])
    requests=inputs(ROOT)
    gold_path=ROOT/'docs/evidence/v4-native-reference-success/successful-run/native-gold.json'
    assert file_digest(gold_path)=='9c67cea181bdfc22f56a063e5b0ffcb3bba1105d178241e8e62c6bcdd59bb4c1'
    gold=json.loads(gold_path.read_bytes())['responses']
    cycles=[]
    async with httpx.AsyncClient(base_url='http://127.0.0.1:8343') as client:
        for cycle in (1,2):
            startup=time.monotonic_ns()
            prefix='cycle'+str(cycle)+'-'
            _,_,registered=await observed_post(client,'/register',config,OUTPUT/(prefix+'register.raw.json'),deadline)
            outcomes={}
            for request in requests:
                purpose=request['purpose'];request_id=prefix+purpose
                payload=dict(request['request']['sampling'],model=config['model'],
                             messages=request['request']['messages'],return_token_ids=True,request_id=request_id)
                actual,trigger,received=await observed_post(client,'/v1/chat/completions',payload,
                                                           OUTPUT/(prefix+purpose+'.raw.json'),deadline)
                checks=validate_response(actual,gold[purpose],request)
                assert actual['id']==request_id
                outcomes[purpose]=dict(checks=checks,trigger_ns=trigger,received_ns=received)
                if purpose=='business':
                    clock=process_identity()
                    events=read_event_window(OUTPUT/'events',os.environ['SLLM_EVENT_RUN_ID'],startup,received,
                        boot_id=clock['boot_id'],time_namespace=clock['time_namespace'])
                    validate_business_event_order(events,request_id,startup,trigger,received)
                    groups={rank:set() for rank in range(8)}
                    for event in events:
                        if event['name']=='grouped_expert_postprocess_host_end':
                            groups[event['rank']].add(event['group_id'])
                    assert all(value=={f'expert_{i:04d}' for i in range(61)} for value in groups.values())
            _,_,deleted=await observed_post(client,'/delete',dict(model=config['model']),
                                            OUTPUT/(prefix+'delete.raw.json'),deadline)
            write_once(OUTPUT/(prefix+'delete-ack.json'),dict(delete_ack_ns=deleted))
            cycles.append(dict(cycle=cycle,startup_ns=startup,registered_ns=registered,
                               delete_ack_ns=deleted,responses=outcomes))
            if cycle==1:
                release=await wait_file(prefix+'unload-observation.json',deadline)
                assert release['model_unload_observed'] and release['sample_span_seconds']>=60
                assert ray.available_resources().get('GPU')==8
    await asyncio.wait_for(controller.shutdown.remote(),remaining(deadline,60))
    server.should_exit=True
    await asyncio.wait_for(task,remaining(deadline,30))
    write_once(OUTPUT/'head-work-finished.json',dict(cycles=cycles,completed_ns=time.monotonic_ns()))
    await wait_file('worker-stopped.json',deadline)
    ray.shutdown()
    await ray_command(['/opt/sllm-v4/bin/ray','stop','--grace-period=15'],'head-ray-stop.log',deadline)
    write_once(OUTPUT/'head-candidate.json',dict(cycles=cycles,normal_worker_shutdown=True,
        native_service_candidate=True,formal_id_consumed=False,physical_cleanup_verified=False))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role',choices=('head','worker'))
    parser.add_argument('--execute',action='store_true')
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps(dict(role=args.role,cycles=2,gpu_or_model_started=False,
            cpu_limit_total=8,memory_limit_total=1024**4,scope='Plan only')))
        return
    armed=json.loads((CONTROL/(args.role+'-guard/armed.json')).read_bytes())
    assert armed['purpose']=='native-service' and socket.gethostname()==armed['identity']['container_id'][:12]
    remaining(armed['deadline_ns'],1)
    preparation=json.loads((CONTROL/'preparation.json').read_bytes())
    assert preparation['source_head'] and preparation['pool_bytes']==890937278464
    try:
        asyncio.run((run_head if args.role=='head' else run_worker)(armed['deadline_ns'],preparation))
    except BaseException as error:
        write_once(OUTPUT/(args.role+'-failure.json'),dict(error=repr(error),traceback=traceback.format_exc(),
                                                       failed_ns=time.monotonic_ns()))
        raise


if __name__=='__main__':
    main()
