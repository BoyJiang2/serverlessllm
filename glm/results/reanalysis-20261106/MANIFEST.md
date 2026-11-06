# GLM V2复算交付清单

本地分析目录：D:/Documents/codex/2026-10-24/new-chat/serverlessllm/glm/results/reanalysis-20261106。原始产物节点qjhs003，原始路径/data/brick1/boyjiang/glm5.1-fp8/results/reanalysis-20261106。home副本已按用户要求删除，后续不再SCP到home。

本轮只读取得原始ledger/结果及事件；权威result_path来自ledger。ledger SHA=76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b。原recompute.py SHA=cb35cf40e8433b36404b3aae96dc677020c7a9b071d46f4e0d4f23dd0153bdfc。逐条输入与路径见all40-matrix.csv，事件文件SHA见event-intervals.csv。input-copy-manifest.json保留已删除home副本的历史传输凭证，不能再当作现存路径。

数值gate与事件gate均通过；声明补丁集的17个目标文件与C23清单、canonical字节SHA一致，另验证Store服务覆盖；deployment-gate.json限定为PASS_FOR_DECLARED_PATCHSET，不扩展为全仓库或逐rank映射机器码证明。Kimi栏缺测留空。未生成N>1摊销图。

复算：python3 independent_audit.py；python3 event_audit.py；python3 deployment_audit.py；使用现有matplotlib运行python plot_analysis.py。标准库分析使用WSL Python3.10；图表使用既有Windows Python3.13/matplotlib3.10.8，无新建环境或安装。

成本来源：`../c23-costs.json`，SHA256 `336d3d51a9991fbd8f906dec8fd0f407cf9ce585f29efc1cfea739c521976fce`；该文件是已有成本汇总，本轮不改写，历史采样不提升为连续峰值证明。

本清单避免自引用，不计算自身SHA；Git提交及外部交接可计算本文件SHA。下表为本次输入、代码和输出字节身份。

| 文件 | 字节 | SHA256 |
|---|---:|---|
| HANDOFF.md | 2686 | `948d3bac238f2084a75d99a536325c2237739b11a2e10a393a6be2336b0a86e8` |
| REPORT.md | 14132 | `f761a164078e74f20d02ff0f076c41c64892acbbcf0b68ec80a2af107112abf5` |
| all40-matrix.csv | 12020 | `ae80d12f06dc7b813e51a145904ac6210066c00f1bd3b0016ff3b3f22bbac82c` |
| build-validation.json | 965 | `c7819ff7769e1648945abca12e68615d531d7fecfcd8ce1d4055513cb4b883af` |
| c23-block-matrix.csv | 766 | `030d322f01d4588632d2ce71795776d8d471bd323411cfbca1c950c66ba49ed5` |
| c23-store-server-overlay.patch | 344 | `c719b0c1c2a6d560b58606e6a4ceabb09177e6ce56c0067be910032b22eef3e7` |
| deployment-binding.json | 13464 | `a99e4dd85a58101fda3d2dc906f45321177b9f7d31a3f57910379b18698d313d` |
| deployment-gate.json | 2419 | `7b06655df102d2d998149aa3caad2c0dd0bd27cb7dfc9ed242a8c73195f27c71` |
| deployment_audit.py | 3566 | `800adb19e88c2dca38fabb8882f51fe1196d11e43c81b01afc496e27ced45d7d` |
| descriptive26.json | 1763 | `5e068208e71d4f625cb5681e23980cf7574a49812416baa32d0cd0ea7c86d29a` |
| event-gate.json | 2147 | `01e0bf4b6dcea514cd71c46c0bf78a3114804256bfe182feb2c3961695659f34` |
| event-intervals.csv | 12987 | `3f11fc8383084adcfa3c37a1022d1b70c65c6e4da2eda8c1f9a5420b14ace158` |
| event_audit.py | 6595 | `03ad1f172afa048a80c3604511cae422c745f78c0e19cd85b1fef31bea98065f` |
| executed-source-binding.json | 1021 | `5b752b9b7dcbe65990428776443cee3c5d07e6e7e7fcc5e26a25eff1abfdb963` |
| git-publication-pending.json | 550 | `998943a195314186f8c651351cafb6ec4d70d39eb213f790c1f75debcb8198b6` |
| home-cleanup-receipt.json | 469 | `e0fc0186f7360cdfa8ec4695e61b2f505b1215a6716c0fa5781f28f1ca208154` |
| independent-audit.json | 23038 | `a0d3c29481327e1ec0887426b9a4594b956ac673831b02a50ab8f242e34dec86` |
| independent_audit.py | 6552 | `e1e186398d54642d301209b85007fb835f8a89d9ba533c9856687b25e82d9c33` |
| input-copy-manifest.json | 17059 | `5734a61e4455af2f419dfc6216e296549b1ed76243270708e41e251f9cbd5dc2` |
| inputs/C23-control-input-manifest.json | 15348 | `3eac8c63cdd670a49d872922c11b821d07a0e252f2fbe80f790e169749091fb9` |
| inputs/C23-executed-source-archive.json | 13713 | `62e283a0fb893ac030b2b0d8f97fff921ad086c032fe2c34223cf8e904375e5f` |
| inputs/C23-formal13-cpu-import-preflight.json | 3974 | `1c2e0a73157bf10c95f6173e7fd2197c78c8c1e67122db4a3aee2c48f4955cfd` |
| inputs/C23-run-manifest.json | 52959 | `bda7616e8310ab08178fc52bf2c2e51cf4d51544915de1b7f199a287bf8cf87d` |
| inputs/C23-store-server.py | 8959 | `b22a5b3c907accfea0747da3463939d755ccbecb0e5994467cfb6cd5b7f36d1f` |
| inputs/canonical-INDEX.json | 6709 | `037b347e22c589b06883a20ca7ccb8805101324f7bdd7001e28497bf837d9978` |
| inputs/formal-ledger.json | 71883 | `76184b66b4418fc486ec00aa530b705542bff20d6b6950c07b00c4aa8a75a06b` |
| inputs/historical-review-manifest-v11.json | 214107 | `da681f8d83ee3416faeffad30f230365e9956c8c6763aa30487d4bb809be81b4` |
| inputs/safetensors_disk_cold_01/result.json | 2214 | `444ccca4086c6d7438914794e4fbe2d39944b40764b4c9fda03601af82be050c` |
| inputs/safetensors_disk_cold_02/result.json | 2221 | `559d9e05e4028a074a0686e83faa3370e585d36b52cdcca282f17ee844ea316c` |
| inputs/safetensors_disk_cold_03/result.json | 2729 | `bc11e8931072e556958ee55a94a76776e2bd687d4caa891e693d198b0fc01cf2` |
| inputs/safetensors_disk_cold_04/result.json | 2729 | `785db88c1164bb1c2c11bdd7a4f98ed8af3c8b659d726c6db9ca2b99a0f0c269` |
| inputs/safetensors_disk_cold_05/result.json | 2717 | `46cf89c1db7c8671121660a6c823df770329ea3d00f04beb18f981d98aece812` |
| inputs/safetensors_disk_cold_06/result.json | 2713 | `9cfcdd2381f4c7768a22794cc282c80383caba3204f6c5aba0cbe512ac8215a2` |
| inputs/safetensors_disk_cold_07/result.json | 2717 | `0b23ef18a87307c54834acf0907e7a370998b386eb3d24d0abc78817f0186b49` |
| inputs/safetensors_disk_cold_08/result.json | 2715 | `4a209169f11cd6c951726a3ee22d88dc35cd9bf4af63f90be792fffbe7e99c4a` |
| inputs/safetensors_disk_cold_09/result.json | 2719 | `e2ac9d9d606d24926d365e9c0f3594925293463d7718f7d026f7c2ff7e16b4ec` |
| inputs/safetensors_disk_cold_10/result.json | 2719 | `f19596e7533121a7351e303c59474cd263743d03d394daf4ea7d5ae94b609f7f` |
| inputs/safetensors_disk_hot_01/result.json | 2661 | `78edccde4150d5e5d2384b736e45238ce44bfa21646788c1694d20ee1c343d55` |
| inputs/safetensors_disk_hot_02/result.json | 1418 | `9302df980c626be48c6ef7640f6f13a5b62f01fcf57e22a752c311b7564cedba` |
| inputs/safetensors_disk_hot_03/result.json | 1423 | `5bb3340e2d4e912682cb1540b25a10ea3722bf0a5cec52b99ad1e9fc5924001a` |
| inputs/safetensors_disk_hot_04/result.json | 2716 | `1005fdc1d20bd82e05080eb9b515c8082cbd5af810c8d450b726c297debae6f8` |
| inputs/safetensors_disk_hot_05/result.json | 2714 | `3dd7da26cabda4b2f73e9b290635b53ff398fdf8270e148512d2f22e1994ecad` |
| inputs/safetensors_disk_hot_06/result.json | 2712 | `a062ed63b35d6b6bac6c32cfb9cf5e92c112eec2116097ae9d0f46e81b4bce2e` |
| inputs/safetensors_disk_hot_07/result.json | 2715 | `4ba5e24155452a9517ef25d7a2856edc1bcc8fc476dd3f909c89ff930fd63591` |
| inputs/safetensors_disk_hot_08/result.json | 2711 | `149abfe3a6efce7443a41481e8a177349f6dc48a99b615d5f6be6fac077f1c94` |
| inputs/safetensors_disk_hot_09/result.json | 2714 | `2d6cc242e2502efd701d79bc6d484a09d271f11d90897cecfbe01421ff5ccb72` |
| inputs/safetensors_disk_hot_10/result.json | 2714 | `945117c43cb380459978880f2c3b955802cf14f2ed867058444675c193cabe7a` |
| inputs/store_disk_cold_01/result.json | 2181 | `42c9bba7f3a4eb9bd4ca6ce2de4728c50c8fa67ae6a4df7cc98c727a49b7eaf1` |
| inputs/store_disk_cold_02/result.json | 2179 | `b9d79fa9e45cd618ff84d486570a10d44b39ab1924363b48eb5391e0fcaa6dd1` |
| inputs/store_disk_cold_03/result.json | 2693 | `468d88e5553aa04b01a4dcdb93c8b21f361e811d32a531c403a30382408a8f76` |
| inputs/store_disk_cold_04/result.json | 2682 | `ae2256ebe31b0bbfe08784406b62936a6e07079b73e22daae7007fcd12e37f0c` |
| inputs/store_disk_cold_05/result.json | 2683 | `0e54771e24ca738d5ab308e5a15df0ffcf670dbba6ddb5d985de06a83105968b` |
| inputs/store_disk_cold_06/result.json | 2683 | `33f6be264cf2ca9f98a3f2d000bfbbb922834daed4b2247904666d9b41efcc68` |
| inputs/store_disk_cold_07/result.json | 2684 | `86818cbead1ecbbbed19009471ca89ebb955d2093391ba220049b5c680713a1c` |
| inputs/store_disk_cold_08/result.json | 2682 | `d837831470023f9f898310841a96fb41765f8eeb400e13377d163f970430afef` |
| inputs/store_disk_cold_09/result.json | 2684 | `8ca3ac2129c9b689f54a8b70af7673818ade1579ee41af16f5c3dca4ed872794` |
| inputs/store_disk_cold_10/result.json | 2683 | `88535060aa901af2d2412b648db282fdf1cf4bf9f03cd9c154ee05d5d9188a90` |
| inputs/store_pool_hit_01/result.json | 866 | `9fb0e81f9c63167f17da2e12d0877c8fe250fc3ff46ca6693141527cda611714` |
| inputs/store_pool_hit_02/result.json | 2165 | `a995f0259ce31f1e4a487e07dea2373e8ad20fe7832b02c019f4ae3b41bf77d3` |
| inputs/store_pool_hit_03/result.json | 2074 | `6a722075c6aeaeb94161da8b6555d6b047d5c88f9e98dbd860e2f4c9765bc2e7` |
| inputs/store_pool_hit_04/result.json | 2664 | `61bdca8ab57bb49fce0cf67bc869e7f9a5f5af9e6f69fd29fbcdcdc81f92c4ff` |
| inputs/store_pool_hit_05/result.json | 2662 | `2d8e2686b5c496fa2816306b0c2058e4270eb575a0718a868369c65870051905` |
| inputs/store_pool_hit_06/result.json | 2666 | `469c371a898185e468f80e2df25743cff3b8ad8b1c30b82307c8e3727b12ee79` |
| inputs/store_pool_hit_07/result.json | 2663 | `24bbad0133ef851cd75dd0c281eda4768a03995b040f62f9803703f983339838` |
| inputs/store_pool_hit_08/result.json | 2664 | `9371a1cbc165a390e9e64ce41dae91b0d5eb383e9e1563eb140063f8f6a6d24a` |
| inputs/store_pool_hit_09/result.json | 2661 | `ec0d4f78dae3b0e8afa0346ead448a00d560c13bba96665adf62c0b57e37d1ab` |
| inputs/store_pool_hit_10/result.json | 2662 | `84bc3947ee848f6424fb0630cf2519f76cdd2d4fd860440197763307ed601501` |
| inputs/v5-store-server.py | 8943 | `c0de1b5843767c8399a016a21a2b536259bcf13056a2f8b36255a811009f303c` |
| latency-decomposition.png | 197684 | `ab95536f9d4c33ff783afd62c2ccc62d4e4a64cf28b753de2b2c9b5502d50a49` |
| latency-decomposition.svg | 68672 | `28e358f91b8c833ecff37f25ec6e0302f440a733c0f454500033cfc4149a543d` |
| original/MANIFEST.md | 8146 | `9e95d7d67ea7e6ec0bb3a4ac8349e61e6683ba308e6ee05c623e36bdfd8d44b5` |
| original/paired-stats.json | 31218 | `66de7c92d2757397c81b7ed536a766e65d8a9c4b03051bed4f261775a0da8bf5` |
| original/recompute.py | 66739 | `cb35cf40e8433b36404b3aae96dc677020c7a9b071d46f4e0d4f23dd0153bdfc` |
| original/sanity-gate.json | 3760 | `6b453007ee3b869b9cd9321a5cae94f4d16d1dc37ad61e666c183a34d33d4054` |
| patch-inventory.json | 16414 | `a5575e174bd6dbe238eec49574a21eabf3325091e312f572445bec5a1a78a0b8` |
| plot_analysis.py | 18609 | `038706374a76162fca4ca2dfda00cf8aaac8aa6e776c8d595298560c068b12bf` |
| raw-event-inputs.json | 776417 | `c32f0e8c63fa50e4aa938673ee443c4b60f6feea19af58709b3ef2714b95f02d` |
| source-clock-evidence.json | 31553 | `068a3428a5695834b6f861e0deea41bcc729409c18cbe9b25614ae1c2e039270` |
| startup-samples.png | 72976 | `7441dfc1f9a4e7ee1aaf1e73b3f6583a6b49a6daa288b48869597a5072ba21ee` |
| startup-samples.svg | 20790 | `cdf9a362fd7aff136f9d867668e1b850cbffc6d0f7f773afe3c66292160e6a30` |
