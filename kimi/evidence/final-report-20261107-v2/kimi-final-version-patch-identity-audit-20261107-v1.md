# Kimi final version, patch and loading identity audit

JSON: `/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-final-version-patch-identity-audit-20261107-v1.json` SHA256 `31b48ba12103a656b29d6695b834cfac96b92a0bedefc2f9d0232eb40d2336c3`

Analysis HEAD `a43734f11e84a06577b3de3833d0e9895e8ab323` is not a runtime deployment. Runtime strata are 86fd/v8 (complete blocks1–3) and835/v9 (complete blocks5–8,10). B4/B9 remain incomplete.

Official baseline `9f50241baa5386e06a9321c51f19a9ef5f964c2b` is established by historical S1–S3 evidence and existing Git object; its vLLM0.11.2 requirement differs from the actual adapted0.19.1 runtime.

Runtime image `sha256:ec5b4131a127045cefe4c84d349ea5d649e6f028d28df7711bdb1f40faa38c27`. Active ELF identities:

- original_C: `41d2409ad03c1587c5fef8312cfd2e6672689f64c1b6c64c38a194755580943e` — Preserved source-tree anchor; masked by nested replacement bind in both roles.
- original_checkpoint_store: `f757a8eb57d6247b91da1ef3955e996c1c7f2fc998bb6acde15a4e9a69b03ec3` — Preserved source-tree anchor; masked by nested replacement bind in both roles.
- active_C: `9d629824c2a13391cd1e1b90edc3c690aa807b8e33d94268e4e21a87107133d5` — Actual requested RO nested mount both roles; historical CPU build/import and ownership tests.
- active_checkpoint_store: `95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd` — Actual requested RO nested mount both roles; historical CPU genuine CMake build/import, not constructor.
- glog_dependency: `d76e3fafb8b0b0176a1b5a0ccc9d5db11c5118046b8f31d724731640740da9e6` — Existing dependency; manifest resolves libglog.so.1 to same content.

Active GPU runner is `f7a958706ee01c2c0c1d8c424fa9bfefa4ac80bb20a2eab9a8f70d0a99f351eb`; all11overlay paths/hashes match both requested RO mounts. The f7 build manifest4707 binds originalpatch9d16. Current trackedpatch6fc is later provenance, not evidence that diagnostic866 ran.

20 production paths differ from the official baseline across native lifecycle/tracing, SDK writer/restore/IPC/pool shutdown, CLI, and vLLM adaptation. The exact per-path baseline/deployed blob hashes and all linked dependency hashes are in JSON.

CPU ABI records establish CPython3.12, Torch2.10/cu129, CXX11 ABI, real extension imports and linked dependencies. They do not establish a fresh live loaded-library mapping for every historical process. No new imports, GPU, Store, containers or large assets were read.

Missing or weaker evidence: fresh active FlashInfer import version; historical live per-process loaded-ELF hash; independent full tensor-value equality. Source/module availability, requested mount identity, CPU ABI and actual model execution remain distinct proof levels.
