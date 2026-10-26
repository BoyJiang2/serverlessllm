请只读审阅当前全新candidate13e，返回R1e approved/changes_required/blocked，必须绑定本次调用附上的实际code commit与manifest SHA。只用read/search，不执行shell/SSH/资源命令，不改文件，不读凭据，不派代理。这是独立AI源码/有界G1方案审阅，不是G1真实结果或R2批准。

先读controls/candidate13e/PLAN.md、USER-OBJECTIVE.md、R1-RESPONSE.md、control-input-manifest.json，以及reviews/verdicts/deepseek-r1-control13c-final.md。13c与归档13d均保留，13e有全量文件及相对13c逐文件diff。请实际读取受影响代码而非仅摘要，尤其R1c B01–B12逐项核验；已撤回猜测不机械恢复。58CPU测试、编译及bash语法不是运行证据。

重点审阅package_inventory/deploy精确清单与bytecode隔离；bootstrap/budget/watchdog统一330/3420总预算和终止reserve/late-exit及边界测试；当前独立run/booking10–18/17:50守护probe和实际只清理本run；root-private证据、timer最终冻结、observer、全部祖先memory/high/swap；148文件cache闭包/LFS双证据/metadatahash/mincore全向量/initial read bytes/每轮repair/read-idle/final placement；native61rows/60s与PSS及最终释放。

尤其阅读gpu_identity13.py、monitor_control13.py、validation_control13.py、accept_control13.py、store_numa_exec_v13.py、bootstrap_control13.py和test_gpu_identity13.py：实例删除后Store辅助进程可能保留GPUcontext，必须以当前host PID+NSpid+startticks+pidnamespace+exactcontainer/cgroup+UID+boot绑定，仅允许当前已登记helper；不准把任意同run engine当helper，或用历史PID白名单。accept需独立关联host monitor原始行；整个控制退出仍须GPU PID空。读取evidence/persistent-helper-counterexample.json的证据边界。请识别真实代码缺口，不基于猜测机械阻断。

当前节点仍为原qjhs0038×143771MiB，同boot/UUID和assets，gpu_memory_utilization0.9不变；qjh003迁移及0.975提议已撤销。当前预约bk-20261026T0248-fd6bea51,10–18,guard17:50仍未部署；读取三份current-*证据，实际运行前仍需刷新。当前1142file source/binary/config哈希一致，9terminal5success4failure31unstarted，下一正式store_pool_hit_03只可在真实G1→R2→正式gate后。无Node8等待/共享锁。历史137仅有dockerd SIGTERM90s后强制退出机制证据，上游stop请求人/原因未知，不改判，不归因Ray。

返回逐项finding的文件/函数/实际证据/影响/最小修正或验证；运行前置条件可单列，但不能把尚未运行的G1/R2作为静态必败。若需要最小信号探针请论证信息增益，不修改全局Docker。请完成独立结论，并给JSON(stage,verdict,code_commit,manifest_sha256,findings,runtime_conditions,limitations)。
