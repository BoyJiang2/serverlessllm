# 源worker代码清单与配置绑定

`source_bundle.py`新增独立build/verify接口。本轮仅本机小Git仓库、元数据、文件校验和CPU子进程测试，无SSH、模型、GPU、Store池或完整转换。原GLM运行包/结果不变。

## 来源及完整文件集合

生成器复用tools/deploy_tree.py既有Git对象读取器，不调用其中的GLM部署/SSH函数，不修改原工具。读取器源码SHA固定为944d9c8abe9c2ddd2002ca2267915e263d43961f95da7261243a0ed992e8d819；读取有界且只执行已核SHA的字节，不依赖调用目录/PYTHONPATH。实际commit先解析为40hex，再用Git ls-tree的mode/size预检，之后才cat-file；只接受普通文件，单文件≤2MiB、总源码≤16MiB、文件≤512。

整棵controls/kimi_store_v1的已提交内容全部纳入，包括CPU测试、文档、契约及固定fixture；不手工挑出一个更小列表冒充完整候选。当前约1MiB，不包括权重、Store、镜像或大日志。工作区修改及未跟踪文件不参与。该大小边界是源码字节界限，**不是Python进程RSS峰值**；Git子进程、解析及多个bytes副本仍需计入准备成本。

生成仅写到全新的绝对目录，manifest放目录之外，exclusive mkdir/x文件写入；不覆盖、不移动旧run工作树。中途失败保留partial，不自动删除或重试。构建命令必须从含已审tools/deploy_tree.py的完整本机checkout运行，校验命令只需候选内的stdlib代码，不需要Git/Tools/模型。

## 校验与run绑定

manifest schema=kimi-source-code-bundle-v1，包含code_commit、source_prefix、完整相对路径/字节数/SHA256，runtime_admitted保持false。调用方须提供独立绑定的manifest SHA及预期code commit。校验器有界读取manifest，先拒绝重复/越界/不安全路径，再比对实际全部文件集合与每个文件字节；拒绝缺项、额外文件、symlink及非普通项。每文件只读声明长度+1，遍历有项数上限。pyc/__pycache__不被忽略；真正容器须只读代码挂载并设置PYTHONDONTWRITEBYTECODE=1。

verify_source_run另外复用现有source spec校验，要求代码目录恰为spec.run_root/code，manifest恰为spec.run_root/source-code-manifest.json，code_commit与spec一致。即使两run代码及manifest字节完全相同，也不能混用另一个run的物理目录/清单路径。返回run_id/holder/spec SHA和校验结果，但identity_declared_only=true；未证明当前UID、boot、预约、守护或实际已导入模块/映射内存。offline verify的git_history_checked=false，不冒充容器里再次查询Git历史；源字节与Git历史的联系来自本机生成器及外部受审manifest，不接受手写清单替代该生成证据。

示例参数形态（只说明CPU接口；不是GPU启动命令）：

```text
python controls/kimi_store_v1/source_bundle.py build --repo <本机仓库绝对路径> --commit <冻结SHA> --directory <新绝对code目录> --manifest <目录外新manifest文件>
python controls/kimi_store_v1/source_bundle.py verify --directory <code目录> --manifest <manifest文件> --sha256 <外部绑定SHA> --commit <冻结SHA>
```

## 验证覆盖和下一项

新增13项CPU测试，总计233项通过。真实小Git仓库证明commit对象字节不受dirty/untracked影响，覆盖缺项/extra/内容变更、外部SHA及commit、重复/绝对/Windows驱动/遍历路径、限额、禁止覆盖、模拟symlink拒绝和超大Git blob在cat-file前拒绝。独立CPU CLI build/verify在无项目PYTHONPATH设置的进程中通过；run绑定测试覆盖同代码不同目录的交叉使用。小仓库只有四个dummy源码/契约文件，仅验证清单协议，**不是完整Kimi候选或真实转换**；symlink测试为标记注入，未做远端Linux文件系统probe。

当前尚未把该校验接到source_plugin/worker启动前；插件的code_commit仍只是声明，不能据本组件PASS声称已执行完整代码绑定。下一项需要实际源码候选包存证、worker启动检查/模块来源、容器与window/guard/probe/monitor、source全预算和退出取证，之后才申请运行准入。预约计划仍见RESERVATION-PLAN.md，未完成准备不占模型/大池等审批。native预算停止与原40ID保持。

初审发现rglob可能抑制子目录扫描OSError。首次注入只patch os.scandir，未覆盖Python3.13缓存的pathlib扫描器，按真实结果保留为探针未命中；修正hook后旧实现确实误返回成功。现改os.walk(onerror=raise, followlinks=False)，子目录扫描失败必须退出；完整初审源码、hash、两次探针结果及最终测试保留source-bundle-tests.json。新回归是小本机故障注入，不冒充真实Linux权限probe。
