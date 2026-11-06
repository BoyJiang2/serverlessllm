# V4 attempt 记录与拒绝规则（P3 CPU）

本批增加 benchmarks/v4_attempts.py，把既有缓存、事件与 GLM 指标检查串到同一条持久记录链。仅实现证据记录器，不执行模型、HTTP、缓存准备或 GPU，也不授予正式 PASS。活动代码/证据在 qjhs002:/home/boyjiang/sllm-v4-pro/{code,evidence}；权重仍原位使用 /mnt/raid/models/DeepSeek-V4-Pro，Store 大数据留 /data。

## 复用与边界

只读复核 GLM C23 attempt_state.py（SHA 02c264fd5c08babf12bda8c2a61f274344d2b808964a3fdf4ce2ec32f9920fb8），其规则是缺少 origin/terminal 的已创建编号仍算消耗并阻止继续。保留四条件、三配对块、12 个 ID 的冻结顺序，SHA 必须来自调用方独立冻结输入。CPU fixture 从冻结 V4 协议生成，但 ID 全部加 cpu_，并保留原协议 SHA；没有创建或消耗正式编号。

记录器检查协议原字节 SHA、12 个唯一 ID、每个 block 四条件齐备及 1200 秒总期限。源代码 commit、source manifest SHA 和执行 run 必填；实际 Linux process identity 带入 origin，此后拒绝换进程继续写。其格式校验不证明这些 source 字段对应真实运行源码或 native gold，执行器仍须验源。

## 持久化和状态顺序

每次构造必须提供全部已声明 execution_roots，包括当前根。只扫描这些根，不能证明调用方没有隐瞒其他执行根。collector 拒绝重复根、跨根重复 ID、未知/链接输入、损坏 JSON、时间倒退、状态乱序或断裂的 SHA 链。空目录也返回 consumed=true/incomplete；origin 不完整或终态缺失不能作为 unstarted 重试。

在任何外部操作前，原子 mkdir 抢占 ID，fsync 根目录，再复用 v4_state.write_once 的临时文件、fsync、hard-link/no-replace 发布。写 origin 失败仍保留已抢占目录。每条 JSON 包含 sequence、前条原字节 SHA、冻结 identity 和时刻；不替换已有条目。它防止意外覆盖/截断，不能防止有权限的写者删除或重写整条链，不是 WORM/签名审计。NFS 服务端的掉电持久性不由本地 CPU 检查证明。

顺序固定为 origin → startup → trigger → business → workload32 → delete → release → result。任一步可写失败终态，失败/未闭合后构造下一 ID 被拒绝；目前没有恢复或新资源准入后继续的旁路。声明全部旧执行根是调用方责任。

使用 with AttemptJournal(...) as journal 包围外部操作。异常写入失败终态后原样抛出；写终态本身失败时保留原异常并附加 sllm_record_error。正常离开但未完成证据也报错。进程被 SIGKILL 时无法执行 finally，collector 仍看到 consumed/incomplete 并阻止继续；本批不自动补造终态或重试。

## 缓存、响应和释放接线

startup 在发布前实际调用两次 cache_scan_checks 及 cache_timing_checks，拒绝混 run/boot/time namespace/clock。校验完成后由记录器生成 preparation_complete_ns 和 startup_command_ns；准备参数不得覆盖 ID、deadline 或原边界，外部 HTTP 必须在该方法成功返回后发出。Store 首次哈希/注册仍按 v4-cache-p3.md 放在缓存准备之前；复用及 pool 确认字段仍需未来执行器通过真实 RPC 验证，不能自填即准入。向量由外部独立冻结清单和解压 bytes 提供；journal 保留 summary/清单/hash，原始压缩向量仍须单独归档。

trigger 验证 register_complete 在 startup 与实际 trigger 之间。business 在入口记录完整 HTTP 响应接收时刻，先检查业务正确性/native token parity，再调用 read_event_window 和 timing_metrics/phase_metrics。只有完整首 token/TP8 阶段链齐备才发布 business 条目。若正确响应已观察到而事件缺失，失败终态仍保留 correct_business_response_observed=true，不误记成响应右删失。workload32 只能随后进行，不覆盖业务响应的计时。

delete 在 fixed32 之后记录命令与确认；release 使用既有 61 样本、至少 60 秒、最大间隔 5 秒和每次新鲜 post-ack GPU 采样时间检查。准备、响应、释放和证据发布共享原 1200 秒期限，不能在 startup 重置。记录器只能在方法边界检查期限，不能中断阻塞的 HTTP/RPC/IO；正式执行器仍需外层 deadline、子进程清理和独立终态证据预算。

成功收集这些结构证据后的状态刻意为 evidence_complete_unqualified，success=false、runtime_qualified=false、physical_cleanup_verified=false。release 时间覆盖不能推出 GPU/IPC/进程/Store 物理释放；也未验证 native reference 来源、完整权重/Store SHA、容量、JIT/干扰隔离、资源预订、服务重启或原生 ABI。不能把它计成正式 PASS。

失败终态保持 GLM 语义的 failure_ns、correct_business_response_observed、right_censored、startup_censor_elapsed_seconds，不把 timeout 当作观测完成时间；cleanup_pending=true 表示外部清理仍必须执行，记账不会释放资源。当前每个阶段存在独立不可替换 JSON，collector 保留完整 journal 和状态/consumed 分母；不是可直接喂给旧 GLM 脚本的同路径 result.json，历史 GLM 原始结果不改动。

## CPU 证据与剩余工作

新增用例覆盖真实 Linux 身份、四进程抢占、origin 发布失败、空目录/中断分母、上下文异常与记录失败、缓存身份和字段覆盖、状态顺序、业务/固定32计时、缺失事件、右删失、释放缺失、源/协议/ID 顺序、SHA 链和链接输入。事件/响应/缓存向量/释放时间均为合成，未加载权重、调用实际 Ray/Store/HTTP 或运行 GPU。定向 21/21 在 /home 下 TMPDIR 执行，统一 235/235 CPU 回归通过、无跳过，源 diff 回执位于 evidence/v4-attempts-p3/。另保存一份完整 CPU journal，clock/worker/缓存/释放及 journal source identity 均为 fixture；receipt 单独记录实际测试源码 HEAD+diff SHA，并标明不含性能或运行证据。原始 JSON 以 -text 保留字节。

后续需把原始阶段记录映射为可与 GLM 并列分析的完整结果，并接入真实控制面/缓存采集、资源及清理证据。正式启动仍必须经过未来完整准入；当前没有提供运行命令或执行正式测量。
