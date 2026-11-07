# Native V4 service resource gate

Source: 294eed31f1691202c21035a88d7f279fb688b005; Ray 2.48.0.

| Node / component | CPU reservation | GPU | Node constraint |
|---|---|---:|---|
| Head container / Ray node | quota4 / logical4 |0|control_node=1; cpuset inherited, no exclusive-core claim|
| Controller |1 explicit|0|control_node=.1|
| StoreManager |1 lifetime from custom-resource rule|0|control_node=.1; old actual debug confirms CPU1|
| FcfsScheduler |1 lifetime from custom-resource rule|0|control_node=.1; require ready acknowledgement|
| Router |1 explicit|0|control_node=.1; await start before inference|
| FineTuningJobStore |default actor placement1 / lifetime0|0|unconstrained; not a permanent CPU1 reservation|
| Worker container / Ray node |quota4 / logical4|8|worker_node=1, worker_id_v4=1|
| VllmBackend |1 explicit|8|worker_node=.1, worker_id_v4=.1|
| start_instance task |1 default task, transient|0|worker_node=.1, worker_id_v4=.1|
| inspect_v4_grouped_store task |1 explicit, transient|0|worker_id_v4=.01; before Backend creation|
| collect_all_info task |1 default task, transient|0|worker_id_v4=.01|
| Store daemon / TP mp children |share worker physical quota; no separate Ray CPU reservation|8|worker cgroup|
| Placement groups |none configured|--|mp executor; actual empty PG table still required|

Head lifetime reservations total4. Worker Backend reserves1, with3 remaining for transient Ray work. This is scheduling feasibility, not a performance result. Default actor semantics differ from explicit/custom-resource actors; process counts do not establish CPU reservations. Actor CPU requests remain unchanged.

Stages: first record exact Docker quota/cpuset, actual cpu.max/cpuset.cpus.effective, Ray nodes CPU4 each, Scheduler constructor readiness and Router start acknowledgement, actual actor/PG state. Then validate eight-rank restore, gold inference, delete, second lifecycle, graceful exit and at least60seconds final release. Original failed launch and release collectors remain failed.

References: [fixed Ray2.48 source](https://github.com/ray-project/ray/blob/ray-2.48.0/python/ray/actor.py#L1496-L1524), [official resources documentation](https://docs.ray.io/en/latest/ray-core/scheduling/resources.html).

Runtime ee307d2: actual Docker NanoCpus4e9 each; both cpu.max400000/100000, inherited cpuset0-239; both Ray nodes CPU4. Scheduler constructor ready; Router start acknowledged; all four control actors ALIVE; PG table empty. Backend really scheduled on worker with CPU1/GPU8. Stage1 passes separately. Stage2 fails at first restore RPC: worker sampled pids.current1024 at limit, grpc pthread_create EAGAIN. Original HTTP500 body and failure/collector records retained. Both containers/network removed; fresh61-sample post-removal observation90.04seconds completes within original deadline and verifies GPU0, absent cgroups and original PID identities gone. This does not promote the failed service run.

Prepared repair changes only worker PIDs capacity to4096 (head already4096) and allows exact-owned peer docker kill up to90seconds, still clamped by the original absolute total deadline. Actor CPU requests, CPU4/4, memory16/1008GiB,8GPU and1080/1200second budgets remain unchanged. Further GPU launch is not automatic. A longer next-run budget requires a separate decision; full lifecycle and formal40 remain unqualified/0.

Approved next-run budget: user explicitly authorized one full native two-cycle attempt with total1800/container1680seconds, both from preparation begin. The isolated candidate passed27 CPU contracts; applied bytes match its receipt. Only native-service guardian/release ceilings change; other purposes retain1200seconds. GPU/CPU/memory, raw failures, clocks and both60second observations remain unchanged.

Run native-service-20261107T090901Z at3ce56a0 completed first native register,8ranks x61groups, exact V4 business and32-token responses, and delete. It remains failed: inner unload used an incorrect calibration phase. After full pool initialization and before first business trigger,121 independent pre-model samples over349.58seconds show only daemon322MiB perGPU and total1997MiB perGPU. The model-deleted state returned to this same pre-model value. The prior345MiB calibration is retained; no cause is assigned to unaccounted driver overhead. The failed run, HTTP responses and raw observations remain unchanged. Its later61-sample90.08second full release returns all selected GPUs to zero.

The next attempt freezes1997MiB as the same-phase initialized Store baseline with the existing16MiB tolerance. Before any business request, the host checks fresh initialized Store context and exact daemon-only GPU identity; the head awaits that receipt. Inner unload still requires original model PID identities gone and only the bound daemon; final full-service release still requires original pre-job zero. Budgets remain1800/1680seconds. No new GPU probe or model conversion is introduced.
