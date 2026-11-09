# Controller原生进程全程端点前瞻补充

旧SFhot已GC，systemd精确入口/退出时间保持null；已存common_environment_begin→release_quiet60_complete只是事件包络，不能替代unit全程。失败Storecold/parent和standalone诊断若仍保留原生属性，可以外部只读采集，失败时长不冒称正常全生命周期。

未来最小修改只在现BLOCK_BODY：保留原child RemainAfterExit=yes，不注入--wait；原systemd-run启动后，在同一父循环和原绝对预算内有界poll该唯一unit。不能以MainPID0单独认终态，必须有真实正数ExecMainStart/ExitTimestampMonotonic与ExecMainPID、结束状态和同一invocation。退出后先把原us与us×1000的ns、raw code/status/result、run/source/spec/command绑定原子保存，再执行原child PASS/失败停止判据。

指标为main process入口→真实退出，正常完成时包括该进程实际执行的prep/create/请求/delete/cleanup/release/finally；强杀时仅为真实失败进程跨度，不证明release已完成。外部后续abort释放另列。单位转换不增加精度；不从Prep+A或失败elapsed拼全程。

完整schema、空值规则、预算/identity门槛和CPU actual-consumer用例见JSON。此处仅方案，无执行或源码变更。
