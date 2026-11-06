# Kimi complete shutdown observation: preparation only

## Verified blocker

The once-only efcb9d4/95d14150 native run passed two historical AAB lifecycles and eight-rank ownership, but failed the unchanged30s Store normal-exit condition. The new explicit release reported1408/10240 attempts,0 failures,29.032s; first1024 were21.530s. There is no full completion measurement. Independent runtime review72a4cede confirms complete abort cleanup and >=60s release, not L1 PASS.

## Minimal next proposal

Change only the existing prepare_native_stop_diagnostic.py and its existing Python test. Preserve the original diagnostic and unexecuted grace300 profile/artifacts. Add one preparation-only observation mode based on frozen87a7631e/66d9a69c, same current ELF, nine roles, AAB, capacity, guard and observer.

Use two explicit criteria from one recorded controller stop origin: the legacy30s normal-exit result and a bounded300s diagnostic observation cap. After30s record an immutable legacy FAIL; do not relabel it when a later exit completes. Continue observing until actual normal exec exit0 and authenticated process identity gone, or until the cap/guard failure. Observation includes IPC/snapshot/poll overhead and is not named pure CUDA time. Nonzero exit or lost ownership remains failure. Cap exhaustion is right-censored completion time, not measured300s completion, nor proof the Store PID remained alive for that whole interval.

All total statuses are DIAGNOSTIC_NOT_L1. Guard normal-exit markers only authenticate normal container cleanup. They must not turn the diagnostic into L1 acceptance. New output paths and run identity must be unused. Old raw snapshots and failures remain byte-identical.

## Budget and acceptance

Reuse the existing600s final-stop slot plus120s cleanup/release and180s evidence reserve, gated against actual controller, guard GO/activation/native expiry, observer duration and the full7200s window before shutdown starts. Preserve >=60s release. A late stage must refuse the complete slot, not truncate it.300s is an observation budget cap inherited from an existing audited envelope; it is not a new normal-exit threshold or an estimate derived from217s.

CPU generated-AST regressions must cover normal completion within30s; completion after30s with FAIL retained; diagnostic cap exhaustion; nonzero exec; identity still present; guard failure; evidence write failure; unchanged SIGINT and non-Store branches; complete budget admission. Independent source/render review and exact commit/publication are required before presenting a new once-only GPU execution for user approval.

## Boundaries

No GPU retry has been authorized or executed for this observation proposal. Do not silently run the earlier grace300 candidate. No os._exit, background release, artificial success, GLM run or altered gold. qjhs3 can access the same /home/boyjiang source/ELF/Store paths; a future node change still requires fresh exact deployment/resource/window/guard review and does not reset same-class failure rules.
