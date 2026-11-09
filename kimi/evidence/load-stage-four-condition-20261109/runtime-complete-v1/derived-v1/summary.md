# Kimi supplemental load stages: one complete block

Separate cohort; n=1 per condition. No pooling with original8 blocks/38 samples. Values below are raw=mean=median in seconds; no significance inference.

| Condition | Full envelope | Ingestion envelope | Post envelope | Max-rank post | Prep | A parse | Native controller |
|---|---:|---:|---:|---:|---:|---:|---:|
| safetensors_disk_cold | 337.711999025 | 332.823070212 | 4.954095935 | 3.063216562 | 57.715386236 | 495.498346539 | 848.733441000 |
| safetensors_disk_hot | 306.654224588 | 301.595182918 | 4.321631015 | 3.171078385 | 283.300369523 | 464.335577610 | 1050.478528000 |
| store_disk_cold | 31.048521166 | 25.276455937 | 1.495703999 | 0.021897705 | 26.886681245 | 212.185357261 | 1301.303054000 |
| store_pool_hit | 25.309462256 | 21.775261680 | 19.362923020 | 0.011842329 | 23.299220065 | 198.875450450 | 1296.598739000 |

Full and inner envelopes use max(end)-min(begin). Inner envelopes are not additive and are not pure disk IO/CUDA completion. Pool-hit post envelope19.362923020s versus max-rank0.011842329s shows rank skew; see all32rank endpoints.

Controller is the retained native child process interval, not Prep+A. The parent native interval is missing after GC and remains null.

Provenance and exact boundary definitions are in endpoints-and-summary.json. All284exported small files were size/SHA-verified; original full rank files were not reread. Missing optional observations remain null.
