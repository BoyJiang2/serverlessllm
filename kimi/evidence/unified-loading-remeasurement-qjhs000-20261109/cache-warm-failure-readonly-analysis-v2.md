# qjhs000 cache-warm failure: read-only evidence audit

The helper completed all84files and595,204,634,151 bytes of preadv, then rejected the all-hot predicate:98,275,914/145,313,657 pages resident (67.630198%). It did not time out or report a short read. The failure occurred before HTTP/model-load endpoints; the remaining3conditions were NOT_STARTED.

59files were fully resident,4metadata files had0resident pages, and21payload shards were partial. The missing pages are concentrated in the earlier-read00001–00021 shards;00022–00064 were fully resident. This is evidence of final non-residency after full reads, not proof of a particular reclaim cause.

The unchanged helper3340bf6a uses <=8MiB scratch buffers and buffered preadv, then a separate all-file mincore pass. It does not pin cache pages; mincore windows are sequential observations, not an atomic whole-manifest state. Read bytes are not physical disk IO bytes.

## Timing limits

Construction-begin to failed-condition timestamp:248.608698536s, an upper operation envelope including helper/read/query/save/healthy/assert/failure-finalization. No exact warm-return/read-end timestamp was persisted. Overall controller elapsed1020.279747953s is not warm duration. The successful cache-admission event and second query were not reached.

## Historical q3 comparison

| Run | Read bytes | Resident/total pages | Construction→admission seconds |
|---|---:|---:|---:|
| kimi-four-v1-b05-safetensors_disk_hot | 595204634151 | 145313657/145313657 | 160.976553941 |
| kimi-four-v1-b06-safetensors_disk_hot | 595204634151 | 145313657/145313657 | 266.887481342 |
| kimi-four-v1-b07-safetensors_disk_hot | 595204634151 | 145313657/145313657 | 132.576148678 |
| kimi-four-v1-b08-safetensors_disk_hot | 595204634151 | 145313657/145313657 | 132.451102876 |
| kimi-four-v1-b10-safetensors_disk_hot | 595204634151 | 145313657/145313657 | 129.081861094 |

These5readily located primary-set receipts all show full residency; other3primary SFhot construction receipts were not located in the direct-public-directory scope. q3 NVMe and q0 RAID differ in node/storage/boot; q3 spans include a second query, so this table is not a pure-read performance comparison.

## Cause remains unresolved

Initial global MemAvailable was 1780.221GiB before640GiB Store pool initialization. That does not prove warm-phase free memory or sufficient NUMA-local cache capacity. A separate postfailure configuration receipt shows NUMA nodes0–1, controller MemoryMax/High=infinity and empty CPUAffinity/AllowedMemoryNodes, worker1536GiB/head16GiB caps and empty requested CpusetCpus/Mems. These explicit settings do not show an imposed controller cap or requested NUMA binding, but do not prove effective ancestor policies/actual allocations. Postfailure node meminfo is not failure-time state. Original per-node allocation/free/cache/reclaim, actual placement and pressure history remain unavailable. NUMA-local reclaim, concurrent memory pressure and streaming replacement behavior remain hypotheses, not findings. No live probes were run.

Original cleanup remains abnormal: worker guard quiet52.063639200s with release60_incomplete. Later separate release observation65.952254509s confirms release only and does not relabel the original guard/sample.

All18export-manifest SHA/byte checks passed. Exact references and84per-file counts are in the JSON. No Git, synchronization, source changes, new cache reads, payload reads, model/container starts or retries were performed.

Postfailure configuration source SHA256: f44a0557a22f79900dcbea873c7cf5e447db9f8efb4a2df63cb622a1ac0f04f6. This v2 appends that evidence; v1 and all raw receipts remain unchanged.
