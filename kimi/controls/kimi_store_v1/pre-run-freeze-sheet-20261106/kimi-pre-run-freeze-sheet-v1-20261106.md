# Kimi freeze sheet — fixed items / unresolved gates

Formal samples: 0; active run ID: none. Not executable until listed gates close.

| 项目 | 已核事实 / 拟冻结规则 | 状态 |
|---|---|---|
| 存储 | GLM为q3本地/dev/nvme1n1 XFS NVMe；Kimi当前/home及/mnt/raid为NFS→qjhs000 RAID，不能称同盘。已找到本地64SF分片595,177,988,208B，19份metadata SHA一致，缺51B配置；全量payload SHA待验。 | 路线待用户选择 |
| 本地化 | 只复制既有Store 617,029,596,501B；复用SF、不重转/重下载。NVMe可用1,543,712,243,712B，复制后926,682,647,160B。另留128GiB输出/暂存+256GiB余量后尚余514,365,786,744B；系统/Docker盘另有821,203,877,888B。 | 未批准、未复制 |
| 计时 | A=同一HTTP进程注册调用前→算术received；B=算术HTTP函数调用前→received。received在JSON解析/响应关闭后、金标/落盘前。读取/解析/校验/父收到与保存分别记。GLM C23冷盘n7业务median179.112278228s，mean180.874626875s；不是天空32题。 | 源码及实际消费者14项CPU/独审通过；无正式样本 |
| 版本 | 共享唯一源码7c7b1b7f95764a1f047b78d53b1b668e6cb10844已核GitHub，tracked无差异；用户untracked指纹保存不动。唯一ELF95d…完整SHA见下方。 | 新路线实际挂载/容器import待核 |
| 关停 | 新Store正常窗口严格60秒：官方exec0+原PID/startticks消失，不强杀替代；模型delete后Store基线60与最终八卡归零、独立quiet≥60分别验收。信号/IPC/等待/退出核验全计，不称纯CUDA注销。 | 旧30FAIL与诊断分类不改；60不是稳定保证 |
| 预算 | 每attempt7200秒；四条件block28920秒=8小时2分；40项+10块监督289200秒=80小时20分。硬运行上限，不是预计耗时。现q3窗口到11/7 09:00，运行前重查。 | 最终路线命令需再核 |
| 次数 | 每condition全新两CID/Store；每block4池初始化、1次pool-hit预填+1次Store冷按需填。全40计划40初始化、10预填+10按需完整8rank填充，实际逐日志记账。 | 不将两次填充误写成全实验次数 |
| 有效性 | 原始metrics、cache/correctness/clock/ownership、condition、block、cleanup分开保存。清理失败则主配对样本不合格但原值保留；四项全合格才完整block。任何失败停止，不替换ID、不自动加时。 | 最终消费者状态细分须绑定 |
| L1 | 功能证据齐备；正式L1验收待澄清并单独确认。原回执与事后接受决定分开保留。 | 不为标签重跑 |

阶段上界(秒)：300+300+1200+120+1800+1500+180+180+720+180=6480；另留720监督余量。720末阶段含600整套关停槽（Store60）+120清理/quiet槽；块监督另120。不可压缩quiet。
ELF: /home/boyjiang/kimi-new/ServerlessLLM/sllm_store/build/kimi-pool-explicit-release-v1/_checkpoint_store.cpython-312-x86_64-linux-gnu.so
ELF SHA256: 95d141504aa4b27901fa6180988cdf8685fc3c5d17f72226a82a50aac3a0c4fd
Manifest SHA256: 59d70f4710ca19d95515e5ca99c5f14ddabd963b2051720908c748e766c2ee2d
完整空间、挂载、ABI/依赖、源码差异与证据路径见同名JSON。
