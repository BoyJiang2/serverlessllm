# Small mutually exclusive intervals

Mean seconds on primary complete blocks. These are observed operation spans, not pure IO/CUDA/generation.

| Model | Condition | n | Prep->register gap | Route->backend first output | Backend first->correct HTTP |
|---|---|---:|---:|---:|---:|
| Kimi-K2.6 | safetensors_disk_cold | 8 | 0.094468347 | 1.878032304 | 0.592469763 |
| Kimi-K2.6 | safetensors_disk_hot | 8 | 0.097631726 | 1.794219794 | 0.590644937 |
| Kimi-K2.6 | store_disk_cold | 8 | 0.098389168 | 1.849554454 | 0.581399260 |
| Kimi-K2.6 | store_pool_hit | 8 | 0.097849039 | 1.836714341 | 0.576331026 |
| GLM-5.1-FP8 | safetensors_disk_cold | 6 | 0.000239372 | 1.182353178 | 0.222864785 |
| GLM-5.1-FP8 | safetensors_disk_hot | 6 | 0.000249609 | 0.982716272 | 0.229067980 |
| GLM-5.1-FP8 | store_disk_cold | 6 | 0.000240298 | 0.990029940 | 0.239580781 |
| GLM-5.1-FP8 | store_pool_hit | 6 | 0.000229877 | 0.919944545 | 0.224149776 |

Data SHA256: 0593266d8e924e5fc21b98e32e48fe97eb7d2f16e9d6e4a31db1741bb6169a04
Kimi owner/startticks/CID/clock/request joins and all raw intervals are in the JSON. GLM arrays are direct references to prior verified plot input. No absolute clocks invented for GLM beyond that input.
