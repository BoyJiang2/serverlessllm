# Actual matched-state cache diagnostic (not model sampling)

Run: `kimi-matched-cache-only-20261109-qjhs000-v1`. Source: `4816aeaae2130923635521c9fa5b5a390fd1bf07`.

Independent result review accepted this diagnostic only. One full buffered read covered 595,204,634,151 bytes across 84 files in 163.125950763 s. Five sweeps scheduled 0/30/60/90/120 s after producer read_end each observed 145,313,657/145,313,657 pages resident. Scans are non-atomic observations, not continuous retention proof or a guarantee during later model loading. Read throughput is host buffered-read throughput; physical disk cold state was not established.

Actual Store pool and chunk were each 67,108,864 bytes. Store normal stop took 2.248460895 s. Both containers exited 0; independent head/worker/observer release observations were 60.169/60.235/60.480 s. No model was registered, loaded or inferred. New formal model n=0; the eight-rank unified weight-load metric remains null.

This does not identify the cause of the old 640-GiB-pool cache failure. Historical failures, old 38/2 campaign, primary eight blocks and accepted L1 remain unchanged. This qjhs000 RAID configuration differs from historical qjhs003 NVMe. Future four-condition sampling needs its own frozen version and admission.

Small raw receipts are unchanged copies with source path/SHA in EXPORT-MANIFEST. Large/private raw telemetry remains on the server, referenced by RAW-REFERENCE-MANIFEST; it is not included in this publication. Derived CSVs and summaries are reproducible CPU postprocessing of these existing receipts.
