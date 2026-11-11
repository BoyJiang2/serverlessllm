# SLLM V11 / weight-Store evidence backup

Self-contained snapshot of the V7 weight-Store acceptance and the V11 (three-path switching)
prompt documents. Curated from `/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7` on
qjhs003; raw logs, JSONL traces and the multi-hundred-MB run directories are intentionally not
included.

- `prompt/` — the constraining V11 protocol (`SLLM_V11_prompt.md`), the plan (`SLLM_V11.md`)
  and its architecture figure.
- `v7/` — controller, loaders, verifiers, migration planner, frozen protocols, candidate inputs,
  and per-run small receipts (`result.json`, `step-result.json`, `qualification.json`).
- `MANIFEST.sha256` — digest of every file in this snapshot.

Key accepted runs (details in `v7/V7-ACCEPTANCE.md`):

- `v7-store-20261110T140421Z` and `v7-store-20261111T014941Z`: fixed chain
  Kimi → GLM → V4 → Kimi → Kimi, byte-identical cache behaviour in both runs.
- `v7-sleep-v4-20261110T153750Z`: Sleep L1 qualified on qjhs003.
- `v7-sleep-v4-qjhs000-20261111T010645Z`: Sleep L1 replicated on qjhs000.

History is read-only: the 2026-11-09 shared-Store results and the `d3c10bda` three-model report
are not overwritten or reinterpreted by this snapshot.
