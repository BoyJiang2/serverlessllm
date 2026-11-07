# Existing Kimi startup-log audit

Fixed public summary independently mapped to38 qualified original samples and the actual analysis0959… / input manifest4dd7…. No new collection or workload.

## C_MODEL_LOADING_LOG_SPAN (seconds)

| Condition | All qualified n | Mean | Min | Max | Primary n | Primary mean |
|---|---:|---:|---:|---:|---:|---:|
| safetensors_disk_cold | 9 | 1118.357281 | 1094.280666 | 1143.679235 |8 | 1121.366857 |
| safetensors_disk_hot | 9 | 283.887790 | 274.176027 | 297.856367 |8 | 284.510617 |
| store_disk_cold | 10 | 89.164936 | 70.699197 | 95.486601 |8 | 87.859091 |
| store_pool_hit | 10 | 9.942815 | 8.816642 | 11.189701 |8 | 9.998397 |

Each run has exactly1 observed marker; all raw values/paths/logSHA/line numbers are retained in JSON. Rank and PID remainunknown, so this is not the8rank maximum. The f7 frozen gpu_model_runner.py times perf_counter before get_model_loader through load_model and optional LoRA/drafter/aux setup, ending before evidence capture. It is broader than GLM weight ingestion and is not pureIO, CUDA, HTTP startup or generation. Costv1 is not overwritten.

## Actual import records

All76 markers (head+worker for38runs) report identical8-package versions and6 origins matching the original source-prefix contract. The4 added wheel versions match original spec assets. The4 core version strings are observed and image-bound; the role reports rather than literal-asserts them, and this public summary does not independently reconstruct bootstrap core-before/after hash equality.

Observed versions: {"GPUtil": "1.4.0", "grpcio": "1.76.0", "msgpack": "1.1.2", "ray": "2.48.0", "speedtest-cli": "2.1.3", "torch": "2.10.0+cu129", "transformers": "5.5.1", "vllm": "0.19.1"}

Original matching source/protocol spec SHA for every run and all6 origins are in JSON. Import paths/version strings are not per-rank mapped ELF or full source-byte proof.

Root collector raw hashes are accepted through the fixed public summary; private logs were not newly re-read. No GPU/container/root recollection/Git/source modifications.

JSON SHA256: 9f69c5d1dad96abc25098b526d951bfdfe11d0896c18c7e2f5a21dcd956f4de4
