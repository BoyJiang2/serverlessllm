# V7 active execution scope

V7 supersedes the dual-cache proposal for new work. Old plans, code, raw data and failures remain unchanged. No CPU KV L2 / LMCache / cross-backend KV persistence / W-K-G scans will be implemented. GPU KV is backend-local runtime state. B2 is optional.

## Evidence and remaining gates

- Historical baseline: `../SHARED-STORE-RESULTS-AUDITED.md`, `../COMPLETION-AUDIT.md`; fixed report commit `6a8d44a99f3270b20fcc2408ab481efacadd40d3` read. Fixed five activations and 12 directed switches cover the historical mechanisms; 46 protocol passes include 44 strict gold matches. They are not unified-engine validation.
- Target: vLLM 0.20.1 @ 132765e3560659ff63ebd236203672e991b70e08; reuse existing V4 image (7d6a368950d1...), Torch 2.11.0+cu130. Historical Kimi/GLM versions remain separately labelled.
- Kimi: historical payload is postprocessed; Marlin repack/scale processing source compared with target. Restore must skip repack, reconstruct workspace and check dense quantization state, generated buffers, aliases and all eight ranks. The candidate preserves its 240 empty tensors per rank and canonical-key rules; real target-image CPU tests pass. Runtime compatibility remains unproven.
- GLM: historical payload is raw/pre-postprocess. New `sllm_glm51_raw_0201` candidate retains exact source identity checks, validates against the target constructor tensor schema and alias graph, uses the existing owned IPC restore, then runs target postprocessing once. The combined 25 CPU tests for both loaders, the backend/Sleep receipt gates and full-load trace hooks pass; all six Store/native-Sleep candidate EngineArgs/LoadConfig constructions pass in the real target image without CUDA initialization. This does not qualify payload compatibility or inference.
- V4: existing target image, grouped payload and loader remain unchanged; new sequence requires runtime proof under its own ID.

## Next bounded execution

1. Finish Kimi/GLM candidate checks using target source and immutable metadata, no payload conversion unless a real layout gap requires it.
2. Freeze the new per-model identities/configs and existing golds plus the exact added question `请只输出数字：17 + 25 = ?` (42); no historical gold replacement.
3. A0-A2: fresh 1 TiB / 32 MiB Store and full TP8 backends, Kimi -> GLM -> V4 -> Kimi -> Kimi. Reuse native ledger/guard/observation machinery. Native lookup/read bytes, allocation/free/victims, all-rank receipts, responses and normal shutdown are required. Store-retained observation >=60 seconds between backends; destroy Store and observe full release >=60 seconds at end. Store-cold does not imply OS page-cache cold.
4. B1 independently: native SafeTensors full-model load, Sleep L1 -> Wake -> old golds and new question; retain the same worker identities, CPU backup / GPU / scheduling observations. Run after Store shutdown so Store-plus-backup overlap is zero by construction; report this limitation. Model coverage must be explicit. Sleep results remain separate from Store results.

## Current resource boundary

CPU work only. GPU snapshot is idle, but booking snapshot has qjhs003 assigned to azure until 2026-11-10 10:00 +08, then weihaoni until 16:00 +08. No V7 GPU run started. Refresh bookings and live processes before GPU admission; do not preempt other owners or assume idle means available. No V7 model attempt has been consumed. Creating the future 16:00-20:00 reservation failed because the saved token lacks bookings:create:self. Device approval is pending; no reservation has been created.

Completion requires actual unified three-model runtime plus A0/A1/A2 and B1, not CPU checks/imports. Current goal remains active.

Fresh five-step Store controller and independent Sleep backend runner are implemented candidates. The controller reuses the old ledger, exact-CID guard and release flow; it requires a fresh owned booking receipt, a frozen protocol, zero initial GPU use and an exclusive V7 controller lock. Its five CPU cohort/parser checks pass. End-to-end controller execution, the separate Sleep host observer/qualifier and GPU acceptance remain unfinished.
