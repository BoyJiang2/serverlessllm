# Kimi Figure5 supplemental qjhs000 RAID cohort

Preparation is not GPU execution. New complete-block n=0 until all four conditions pass. Original Kimi40 (38qualified/2failed), eight primary complete blocks and failed first supplemental block remain unchanged. The q3 proc-confirm-v2 plan remains unexecuted.

Source: 91fb2a10481b44c9d50a2cd963ecf0ac0d250c80 (GitHub SHA verified).
Cohort: kimi-unified-loading-20261109-qjhs000-raid-v1, one four-condition block.
Order: SF hot, Store cold, SF cold, Store pool hit.
Hard budget:28920 seconds=4*7200+120. Normal Store shutdown60; every release quiet at least60. Stop later cases on failure; no retries/replacement IDs. Forced cleanup is not normal PASS.

Metric: weight_load_host_all_rank_span_s=(max(rank_end_ns)-min(rank_begin_ns))/1e9, same instance ranks0..7, host clock/boot identity verified. Begin before full model_loader.load_model (fresh construction/storage allocation included), end after successful return (assignment/restore and required quantization/layout processing included). Exclude runner LoRA/drafter/aux, device/comm init, KV/profile/warmup/graph/requests. No added CUDA synchronization. Host envelope, not pureI/O/exact device completion. Separately report max(end-begin). Missing/incomplete primary values stay null; observed partial endpoints separately retained. Scalar f7 is not this metric.

Reuse q0 /mnt/raid/models/Kimi-K2.6, /home/boyjiang/kimi-new/store-conversion-20261105-once and /home/boyjiang/kimi-new/evidence/runs, all local md0 XFS RAID bind paths. This is not q3 NVMe. Shared exports/background workloads/device-cache limits must be disclosed. Existing per-file cache helper verifies page-cache state; no globaldropcache/copy/download/conversion.

Inventory188 stat identities/60metadata SHAs;128payloads not re-read during CPU preparation. Store integrity retains historical transfer+metadata scope, not fresh independent617GB SHA. Reuse complete pins for image/loader/endpoints/nativeELFs. Historical qjhs003_observation_derivation fields are lineage only, not q0 admission.

Frozen protocol SHA b0346585d59ddc993041e94e4062a621bbb8cc7b56b9ae241c0c3a7385f4a165. User authorizes q0 GPUs and per-file cache prep, but actual final review and fresh booking/root/GPU/memory/pins/claims/guard gates still precede activation. Preparation/read-only audits do not prove runtime PASS. GLM historical phase envelopes remain separately labeled; no matched-media unified-comparison claim.
