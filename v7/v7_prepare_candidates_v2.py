"""Compose V7 candidates from the deployed profiles; never launch a container."""
import copy
import hashlib
import json
from pathlib import Path


BASE = Path("/home/boyjiang/sllm-v4-pro/cache-competition-20261109")
V7 = BASE / "v7"
TARGET_SITE = "/opt/sllm-v4/lib/python3.12/site-packages"
FORMATS = {"kimi": "sllm_kimi26_postprocessed_0201", "glm": "sllm_glm51_raw_0201"}


def prepare_sleep(output):
    profiles = json.loads((output / "profiles.json").read_bytes())
    directory = output / "sleep-l1"
    directory.mkdir()
    checkpoints = dict(kimi="/mnt/raid/models/Kimi-K2.6", glm="/experiment/checkpoint",
                       v4="/mnt/raid/models/DeepSeek-V4-Pro")
    for name, profile in profiles.items():
        config = profile["backend_config"]
        config.update(pretrained_model_name_or_path=checkpoints[name],
                      load_format="safetensors", enable_sleep_mode=True,
                      worker_extension_cls="sllm_store.v7_sleep_worker.V7SleepWorkerExtension")
        config.pop("model_loader_extra_config", None)
        (directory / (name + "-input.json")).write_text(json.dumps(dict(
            name=name, scenario="sleep_l1", config=config, requests=profile["requests"],
        ), indent=2, ensure_ascii=False) + "\n")
    (directory / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")


def main():
    original = json.loads((BASE / "full-preparation-v3/profiles.json").read_bytes())
    output = V7 / "candidates"
    output.mkdir()
    entrypoints = """[console_scripts]
sllm-store = sllm_store.cli:main

[vllm.general_plugins]
sllm_v4_store = sllm_store.vllm_v4_loader:register
sllm_glm20_store = sllm_store.vllm_glm20_loader:register
sllm_kimi20_store = sllm_store.vllm_kimi20_loader:register
"""
    (output / "entry_points.txt").write_text(entrypoints)
    profiles = {}
    for name, source in original.items():
        profile = copy.deepcopy(original["v4"])
        config = copy.deepcopy(source["backend_config"])
        if name in FORMATS:
            config["load_format"] = FORMATS[name]
            config.setdefault("seed", 0)
            for src, dst in source["mounts"]:
                if dst in ("/mnt/raid/models/Kimi-K2.6", "/experiment/checkpoint"):
                    profile["mounts"].append([src, dst])
        if name == "glm":
            profile["env"].update(VLLM_USE_DEEP_GEMM_E8M0="0",
                                  SLLM_MODEL_REVISION="f396cf805182f4ca10fa675e1a99815b3ca384db")
        profile["env"].update(OMP_NUM_THREADS="1", MKL_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        for module in ("vllm_glm20_loader.py", "vllm_kimi20_loader.py", "v7_sleep_worker.py"):
            profile["mounts"].append([str(V7 / module), TARGET_SITE + "/sllm_store/" + module])
        profile["mounts"].append([
            str(output / "entry_points.txt"),
            TARGET_SITE + "/serverless_llm_store-0.8.0+v4pro.4.dist-info/entry_points.txt",
        ])
        profile["backend_config"] = config
        requests = copy.deepcopy(source["requests"])
        payload = copy.deepcopy(requests[0]["payload"])
        for key in ("prompt", "input_tokens", "messages"):
            payload.pop(key, None)
        payload.update(messages=[dict(role="user", content="请只输出数字：17 + 25 = ?")], max_tokens=16)
        requests.append(dict(name="v7_added_arithmetic", payload=payload,
                             validation="exact_numeric_answer", expected_content="42"))
        profile["requests"] = requests
        profiles[name] = profile
        (output / (name + "-input.json")).write_text(json.dumps(
            dict(name=name, config=config, requests=requests), indent=2, ensure_ascii=False) + "\n")
    (output / "profiles.json").write_text(json.dumps(profiles, indent=2) + "\n")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    (output / "receipt.json").write_text(json.dumps(dict(
        stage="candidate_not_runtime_qualified", hashes=hashes,
        source_profiles_sha256=hashlib.sha256((BASE / "full-preparation-v3/profiles.json").read_bytes()).hexdigest(),
        scope="V7 weight-only; no CPU KV service; historical requests unchanged",
    ), indent=2) + "\n")
    prepare_sleep(output)


if __name__ == "__main__":
    main()
