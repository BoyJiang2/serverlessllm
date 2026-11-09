# Independent runtime review

ACCEPT: four qualified conditions / one new complete block, each n=1. Historical cohorts are unchanged.

|Condition|Full 8-rank span s|Ingest envelope s|Post envelope s|Max post rank s|Store stop s|
|---|---:|---:|---:|---:|---:|
|safetensors_disk_hot|306.654224588|301.595182918|4.321631015|3.171078385|2.2606704868376255|
|store_disk_cold|31.048521166|25.276455937|1.495703999|0.021897705|132.5296822777018|
|safetensors_disk_cold|337.711999025|332.823070212|4.954095935|3.063216562|2.270278043113649|
|store_pool_hit|25.309462256|21.77526168|19.36292302|0.011842329|135.57414984423667|

All284 exported entries match bytes/SHA (239 raw copies,45 derived capsules;285 files including manifest). Twelve HTTP/trace chains,32 endpoint identities, cache admission, normal Store300/CID0 and guard/observer quiet>=60 pass. Storecold fixed32 differs from strict reference but satisfies the frozen length-only criterion.

Poolhit post envelope19.362923020 versus maxrank0.011842329 reveals cross-rank skew. Stage envelopes must not be summed. New n1 Figure5 observations remain separate from old40/main8 and GLM; host loading boundaries are not pure IO or synchronized device completion. Exact child native spans are retained; parent native span is missing after GC and cannot be replaced with Prep+A. Root-private full originals and live release checks remain parent-attributed provenance, not reviewer private reads. See JSON for all checks and limits.
