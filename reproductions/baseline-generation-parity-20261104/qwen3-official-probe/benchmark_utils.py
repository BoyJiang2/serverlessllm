# ---------------------------------------------------------------------------- #
#  ServerlessLLM                                                               #
#  Copyright (c) ServerlessLLM Team 2024                                       #
#                                                                              #
#  Licensed under the Apache License, Version 2.0 (the "License");             #
#  you may not use this file except in compliance with the License.            #
#                                                                              #
#  You may obtain a copy of the License at                                     #
#                                                                              #
#                  http://www.apache.org/licenses/LICENSE-2.0                  #
#                                                                              #
#  Unless required by applicable law or agreed to in writing, software         #
#  distributed under the License is distributed on an "AS IS" BASIS,           #
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.    #
#  See the License for the specific language governing permissions and         #
#  limitations under the License.                                              #
# ---------------------------------------------------------------------------- #
import gc
import os
import time

import torch
from torch import nn
from tqdm import tqdm
from transformers import AutoModelForCausalLM, AutoTokenizer, GenerationConfig

from sllm_store.transformers import load_model


def print_gpu_memory(prefix=""):
    """Print GPU memory usage via PyTorch."""
    if torch.cuda.is_available():
        allocated = torch.cuda.memory_allocated() / 1024**3
        reserved = torch.cuda.memory_reserved() / 1024**3
        print(
            f"[GPU Memory {prefix}] Allocated: {allocated:.2f} GB, Reserved: {reserved:.2f} GB"
        )
    else:
        print(f"[GPU Memory {prefix}] CUDA not available")


def _warmup_cuda():
    num_gpus = torch.cuda.device_count()
    print(f"Warming up {num_gpus} GPUs")
    for i in tqdm(range(num_gpus)):
        torch.ones(1).to(f"cuda:{i}")
        torch.cuda.synchronize()


def _warmup_inference():
    print("Warming up inference")
    model_name = os.environ.get("SLLM_BENCHMARK_WARMUP_MODEL", "facebook/opt-6.7b")
    model = AutoModelForCausalLM.from_pretrained(
        model_name, torch_dtype=torch.float16, device_map="auto"
    )
    prompts = [
        "The quick brown fox jumps over the lazy dog.",
    ]
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    inputs = tokenizer(prompts, return_tensors="pt").to("cuda")
    with torch.no_grad():
        outputs = model.generate(**inputs, max_new_tokens=50)
    del outputs, tokenizer, inputs, model
    gc.collect()
    torch.cuda.empty_cache()


def benchmark_inference(model: nn.Module, model_path: str):
    # Inference
    prompts = [
        "The quick brown fox jumps over the lazy dog.",
    ]
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    inputs = tokenizer(prompts, return_tensors="pt").to("cuda")
    with torch.no_grad():
        torch.cuda.synchronize()
        start_time = time.time()
        outputs = model.generate(**inputs, max_new_tokens=50)
        torch.cuda.synchronize()
        end_time = time.time()
    output_text = tokenizer.decode(outputs[0], skip_special_tokens=True)
    end_to_end_time = end_time - start_time
    throughput = outputs.shape[1] / end_to_end_time

    details = {
        "input_token_ids": inputs["input_ids"][0].cpu().tolist(),
        "generated_token_ids": outputs[0, inputs["input_ids"].shape[1]:].cpu().tolist(),
        "generation_config": model.generation_config.to_dict(),
        "requested_max_new_tokens": 50,
    }
    details["generated_tokens"] = len(details["generated_token_ids"])
    details["new_token_throughput"] = details["generated_tokens"] / end_to_end_time
    del outputs, tokenizer, inputs
    gc.collect()
    torch.cuda.empty_cache()

    return end_to_end_time, throughput, output_text, details


def measure(
    model_name: str, model_format: str, model_dir: str, loading_order: list
):
    results = []
    print(
        f"Measuring loading time for {model_format} model={model_name}, repeating {len(loading_order)} times"
    )
    print_gpu_memory("BEFORE benchmark")
    # loading_order = torch.randperm(num_replicas)
    for sample_index, model_idx in enumerate(loading_order):
        print(f"Loading {model_name}_{model_idx}")
        print_gpu_memory(f"before load #{model_idx}")
        model_record = {"model_name": f"{model_name}_{model_idx}"}

        # Model Loading
        if model_format == "sllm":
            model_path = os.path.join(model_dir, f"{model_name}_{model_idx}")
            start_time = time.time()
            model = load_model(
                f"{model_name}_{model_idx}",
                storage_path=model_dir,
                device_map="auto",
                torch_dtype=torch.float16,
            )
            end_time = time.time()
        elif model_format == "safetensors":
            model_path = os.path.join(
                model_dir, f"{model_name}_safetensors_{model_idx}"
            )
            start_time = time.time()
            model = AutoModelForCausalLM.from_pretrained(
                model_path,
                torch_dtype=torch.float16,
                device_map="auto",
            )
            end_time = time.time()
        model_record["loading_time"] = end_time - start_time

        from accelerate import init_empty_weights
        from transformers import AutoConfig
        with init_empty_weights():
            reference_model = AutoModelForCausalLM.from_config(AutoConfig.from_pretrained('/checkpoint'), torch_dtype=torch.float16)
        actual_buffers = dict(model.named_buffers())
        for name, tensor in reference_model.named_buffers():
            actual = actual_buffers[name].detach().cpu()
            assert actual.dtype == tensor.dtype and torch.equal(actual, tensor), ('buffer mismatch', name)
        del reference_model, actual_buffers
        model_record['buffers_match_safetensors_cpu_reference'] = True
        import hashlib
        digest = hashlib.sha256()
        for name, parameter in model.named_parameters():
            digest.update(name.encode())
            digest.update(parameter.detach().cpu().contiguous().view(torch.uint8).numpy().tobytes())
        model_record['parameter_sha256'] = digest.hexdigest()

        reference_config = GenerationConfig.from_pretrained(
            os.environ.get("SLLM_BENCHMARK_SOURCE_MODEL", model_path), local_files_only=True
        )
        assert model.generation_config.to_dict() == reference_config.to_dict(), "generation config differs from SafeTensors baseline"
        sampling_seed = 20261104 + sample_index
        torch.manual_seed(sampling_seed)
        # Inference
        end_to_end_time, throughput, output_text, details = benchmark_inference(
            model, model_path
        )

        model_record["end_to_end_time"] = end_to_end_time
        model_record["throughput"] = throughput
        model_record["output_text"] = output_text

        model_record.update(details)
        model_record["sampling_seed"] = sampling_seed
        results.append(model_record)

        print_gpu_memory(f"after inference #{model_idx}")
        del model
        gc.collect()
        torch.cuda.empty_cache()
        torch.cuda.synchronize()
        print_gpu_memory(f"after cleanup #{model_idx}")

    print_gpu_memory("AFTER all benchmarks")
    return results
