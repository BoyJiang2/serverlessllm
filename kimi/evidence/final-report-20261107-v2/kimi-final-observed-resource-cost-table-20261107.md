# Existing observed physical-resource table

All values below are existing per-device NVML sampled-peak lower bounds. Host/cgroup numeric peaks were not present in reviewed small exports and remain null. Each run retains8device peaks, sample counts, gaps and SHA-bound baseline references in JSON. No new GPU sampling or memory-time integration.

|Run|Condition|Per-device sampled peak range MiB|Samples|Query gaps|Initial Store baseline MiB/device|
|---|---|---:|---:|---:|---:|
|kimi-four-v1-b01-safetensors_disk_hot-retry04|safetensors_disk_hot|118057–118171|1347|0|1615–1615|
|kimi-four-v1-b01-store_disk_cold-retry04|store_disk_cold|118045–118135|989|0|1615–1615|
|kimi-four-v1-b01-safetensors_disk_cold-retry04|safetensors_disk_cold|118057–118171|2194|0|1615–1615|
|kimi-four-v1-b01-store_pool_hit-retry04|store_pool_hit|118047–118135|971|0|1615–1615|
|kimi-four-v1-b02-store_pool_hit-retry02|store_pool_hit|118045–118135|974|0|1615–1615|
|kimi-four-v1-b02-safetensors_disk_cold-retry02|safetensors_disk_cold|118057–118171|2213|0|1615–1615|
|kimi-four-v1-b02-store_disk_cold-retry02|store_disk_cold|118047–118131|993|0|1615–1615|
|kimi-four-v1-b02-safetensors_disk_hot-retry02|safetensors_disk_hot|118057–118171|1549|0|1615–1615|
|kimi-four-v1-b03-store_disk_cold|store_disk_cold|118045–118133|996|0|1615–1615|
|kimi-four-v1-b03-store_pool_hit|store_pool_hit|118047–118135|987|0|1615–1615|
|kimi-four-v1-b03-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1388|0|1615–1615|
|kimi-four-v1-b03-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2282|0|1615–1615|
|kimi-four-v1-b04-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1325|0|1615–1615|
|kimi-four-v1-b04-store_disk_cold|store_disk_cold|118047–118139|999|0|1615–1615|
|kimi-four-v1-b04-store_pool_hit|store_pool_hit|118045–118135|974|0|1615–1615|
|kimi-four-v1-b05-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2211|0|1615–1615|
|kimi-four-v1-b05-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1421|0|1615–1615|
|kimi-four-v1-b05-store_pool_hit|store_pool_hit|118047–118131|986|0|1615–1615|
|kimi-four-v1-b05-store_disk_cold|store_disk_cold|118047–118139|996|0|1615–1615|
|kimi-four-v1-b06-store_pool_hit|store_pool_hit|118047–118135|976|0|1615–1615|
|kimi-four-v1-b06-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2176|0|1615–1615|
|kimi-four-v1-b06-store_disk_cold|store_disk_cold|118047–118137|993|0|1615–1615|
|kimi-four-v1-b06-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1496|0|1615–1615|
|kimi-four-v1-b07-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2243|0|1615–1615|
|kimi-four-v1-b07-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1309|0|1615–1615|
|kimi-four-v1-b07-store_pool_hit|store_pool_hit|118047–118131|978|0|1615–1615|
|kimi-four-v1-b07-store_disk_cold|store_disk_cold|118047–118137|993|0|1615–1615|
|kimi-four-v1-b08-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2212|0|1615–1615|
|kimi-four-v1-b08-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1320|0|1615–1615|
|kimi-four-v1-b08-store_pool_hit|store_pool_hit|118047–118135|979|0|1615–1615|
|kimi-four-v1-b08-store_disk_cold|store_disk_cold|118045–118135|999|0|1615–1615|
|kimi-four-v1-b09-store_pool_hit|store_pool_hit|118047–118137|976|0|1615–1615|
|kimi-four-v1-b09-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2222|0|1615–1615|
|kimi-four-v1-b09-store_disk_cold|store_disk_cold|118045–118133|988|0|1615–1615|
|kimi-four-v1-b10-store_disk_cold|store_disk_cold|118045–118135|993|0|1615–1615|
|kimi-four-v1-b10-store_pool_hit|store_pool_hit|118047–118135|976|0|1615–1615|
|kimi-four-v1-b10-safetensors_disk_hot|safetensors_disk_hot|118057–118171|1326|0|1615–1615|
|kimi-four-v1-b10-safetensors_disk_cold|safetensors_disk_cold|118057–118171|2213|0|1615–1615|

Per-device peaks need not be simultaneous. Their sum is deliberately not reported as an observed aggregate peak. GPU baselines are devicewide samples with authenticated StorePID, not a host pinned-memory count. Logger0.19GiB is an allocator delta and is not used as resource usage. PartialB04/B09 cases do not become paired primary blocks.

JSON `/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-final-observed-resource-cost-table-20261107.json` SHA256 `132e7344c863033630df84166c1f4cb3d7cb6170a0e4c6f2746ad13c985a69bd`.
