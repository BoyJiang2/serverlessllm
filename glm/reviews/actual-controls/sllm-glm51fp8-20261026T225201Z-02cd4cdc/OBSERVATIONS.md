# C21 actual results and limits

Runtime `16e40e9d3a627d9c60079c7f5a56f7d012c363de`, manifest `6f456dfaaaca5bc3011ba217e481a7c107ff7186722084a1640a8472e76a4ef0`. These are native ServerlessLLM instance trials with persistent common infrastructure. They are not whole-node cold boots or a reproduction of every paper evaluation.

| Original ID | Preparation s | Weight ingestion span s | Command → backend first token s | Command → correct complete business s |
|---|---:|---:|---:|---:|
| safetensors_disk_cold_03 | 40.400 | 405.425 | 480.977 | 481.229 |
| store_disk_cold_03 | 12.127 | 121.204 | 177.868 | 178.168 |
| safetensors_disk_hot_04 | 316.975 | 69.589 | 124.854 | 125.067 |
| safetensors_disk_cold_04 | 40.158 | 397.942 | 455.058 | 455.282 |

All four terminal IDs passed three independent gold cases (natural-EOS arithmetic/translation and greedy32), all phase evidence, cache admission, native delete and61-point>=60s/PSS observation. Immutable original results are not rewritten. Global9success/5failed/26unstarted; next `store_disk_cold_04`. Same-code Store pool-hit has no formal sample in this run; normal service controls are correctness evidence and cannot fill that timing row.

The original40ID order was preserved. Four IDs finished before an actual1351.225080341s remaining-work budget failed the1440s new-ID requirement. No retry, replacement, reorder or deadline renewal. Source was archived only after complete release and all80 hashes matched.

The normal control separately completed two native formats×two cycles,12gold responses,4fixed32 and4idle/PSS/phase checks. Current normal has auditFalse/empty rank coverage; C17 full8-rank weight/scale proof remains separately bound, including its explicit GPU scratch/opaque allocation exclusions. Current formal phase evidence is not a new payload tensor audit.

Read-only post-run recomputation passed:16complete cache snapshots,2048final mincore vectors,2965482304page positions across repeated snapshots;148Safetensors and108Store timed-input closure. Two Safetensors cold and one Store cold admissions have zero resident pages; hot admission has all184621464Safetensors pages resident. Claims concern the Linux page cache only. Hardware/storage/CUDA/JIT caches are not claimed cleared. Other-asset observation precedes target preparation/final full scan. Raw vectors remain remote with compressed/uncompressed hashes; no model payloads were reread for audit.

Whole monitor3427complete samples, max duration2.084312235s/max gap3.085068272s/no partial final row. Original normal prefix SHA matches. No observed foreign GPU or owned OOM. Sampled owned cgroup peak1554038214656B; kernelpeak unavailable. Per-GPU peak99291MiB. This is not total host cache accounting.

After each instance delete the persistent Store helper remained at1769MiB/card and the716GiB host pool remained part of the deployment. They are retained infrastructure costs, not model leakage hidden as zero. Formal common initialization239.671165906s; Store SIGINT to zero exit27.270924387s, no forced kill. Preparation includes316.974538027s to establish the Safetensors hot condition; it is not removed from total cost. Normal qualification, compilation, approval waiting and earlier control/failure costs remain separate ledgers.

Final own cleanup and61-point75.789196786s release passed: all eight cards0MiB/no GPU process/no owned process, owned anonymous memory0, shmem0/unevictable0. Ordinary file-cache628815429632B remained in the owned cgroup and is retained/separately charged; disk checkpoint/Store assets preserved. Root observer units show failed/stopped after deliberate cleanup; runtime liveness is established by the complete raw monitor, not final unit labels.

Interim raw values, success counts, mean/median/nearest-rank P90 and observed min/max are in `interim-stratified-summary.json`, stratified by node/code/protocol. N=1or2 per executed condition cannot complete original10-per-condition paired uncertainty; no invented numeric CI. Old nine results use their existing historical protocol attribution; the C19 `store_pool_hit_03` remainsfailed. Historical exit137 upstream cause remainsunknown.

Next compute requires a newly verified resource window, new run-specific independent guard, pinned full prior result/origin set, current source review/admission and actual qualification. Stopped Store ready/readback credentials and old R2 do not authorize a new run. No booking was created.
