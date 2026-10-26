# Canonical path/input boundary handoff

Source commit: bd25defb403cf71bbb07c1bd5d407c2bb24cefe8.
Review file manifest SHA256: 951e80c52f71b72fed3ad1d5c041321828c91edbb138c009b4265f43de9f41c9.
Actual DeepSeek verdict: approved_for_path_boundary. See verdict-v2.json and receipt-v2.json.
This is NOT a full-R1 or deployment verdict; the deployer deliberately rejects it.

Implemented in the single runtime/active source:
- Canonical host workspace vs explicit logical /experiment mounts.
- Shared readonly checkpoint/reference/Store-layout verifier, called on host launch and before bootstrap output creation.
- Pinned restore/weight contracts; eight active ranks/80part sizes; reject rank links/escape.
- Explicit host-only historical mapping and acceptance.
- Full-R1 scope/identity requirement before deployment.

Validation:22CPUtests,30Pythoncompile,3bashsyntax,15actual readonly no-GPU/no-network container cases. Bootstrap sentinel output stays empty. The first checker failure and original8case evidence are preserved. PB02 env allegation was disproved and withdrawn; do not mechanically restore that false finding.

Server evidence: /data/brick1/boyjiang/glm5.1-fp8/migration/runtime-repair/.
Old long root is absent. Real assets/history remain inside the canonical root.
Do not restore the alias or restart old stopped containers.
Active full control source is NOT deployed; probe-only snapshots are not a deployment.

Resume main from the committed source; preserve these changes. Finish remaining verified behavior fixes separately, freeze full runtime package and obtain actual full-R1 before approved deployment/guard/G1. Then R2/formal admission. Existing40IDs/gold/history unchanged; nextIDstore_pool_hit_03 only after all gates. Do not use old v11 gate or this scoped verdict to launch.
