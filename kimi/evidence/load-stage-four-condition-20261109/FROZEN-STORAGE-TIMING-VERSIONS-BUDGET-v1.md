# New Kimi three-stage supplement: frozen table (awaiting protocol/run approval)

| Item | Frozen value / rule |
|---|---|
| Node/storage | qjhs000(qjhs0), local /dev/md0 XFS RAID. Not historicalqjhs003 NVMe. Existing readonly SF and existing converted Store; no new copies. |
| Source |87bc7ab2a3106eaea50ace11e2a000e228a0c9c5; explicit4 changed source files across2 commits. |
| Protocol SHA |78beeac12a703ad7c26c7e42475a5e0918cf6741d08fbba1ec24ed933dd3bdde |
| Overlay manifest SHA |79e0584259faee50d6f62ff947f7ef98118da9abc0f6657f30e59e9ac4f434df; exact3 new modules and readonly mounts, original modules immutable. |
| Store ELF SHA |95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd; unchanged. |
| Checkpoint ELF SHA |9d629824c2a13391cd1e1b90edc3c690aa807b8e33d94268e4e21a87107133d5; unchanged. |
| Resources |8H20,worker32CPU/1536GiB/SHM8GiB/PIDs4096;modelutil0.8,eager/TP8/2048context/1seq. No capacity alignment change. |
| Pools |SFcold/SFhot64MiB control pool; Storecold/poolhit640GiB payload pool; chunk64MiB,4IOthreads. |
| Cache |SFhot single complete read+fixed5 finite residency scans; new run re-admits. Prior matchedcache proof not guarantee during later loading. No globaldropcache or rewarm-until-pass. |
| Fig5 primary |(max8rank full.end - min8rank full.begin)/1e9; before selectedloader fresh model/storage construction through weights+necessary native processing return. |
| Other load outputs |Eachrank full/ingest/post endpoints+duration, separate maxduration and8rank stage envelopes; not additive/pureIO/devicefinish. No newCUDA sync.8 original JSON files first/final fullSHA retained. |
| A primary |Actual HTTP producer pre/register -> exactjson_parse_complete_ns, before gold; excludes parent poll/parse/persist. |
| A secondary and GLM |received_ns after response context/helper return separately retained; originalGLM C23 uses post-http received, so exactparse primary is not falsely called an identical historical endpoint. |
| Requests |Arithmetic42,translation猫,sky32; ownKimi tokenizer/template/gold. max128/noignore for business; sky max32/ignoreEOS=true, actual32/length, text differences reported separately. |
| Sampling differences |ActualGLM runner explicittemperature0/seed20261024/logprobs1/order and maxEOS proven. GLM omits top_p/top_k/min_p/n; Kimi preserves its own explicit1/0/0/1. No guessed effectiveGLM default or outputID equivalence claim. |
| Prep |Keep actual environment/pool,cache-admission,register gaps separate. No cache/pool time in weight-load metric. |
| Controller |Native sameInvocation/ExecMainPID/noRestart+boot/timeNS proof; actualmain entry->exit integerus, including performed setup/requests/delete/shutdown/release/evidence. Normal and forced/nonzero separated. Not Prep+A. |
| Proposed shutdown |Only newprofile strict normalStore300s including IPC/diagnostics/finish/strictgone; prior60FAIL/oldrules unchanged. Pending explicit approval. No backgroundfree/forcedkill as normalPASS. |
| Max budget |7200s/condition; 28920s(8h2m)/whole4block.600s orderedstop includes Store300;120srelease slot with actualquiet>=60;180sevidence; actualGO/native guard/observer coverage checked beforestop. |
| 40-equivalent budget |10x28920=289200s=80h20m; arithmetic only, not a new40experiment or ETA. |
| Qualification |cache+gold+all8rank3stages+source/clock+normalservice/containerexit+quiet60. Sample validity,condition,block,cleanup separately recorded. Anyfailure stops, no retry or replacement. |
| Formal samples/run |0; kimi-load-stages-20261109-qjhs000-v1-b01. All4NOT_STARTED, arrays empty/means and medians null. |
| Historical data |Original40:38qualified/2failed; original8completeblocks,acceptedL1,GLM35/5 and oldf7 observations retained. No olddata backfill. |
| Missing gates |Final actual10 review/evidence publication; explicit new300 protocol+oneblock approval; fresh fullwindow/resources/root/pins/unusedclaims/guards. No GPU started by preparation. |

Exactactual10paths/SHA,all3overlayfullSHAs,allresourceproperties,ELFmanifest identities and numericalphasecaps are in FROZEN-STORAGE-TIMING-VERSIONS-BUDGET-v1.json and actual-frozen-render-v1.json. The root command is a frozen argv with a fresh-booking-expiry placeholder only; never run it from this document before the missing gates pass.
