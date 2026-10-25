"""Record retained JIT artifacts and detect observed compilation/download jobs."""
import hashlib
import pathlib

CACHE_DIRECTORIES = ('inductor', 'triton', 'vllm', 'cuda', '.cache', '.tensorrt_llm')
TOOLS = {'nvcc', 'ptxas', 'nvlink', 'ninja', 'cmake', 'make', 'gmake',
         'gcc', 'g++', 'cc', 'c++', 'cc1', 'cc1plus', 'clang', 'clang++',
         'curl', 'wget', 'aria2c', 'hf', 'huggingface-cli', 'modelscope'}


def artifact_snapshot(home):
    home = pathlib.Path(home)
    rows = {}
    for directory in CACHE_DIRECTORIES:
        base = home/directory
        assert base.is_dir(), str(base)
        for path in sorted(base.rglob('*')):
            if path.is_file():
                data = path.read_bytes()
                rows[path.relative_to(home).as_posix()] = {
                    'bytes':len(data), 'sha256':hashlib.sha256(data).hexdigest()}
    return rows


def observed_interference(processes):
    found = []
    for row in processes:
        argv = row.get('cmdline', '').split()
        if not argv:
            continue
        executable = pathlib.PurePosixPath(argv[0]).name
        python_job = executable.startswith('python') and any(
            word in ' '.join(argv[1:]) for word in (
                'huggingface_hub.commands', 'snapshot_download', 'download_checkpoint',
                'pip install', 'build_ext', 'setup.py', 'torch.utils.cpp_extension'))
        if executable in TOOLS or python_job:
            found.append({'pid':row['pid'], 'cmdline':row['cmdline']})
    return found
