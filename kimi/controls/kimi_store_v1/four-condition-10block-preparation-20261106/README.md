# Kimi four-condition x ten-block preparation

40 planned Kimi attempts; formal samples: zero. GLM original40 remains frozen.

Protocol, order, finite asset list, statistics and budget files are candidates, not an execution approval or completed experiment. The current 7200-second per-condition/28800-second block figures are conservative draft ceilings, not predicted runtimes; final executable budgets must also include parent supervision overhead.

File-cache V2 source and tiny-file CPU validation are verified: 12 tests and independent review passed. Exact source branch commit `70b34dd5fe877640abb9bd9d55ad20d36e19e37e` was published and GitHub ref verified. V1 review is retained and explicitly superseded: its interleaved warm/query order could miss eviction caused by a later read. V2 performs every target operation before the final whole-list residency query.

No real model payload was read for the inventory, no model-cache operation ran and no GPU container was started in this preparation. Client-page-cache evidence cannot establish NFS-server or physical-media cache state.
