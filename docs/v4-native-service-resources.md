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
