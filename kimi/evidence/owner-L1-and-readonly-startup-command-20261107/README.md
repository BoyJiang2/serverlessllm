# L1 所有者决定与历史启动日志只读命令

所有者明确答复：“接受既有证据完成L1功能验收，保留原分类”。
当前统一表述：**L1功能验收已由所有者基于既有证据接受；原始运行仍为NOT_L1，旧30秒FAIL保留；没有新执行的正式L1运行。** 该独立决定闭环原goal最终交付11.1，不回改运行、金标或失败。先前审计pending状态按其记录时间保留，以本决定为当前状态。

其余证据缺口仍在：私有host日志的加载跨度/实际导入标记尚未读。capture_seconds不是加载时长；38个旧worker已移除不证明主机日志不存在。整体goal尚未完成。

## 已验证但尚未执行的CPU只读命令

- 源码已发布并核验fdc3643c9faed467314beea12d90d1b7b2c11bf1，只新增collector与7项CPU测试，不改生产loader/guard，不是GPU部署。
- 只读冻结v3中38个原始合格run顶层stdout/stderr；每文件8MiB/日志总128MiB，核SHA与前后文件身份。不rglob、不读权重。
- 只输出白名单包版本/模块路径、数值model-loading标记；未解析rank/PID为null，列表不是单一C。oldf7计时包含getloader/loadmodel及可选LoRA/drafter/aux；timer在capture之前结束、日志在之后打印。不是GLM纯ingestion、backendinit、TTFT或纯I/O。
- private摘要O_EXCL0600；成功后对相同sanitized bytes创建独占public0644文件并核SHA。不输出任意原日志、env或凭据，不覆盖文件。
- wrapper要求root，timeout60秒/TERM/kill-after5是进程超时保护，不保证D状态立即终止。失败/超时/partial不可当完成，不自动重试。
- 本轮7项合成小文件CPU测试、bash/Python语法与独审通过，真实root收集未执行；没有GPU/容器/Store启动。
- 旧root工具续接被自动审批拒绝（需审批而当前策略禁止申请），sudo非交互无认证。不需要再发送密码；请在qjhs003用已有sudo认证手动执行一次。

从本机明确连接qjhs003执行：

```bash
ssh -t qjhs003 'sudo bash /home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/run-existing-startup-readonly-20261107-once.sh'
```

脚本SHA256：1890896aa0594300f430750779159177589f187f93ae814b28479b396dcdbd50

成功摘要：`/home/boyjiang/kimi-new/evidence/s4b-readonly-20261104/kimi-existing-startup-log-readonly-20261107-once-public.json`。
执行后只需告知完成，agent继续读取核验。失败保留partial和错误，不重复执行、不新增GPU探针；原40项及历史/补跑终态保持。
