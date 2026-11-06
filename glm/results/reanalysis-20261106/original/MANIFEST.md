# MANIFEST — C23 reanalysis

> **Sanity gates: PASS** — every published C23 condition mean (prep / route / first_tok / biz) reproduced within 0.05 s on the unified command(/register) origin.


Generated: 2026-11-06T05:15:24.690468+00:00

## Environment

- Host user: `boyjiang`
- Python: 3.10.12
- Python executable: /usr/bin/python3
- Platform: Linux-5.15.0-91-generic-x86_64-with-glibc2.35
- numpy/pandas: not available on host; pure standard library used

## Reproducibility parameters

- Seed: 20261024
- Bootstrap resamples: 10000
- Bootstrap unit: block (paired differences within block)
- CI method: percentile, 2.5% / 97.5%
- Permutation test: exact two-sided sign-flip, all 2^n assignments
- Multiple comparisons: Holm-Bonferroni, family of 4, per window

## Inputs

- Ledger: `/data/brick1/boyjiang/glm5.1-fp8/results/formal-ledger.json`
- Ledger sha256: `76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b`
- Ledger generated_at_utc: 2026-10-27T05:29:57.089087+00:00
- Population: `protocol_revision == v13-canonical23-qualified-disk-jit-seed` (26 rows)
- Code: `/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/recompute.py`
- Code sha256: `cb35cf40e8433b36404b3aae96dc677020c7a9b071d46f4e0d4f23dd0153bdfc`

## result.json sha256 used (26)

| attempt_id | block | condition | sha256 |
|---|---|---|---|
| store_disk_cold_04 | 4 | store_disk_cold | `ae2256ebe31b0bbfe08784406b62936a6e07079b73e22daae7007fcd12e37f0c` |
| store_pool_hit_04 | 4 | store_pool_hit | `61bdca8ab57bb49fce0cf67bc869e7f9a5f5af9e6f69fd29fbcdcdc81f92c4ff` |
| safetensors_disk_cold_05 | 5 | safetensors_disk_cold | `46cf89c1db7c8671121660a6c823df770329ea3d00f04beb18f981d98aece812` |
| safetensors_disk_hot_05 | 5 | safetensors_disk_hot | `3dd7da26cabda4b2f73e9b290635b53ff398fdf8270e148512d2f22e1994ecad` |
| store_disk_cold_05 | 5 | store_disk_cold | `0e54771e24ca738d5ab308e5a15df0ffcf670dbba6ddb5d985de06a83105968b` |
| store_pool_hit_05 | 5 | store_pool_hit | `2d8e2686b5c496fa2816306b0c2058e4270eb575a0718a868369c65870051905` |
| safetensors_disk_cold_06 | 6 | safetensors_disk_cold | `9cfcdd2381f4c7768a22794cc282c80383caba3204f6c5aba0cbe512ac8215a2` |
| safetensors_disk_hot_06 | 6 | safetensors_disk_hot | `a062ed63b35d6b6bac6c32cfb9cf5e92c112eec2116097ae9d0f46e81b4bce2e` |
| store_disk_cold_06 | 6 | store_disk_cold | `33f6be264cf2ca9f98a3f2d000bfbbb922834daed4b2247904666d9b41efcc68` |
| store_pool_hit_06 | 6 | store_pool_hit | `469c371a898185e468f80e2df25743cff3b8ad8b1c30b82307c8e3727b12ee79` |
| safetensors_disk_cold_07 | 7 | safetensors_disk_cold | `0b23ef18a87307c54834acf0907e7a370998b386eb3d24d0abc78817f0186b49` |
| safetensors_disk_hot_07 | 7 | safetensors_disk_hot | `4ba5e24155452a9517ef25d7a2856edc1bcc8fc476dd3f909c89ff930fd63591` |
| store_disk_cold_07 | 7 | store_disk_cold | `86818cbead1ecbbbed19009471ca89ebb955d2093391ba220049b5c680713a1c` |
| store_pool_hit_07 | 7 | store_pool_hit | `24bbad0133ef851cd75dd0c281eda4768a03995b040f62f9803703f983339838` |
| safetensors_disk_cold_08 | 8 | safetensors_disk_cold | `4a209169f11cd6c951726a3ee22d88dc35cd9bf4af63f90be792fffbe7e99c4a` |
| safetensors_disk_hot_08 | 8 | safetensors_disk_hot | `149abfe3a6efce7443a41481e8a177349f6dc48a99b615d5f6be6fac077f1c94` |
| store_disk_cold_08 | 8 | store_disk_cold | `d837831470023f9f898310841a96fb41765f8eeb400e13377d163f970430afef` |
| store_pool_hit_08 | 8 | store_pool_hit | `9371a1cbc165a390e9e64ce41dae91b0d5eb383e9e1563eb140063f8f6a6d24a` |
| safetensors_disk_cold_09 | 9 | safetensors_disk_cold | `e2ac9d9d606d24926d365e9c0f3594925293463d7718f7d026f7c2ff7e16b4ec` |
| safetensors_disk_hot_09 | 9 | safetensors_disk_hot | `2d6cc242e2502efd701d79bc6d484a09d271f11d90897cecfbe01421ff5ccb72` |
| store_disk_cold_09 | 9 | store_disk_cold | `8ca3ac2129c9b689f54a8b70af7673818ade1579ee41af16f5c3dca4ed872794` |
| store_pool_hit_09 | 9 | store_pool_hit | `ec0d4f78dae3b0e8afa0346ead448a00d560c13bba96665adf62c0b57e37d1ab` |
| safetensors_disk_cold_10 | 10 | safetensors_disk_cold | `f19596e7533121a7351e303c59474cd263743d03d394daf4ea7d5ae94b609f7f` |
| safetensors_disk_hot_10 | 10 | safetensors_disk_hot | `945117c43cb380459978880f2c3b955802cf14f2ed867058444675c193cabe7a` |
| store_disk_cold_10 | 10 | store_disk_cold | `88535060aa901af2d2412b648db282fdf1cf4bf9f03cd9c154ee05d5d9188a90` |
| store_pool_hit_10 | 10 | store_pool_hit | `84bc3947ee848f6424fb0630cf2519f76cdd2d4fd860440197763307ed601501` |

All of the above hashes were recomputed from disk and matched the `result_sha256` recorded in the ledger.

## Metrics keys observed

- common across all 26 attempts: 16
- union: 16
- identical key sets: True

- `backend_construct_s`
- `command_to_correct_business_response_s`
- `command_to_first_token_s`
- `command_to_route_publication_s`
- `phase_communication_groups_span_s`
- `phase_compile_graph_and_warmup_span_s`
- `phase_device_setup_including_communication_span_s`
- `phase_kv_cache_allocation_span_s`
- `phase_quantization_postprocess_span_s`
- `phase_weight_ingestion_span_s`
- `phase_weights_pipeline_including_allocation_span_s`
- `preparation_s`
- `trigger_to_correct_business_response_s`
- `trigger_to_first_token_s`
- `weight_ingestion_span_s`
- `weights_pipeline_span_s`

## Sanity gate

- Status: **PASS**
- Tolerance: ±0.05 s on all 16 published condition means
- Origin: unified command (/register) for prep, route, first token and business response
- Detail: `sanity-gate.md`

## Documented limitations

### `preparation_s` is a composite measurement, not fill-only (verified in source)

Source: `code/c23-reference-f176e05/formal_trials.py` (commit f176e05607d04999ab28e8ea8cd49b41270cb528, matching `code_commit` on every C23 result).

- L147: `'preparation_begin_ns':time.monotonic_ns()` opens the window.
- L152: `try:` — the window is already open before the try block.
- L159–L163: `if pool_loaded:` then a loop over all 8 identifiers calling `client.unload_from_cpu(identifier)`, then `pool_loaded = False`. **This teardown of the previous attempt's pool runs inside the preparation window.**
- L182: `record['preparation_complete_ns'] = time.monotonic_ns()` closes the window.
- L225: `'preparation_s':(preparation_complete_ns - preparation_begin_ns)/1e9` — so the recorded preparation time includes that unload.

Conditional scope: the unload only executes when `pool_loaded` is true at attempt start, which is set at L170 (after a `store_pool_hit` `load_into_cpu`) and at L236 (after any `store_*` attempt). Attempts that start with `pool_loaded = False` carry no teardown. Either way, `preparation_s` is a measured composite, not a fill-only measurement, so it is reported as measured and is never decomposed.

### Pairing rule sensitivity (contrast 3)

Contrast 3 admits n=6 (complete-block) and n=7 (maximal pair, adding block 4). Both are reported side by side in `paired-contrasts.md`; neither is preferred. The n=7 variant is influenced by `store_disk_cold_04`, itself a 3-MAD outlier.

### Family-wise power floor

- n=6: min raw p = 0.03125, min Holm p (4 contrasts) = 0.12500 (not significant at 0.05)
- n=7: min raw p = 0.01562, min Holm p (4 contrasts) = 0.06250 (not significant at 0.05)
- n=8: min raw p = 0.00781, min Holm p (4 contrasts) = 0.03125
- n=9: min raw p = 0.00391, min Holm p (4 contrasts) = 0.01562
- n=10: min raw p = 0.00195, min Holm p (4 contrasts) = 0.00781

With a family of four paired contrasts and an exact sign-flip test, at least **8 complete blocks** are required before family-wise significance at alpha=0.05 is attainable at all.

## Re-run

```
/usr/bin/python3 /data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/recompute.py --check   # diagnostics + gate check, writes nothing
/usr/bin/python3 /data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/recompute.py           # full run, rewrites all 10 files
/usr/bin/python3 /data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106/recompute.py --followups-only   # rewrites only the three follow-up files
```

The delivered revisions of `paired-contrasts.md`, `amortization.md` and this file were produced with `--followups-only`, so no other output was touched.
