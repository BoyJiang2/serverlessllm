# V4-Pro 指定权重目录与冻结 revision 核验

2026-11-06。本批只读复核 qjhs2:/mnt/raid/models/DeepSeek-V4-Pro；未执行完整权重哈希、模型导入、环境安装、Store转换或GPU。运行源码仍为73184e7，274项CPU回归对应的源文件未改，不因文档/证据补充重复跑套件。

## 实际结论

| 项目 | 新鲜证据 | 边界 |
|---|---|---|
| 官方固定来源 | [revision文件树](https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro/tree/b5968e9190ef611bbf34a7229255be88a0e937c1)；[固定revision API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4-Pro/revision/b5968e9190ef611bbf34a7229255be88a0e937c1?blobs=true)，原JSON23721字节、91文件，记录访问时间/SHA | 固定目标是b5968e9190ef611bbf34a7229255be88a0e937c1；不是本地内容已全部证明 |
| 64份权重 | 新官方清单的名称、尺寸及LFS SHA与此前64份声明完全一致；本地根目录无额外safetensors，尺寸均匹配 | 864721029744字节；实际完整文件SHA验证数仍为0 |
| index/headers | 新读index全SHA匹配官方；64份header长度/摘要与前次一致，145116 key覆盖、shape/dtype/offset连续性与index一致 | header/index不证明tensor数值；未读取请求的tensor数据区 |
| 16个运行输入 | 15个既定小资产及index全部完整哈希匹配固定revision | tokenizer.json/config、generation/config及既定编码资产；不是64个权重文件哈希 |
| 其余非权重文件 | 27个官方非权重文件中26个完整内容匹配；本地.gitattributes存在但不同 | 本地6509字节，官方1668字节，原文/哈希已归档为.txt防止被Git解释；原因未确定，不修改模型目录 |
| 目录来源标记 | .mv仍为Revision:master | 不是HF固定commit内容证明；该目录不能宣称是完整一致的HF checkout |
| CPU工具发现 | 当前python3用find_spec发现requests，未发现tokenizers/transformers | 未导入这些模型库或安装；不代表整个服务器没有其他环境，也不是目标tokenizer资格 |

主检查对模型目录请求读取35069037字节元数据（约33.45MiB），包括完整小文件/index和safetensors header，不包括控制脚本/回执或额外差异副本的读取。OS/NFS readahead实际字节未测量，故不能声称物理payload IO为0。只读不是cache-neutral；后续正式cold gate必须在这些读取之后重新准备。

## 哈希口径必须区分

[Git LFS规范](https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md)的oid对应原始文件对象。expected_file_sha256校验从文件offset0到EOF，包含8字节header长度、JSON header和数据区。它不是只对tensor数据区计算的SHA。blobId是Git里的LFS pointer对象，不可拿来校验大文件；Xet标识也不替代这里明确列出的LFS SHA256。

旧回执的expected_payload_sha256_not_verified/lfs_sha256_declared_not_payload_verified保存的是整文件LFS SHA，命名有歧义，保持原始证据不改写。新expected-weight-files.json明确hash_scope=whole_file_including_safetensors_header，64行expected-whole-files.sha256可供后续coreutils sha256sum --check使用。当前source_status为METADATA_MATCHES_FULL_WEIGHT_CONTENT_UNVERIFIED，source_revision_content_verified=false。

16个运行输入/26个非权重文件的exact hash不包括权重payload。非运行文件.gitattributes不同不能推出数值权重不同，也不能据此自动升级为“revision完整匹配”。完整revision内容资格仍要求64个实际文件完整SHA相等，并保证校验/加载期间数据封闭。

## 可评审的下一步完整文件校验方案（未执行）

输入为本次64行校验清单及固定官方原JSON；只读现有权重、不复制。现有coreutils足够，不安装模型环境，不占用GPU。以单进程低CPU优先级从头到尾读取805GiB左右，日志全部写 /home/boyjiang/sllm-v4-pro/evidence 下新的job目录。nice/ionice不保证NFS服务端带宽限制，须先明确I/O时段/预算。

读取前后保存本次清单SHA、真实path/dev/inode/size/mtime/ctime及worker主机身份；全部64个文件通过且文件状态稳定才能形成完整内容回执。失败、部分完成或修改中的文件保留日志，不能因完成了一部分而标整个模型通过。stat稳定本身不是恶意写入防护，仍需封闭输入和控制并发写者。

计划命令语义：在 /mnt/raid/models/DeepSeek-V4-Pro 工作目录运行 sha256sum --check 指向本次expected-whole-files.sha256，将stdout/stderr写入任务新job目录，保留原始退出码和每文件结果。不得对单纯数据区哈希，不得向模型目录写标记，不清page cache、不启动模型。实际IO耗时未知；若持续100MiB/s约137分钟，256MiB/s约54分钟，仅为字节数除速度的预算，不是实测吞吐。完整读取会影响客户端/服务端缓存，必须计入bootstrap准备成本并在未来正式cache gate前完成。

鉴于当前范围是独立分支和CPU契约验证，本批未自行启动这项805GiB实数据IO；校验清单和方案已可审阅。无论完整SHA后续是否通过，native gold、ABI/内核、全Store恢复、物理释放仍需后续各自证明，不由文件校验替代。

## 证据

全部原JSON、API回执、旧声明、新鲜本地检查、差异原文、normalized整文件清单、检查脚本与bundle-manifest在docs/evidence/v4-weight-source-p3/。检查使用此前inspect_existing_v4_assets的header/index逻辑，限制header长度与前次一致并将主检查预算控制在64MiB内；.gitattributes副本使用.txt文件名，不作为Git属性执行。原模型目录未写入或移动，旧回执仍保留。
