# Kimi IPC关闭错误传播候选

此目录是独立源码候选，未部署或编译完整Store，不改旧GLM归档、镜像、进程或结果。完整基线与候选SHA、修改范围见ledger.json。

原Model::ToGpu在成功复制后设LOADED，IPC close失败仅写日志；ConfirmModel的等待可能仍返回成功。逐记录分配希望在重排时释放原记录，必须先有可靠的传输/IPC关闭结果，不能靠确认布尔或对象类型替代。

候选仅修改关闭尾部：cudaSetDevice失败时拒绝在错误设备继续关闭该组，记失败并继续处理其他设备；任一cudaIpcCloseMemHandle失败也记失败，仍尝试后续可关闭句柄；最终状态设INTERRUPTED，通知移动到这些尝试与最终状态之后。成功保留LOADED，原复制失败仍保留INTERRUPTED。不cudaFree、不强制清理、不声称错误情况下已关闭全部句柄。

CPU验证直接提取旧/新ToGpu的实际状态及IPC关闭尾部，放入最小C++宿主夹具编译执行；CUDA函数、复制结果bool、Replica/CV为stub，没有链接CUDA。旧片段的设备切换/关闭故障控制未被拒绝，新片段对这两种故障均返回失败；成功、复制失败保留、继续有效句柄及最后通知均通过。源文件改动仅在该尾部，其他区域（LF归一化后）相同。177项CPU测试通过，g++14.2.0及观测断言见cpu-probe.json。

限制：这不是完整C++库编译、真实并发/锁等待、真实IPC或DMA验证。gpu replica在失败后可能被现有LoadModelFromMem移除，ConfirmModel仍可能等到RPC deadline；调用方必须保留失败与原始日志、由新守护受控退出本实验context/Store，不重试同worker。下一步多handle producer必须绑定实际编译部署的修订及库hash，并通过独立审阅/故障probe；原Python接口SHA不证明已使用该补丁。当前native工厂仍未切到逐记录布局。
