"""Candidate pure extraction of the C23 Docker argv construction, no execution."""
from pathlib import PurePosixPath

HOST = PurePosixPath('/data/brick1/boyjiang/glm5.1-fp8')
RUN_ALIAS = PurePosixPath('/r')


def docker_create_args(spec, run, mounts, image_id):
    # Caller must retain metadata/preflight/guard checks before Docker create.
    args = ['docker', 'create', '--network=' + spec.get('network', 'none'), '--memory=' + spec['memory'],
            '--cpus=' + str(spec['cpus']), '--shm-size=32g', '--ulimit=core=0', '--restart=no', '--cgroup-parent=' + run + '.slice',
            '--name=' + run + '-' + spec['name'], '--label=sllm.run_id=' + run,
            '--label=sllm.uid=2147', '--user=2147:2147',
            '--env=USER=boyjiang', '--env=LOGNAME=boyjiang']
    for mount in mounts:
        args += ['--mount=type=bind,src='+mount['source']+',dst='+mount['destination']+(',readonly' if mount['readonly'] else '')]
    if spec.get('gpus'):
        args += ['--gpus=all']
    else:
        args += ['--runtime=runc']
    if spec.get('cuda_toolkit'):
        args += ['--mount=type=bind,src=/usr/local/cuda-13.0,dst=/host-cuda,readonly']
    if spec.get('raw_export_hook') or spec.get('phase_trace'):
        if spec.get('raw_export_hook'):
            assert spec['env'].get('SLLM_EXPORT_RAW_STORE') == '/experiment/store/vllm/glm51-fp8'
        args += ['--mount=type=bind,src=' + str(HOST / 'archive/original-run/patches/base_loader.py')
                 + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/model_executor/model_loader/base_loader.py,readonly']
    if spec.get('phase_trace'):
        event_path=PurePosixPath(spec['env'].get('SLLM_EVENT_DIR',''))
        assert event_path.is_relative_to(RUN_ALIAS) and '..' not in event_path.parts
        args += ['--mount=type=bind,src=' + str(HOST / 'archive/original-run/patches/gpu_worker.py')
                 + ',dst=/usr/local/lib/python3.12/dist-packages/vllm/v1/worker/gpu_worker.py,readonly']
    if spec.get('host_monitor'):
        args += ['--pid=host', '--cgroupns=host']
    for key, value in spec.get('env', {}).items():
        args += ['--env=' + key + '=' + str(value)]
    return args+['--entrypoint=' + spec['argv'][0], image_id, *spec['argv'][1:]]
