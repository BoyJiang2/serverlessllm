# Docker CPU调试入口

本入口统一使用一份JSON spec，提供`up/status/down`，只用于轻量CPU调试；不代替GPU服务、共享锁或正式准入。

```bash
python3 docker_debug.py up probe.json --seconds 90
python3 docker_debug.py status probe.json
python3 docker_debug.py down probe.json
```

需在qjhs003授权UID2147的Linux环境运行，`docker_plan.py`与`docker_debug.py`在同一目录。状态文件位于规范根`debug/<run_id>/<role>-identity.json`。`docs/docker-debug-probe.example.json`展示JSON格式，每次实际调用需使用新的debug run_id；已存在身份文件不复用。

## 本版边界

- 完整原镜像ID固定，禁止隐式拉取、GPU请求及网络，使用runc。
- 最大2CPU、2GiB、128任务，移除capability并禁止新增权限。
- 工作时限1–300秒，另有10秒KILL宽限。镜像内timeout是本CPU工具的应用时限，不是模型实验所需的独立宿主守护。
- CPU调试仅允许只读`code/`子树挂载到`/debug/code`子树，不提供checkpoint、Store、页缓存读入或锁页大池入口。
- 用户环境仅允许`PYTHONDONTWRITEBYTECODE`，避免替换寿命程序或影响动态加载器。
- 创建前登记随机holder；后续操作同时核验完整CID、holder、run_id、role/name、image、UID、runc及网络策略。停止操作只针对匹配的确切容器ID。
- create超时且未返回CID时，仅按确切name与holder重新识别；无法确认即标为清理未核验，不删除陌生容器。不把inspect失败等同于已释放。
- `down`成功后复查确切CID不在Docker容器清单内，再记录released。重复up使用新run_id；已删除容器再次status返回核验错误，不伪造仍在线。

目录创建逐级检查规范路径、所有者和符号链接。进程或SSH断开后应检查状态收据及实际容器，不直接假设命令未执行。原40正式ID、金标、运行配置及历史结果不改动。

## 验证

本地CPU单元测试覆盖参数边界、挂载/加载器保护、身份不符不删除、create超时的可信身份恢复及未知结果拒绝清理。真实无GPU探针另记录up、timeout退出124、版本及设备日志、down和实际无残留。即使探针通过，也只证明该CPU入口，不宣称完整P1或模型服务通过。
