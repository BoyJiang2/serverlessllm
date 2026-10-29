"""CPU-only pre-export checks and byte-slice planning, not a Store loader."""
import hashlib
import json
import math

MODEL = 'moonshotai/Kimi-K2.6'
REVISION = '7eb5002f6aadc958aed6a9177b7ed26bb94011bb'
CONFIG_SHA256 = '85825ca6e18cbe539eb83ee09eedfb3f4222265929f06e9f535a6d9364f55899'
FORMAT = 'sllm-kimi-k26-int4-preprocess-v1'
DTYPE_BYTES = {'torch.int32': 4, 'torch.bfloat16': 2, 'torch.float32': 4}
MAX_CHUNK_BYTES = 2 * 1024**3


def validate_config(raw):
    if hashlib.sha256(raw).hexdigest() != CONFIG_SHA256:
        raise ValueError('config differs from frozen official bytes')
    config = json.loads(raw)
    quant = config['text_config']['quantization_config']
    if (config['model_type'] != 'kimi_k25'
            or quant['quant_method'] != 'compressed-tensors'
            or quant['format'] != 'pack-quantized'):
        raise ValueError('unsupported packed INT4 config')
    return config


def chunk_ranges(byte_length, element_bytes, chunk_bytes):
    """Slice raw storage on element boundaries; never unpack or cast INT4."""
    if any(type(x) is not int for x in (byte_length, element_bytes, chunk_bytes)):
        raise ValueError('byte counts must be integers')
    if (byte_length < 0 or element_bytes not in (2, 4)
            or byte_length % element_bytes or chunk_bytes < element_bytes
            or chunk_bytes > MAX_CHUNK_BYTES):
        raise ValueError('invalid or unbounded byte slice request')
    step = chunk_bytes // element_bytes * element_bytes
    for offset in range(0, byte_length, step):
        yield offset, min(step, byte_length - offset)


def plan_rank(config_raw, *, rank, tensor_parallel_size, revision, tensors,
              vllm_version, torch_version, chunk_bytes=64 * 1024**2):
    """Validate externally audited pre-postprocess records; no tensor I/O."""
    validate_config(config_raw)
    if (type(rank) is not int or rank not in range(8)
            or type(tensor_parallel_size) is not int or tensor_parallel_size != 8
            or revision != REVISION):
        raise ValueError('wrong revision or TP rank')
    if vllm_version != '0.19.1' or torch_version not in ('2.10.0', '2.10.0+cu129'):
        raise ValueError('unfrozen or missing backend ABI identity')
    # Validate the chunk limit even for a rank containing only empty tensors.
    list(chunk_ranges(0, 4, chunk_bytes))
    if not isinstance(tensors, dict) or not tensors:
        raise ValueError('missing rank tensor inventory')
    rows, total = {}, 0
    for name, record in tensors.items():
        if not isinstance(name, str) or not name or not isinstance(record, dict):
            raise ValueError('invalid tensor record')
        dtype, shape = record.get('dtype'), record.get('shape')
        if dtype not in DTYPE_BYTES or not isinstance(shape, list):
            raise ValueError('unreviewed dtype or missing shape: ' + name)
        if any(type(dim) is not int or dim < 0 for dim in shape):
            raise ValueError('invalid tensor shape: ' + name)
        size = math.prod(shape) * DTYPE_BYTES[dtype]
        if type(record.get('bytes')) is not int or record['bytes'] != size:
            raise ValueError('shape/dtype/byte mismatch: ' + name)
        if record.get('contiguous') is not True:
            raise ValueError('noncontiguous state needs explicit view handling: ' + name)
        step = chunk_bytes // DTYPE_BYTES[dtype] * DTYPE_BYTES[dtype]
        rows[name] = dict(shape=shape, dtype=dtype, bytes=size,
                          chunk_count=(size + step - 1) // step)
        total += size
    return dict(format=FORMAT, model=MODEL, source_revision=revision,
                config_sha256=CONFIG_SHA256, rank=rank, tensor_parallel_size=8,
                state='before_quantization_postprocessing',
                vllm_version=vllm_version, torch_version=torch_version,
                chunk_bytes=chunk_bytes, planned_transfer_bytes=total, tensors=rows,
                aliases_deduplicated=False, payload_verified=False,
                tp_mapping_verified=False, store_created=False, runtime_admitted=False)
