# Kimi existing cost evidence audit

Scope: 38 qualified original samples, not a total study-cost bill. SHA-linked public exports only; original private R0 permissions unchanged.

| Condition | n | Common env | Ray join | Pool init | Target Prep | Backend init | Delete ack | API stop | Store stop | Stop to release |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| safetensors_disk_cold | 9 | 33.164 | 7.924 | 229.450 | 49.819 | 1242.655 | 5.974 | 2.538 | 33.126 | 102.589 |
| safetensors_disk_hot | 9 | 32.585 | 7.805 | 229.203 | 163.223 | 417.050 | 5.732 | 2.417 | 32.768 | 102.112 |
| store_disk_cold | 10 | 32.725 | 7.888 | 229.501 | 26.382 | 215.964 | 5.810 | 2.449 | 32.710 | 102.004 |
| store_pool_hit | 10 | 33.772 | 7.718 | 229.624 | 91.470 | 133.016 | 5.603 | 2.555 | 32.721 | 102.016 |

Values are mean seconds. Operation boundaries are controller-observed and include communication/polling/verification overhead. Backend init overlaps A; API/Store stop overlap the stop-to-release envelope. Do not add nested intervals.

Counts: 38 fresh environments and Store pools; 9 warm-file preparations, 19 target evictions, 10 poolhit prefill admissions / 80 confirmed rank CPU loads, 10 Storecold demand attempts, 114 correct HTTP requests, 38 delete/baseline/normal Store shutdown and release sequences. This scope excludes two failed original attempts, one supplemental success and five historical launches from cost aggregation; their main denominators remain unchanged.

**C weight ingestion and pure pool-fill are missing.** Registered load metadata has no ingestion begin/end. capture_seconds measures metadata capture, not weights. Poolhit Prep includes registration/load/admission, not isolated fill. Initial/deleted baseline observation and independent release quiet each have their own >=60 records, and are not summed together.

## N>1 projection parameters

Forced-reset online average is mean(Prep+Gap+A), flat with N. For Kimi Store conditions an explicitly separate historical conversion-controller proxy of678.128942504 seconds can be charged once as proxy/N. It includes initialization/conversion/validation/normal exit/60-second release; it is not pure conversion. The original command provides no saved tighter pure-conversion monotonic pair. Historical conversion node/storage differs from online qjhs003. Later localization-copy cost is outside that proxy.

Each hypothetical cycle repeats its fixed cache/pool preparation: no continuing resident-service reuse is measured. Common environment/pool initialization and deletion/final release are outside the online window, so the curve is not full lifecycle cost. GLM conversion is unknown: its including-conversion curve stays missing, not zero. No break-even is claimed.

GLM c23-costs.json and its existing P/N+H amortization.json are received by exact SHA. The old once-paid-preparation curve has different assumptions and is not silently reused for forced-resetN. Its26-trial prep includes state-dependent previous-pool unload. Underlying large monitors were not re-read.

Raw per-run boundary timestamps, clocks, source manifests, durations, summary ranges, projection parameters and all limitations are in kimi-final-cost-evidence-audit-20261107-v1.json.

JSON SHA256: 07a44e897eec5d8b6aab2c37cfaeaacedd87201939b37d8ebc308a5e2cbaf247
