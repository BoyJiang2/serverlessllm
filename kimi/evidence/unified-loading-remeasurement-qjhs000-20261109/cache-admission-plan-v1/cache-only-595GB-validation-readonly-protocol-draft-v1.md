# 595GB SF page-cache validation — readonly protocol draft

Status: DRAFT, no execution authorization. D0 is one CPU-only full read with no Store/GPU startup. Existing helper3340 already checks an explicit frozen list, reads every file once, then queries all files without further payload reads; it does not persist full-read endpoints or observe a sustained window.

The target is84SFfiles /595,204,634,151bytes /145,313,657pages. Require each file100% resident in five full-list scans anchored to actual producer read_end at0/30/60/90/120seconds, each finishing by its fixed+30second end. Record actual scan intervals; this proves sampled all-hot states, not continuous or atomic residency. Any failure is sticky; remaining scheduled query-only observations may finish inside the unchanged cap, never rewarm.

Proposed resource cap:4CPU,768GiB memory, high=infinity, swap0; global MemAvailable>=896GiB and finite effectiveancestor remaining headroom>=rounded targetpages+64GiB. Capture real pernode memory/cpuset/PSI/reclaim and allancestor limits; these choices do not guarantee persistence or diagnose the prior failure. Dependency review must first fix the no-Store SF resource condition.

Recommend2400seconds rather than2100: startup60 + read1800 + retention through finalquery150 + evidence60 + cleanup30 + release60 =2160, leaving240seconds outer margin. The1800read cap is a configured330.67MB/s requirement, not a proven RAID throughput floor. Historical248.609seconds was an operation-to-failure envelope, not exact read time.

Minimal source proposal is additive helper timing fields plus existing helper tests; any CPU-only controller must reuse existing preparation/one-shot unit patterns and avoid the native renderer service setup. No modification is made by this draft. Missing read timing after kill remains unknown. Cached pages are not automatically evicted at cleanup. See JSON for exact identity, counters, sampling, gate and authorization boundaries.
