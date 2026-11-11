# INVALID run — do not use as evidence

`B1-B1-smoke-r1-20261111T092923Z` was started while a leftover probe container
(`lmc-probe`, started at 16:24 for the API contract test) still owned ports
9100 (HTTP) and 5555 (MP transport).

Consequences, verified after the fact:

- the run's own `sllm-v11-B1-B1-smoke-r1-20261111T092923Z-lmcache` container
  exited immediately (port bind failure); docker shows `Exited (0) 12 minutes ago`;
- the Kimi backend still initialised because `LMCacheMPConnector` connected to the
  foreign `lmc-probe` server, whose `/status` reports `memory_total_bytes = 2 GiB`
  (the probe tier), not the run's 16 GiB;
- therefore any KV bytes attributed to this run belong to a server this run did
  not own.

The run directory is kept unchanged for traceability, but it must not be counted in
B1 statistics. The controller now refuses to start B1 when 9100/5555 are busy, and
asserts the started server is Running with the expected tier size before any
activation.

Replacement: `B1-B1-smoke-r1-20261111T0945Z` (or later) after cleanup.
