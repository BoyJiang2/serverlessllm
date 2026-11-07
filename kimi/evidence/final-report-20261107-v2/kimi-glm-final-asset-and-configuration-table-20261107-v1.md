# Kimi / GLM asset and configuration table

| Asset | Payload files / bytes | Metadata + provenance files / bytes | All regular files / bytes |
|---|---:|---:|---:|
| Kimi safetensors | 64 / 595177988208 | 20 / 26645943 | 84 / 595204634151 |
| Kimi store | 64 / 617000763392 | 40 / 28833109 | 104 / 617029596501 |
| GLM safetensors | 142 / 756177645648 | 9 / 31690471 | 151 / 756209336119 |
| GLM store | 80 / 762037043200 | 26 / 48703043 | 106 / 762085746243 |

GLM Store frozen metadata subset:22files /48,659,964bytes; full current root also contains README/LICENSE and provenance files. Kimi metadata20/40files versus GLM root/provenance categories must not be silently treated as identical inventories.

| Configuration from exact config.json | Kimi | GLM |
|---|---|---|
| Architecture | KimiK25ForConditionalGeneration (multimodal) | GlmMoeDsaForCausalLM |
| Text layers / hidden size |61 /7168 |78 /6144 |
| Routed experts / selected per token / shared |384 /8 /1 |256 /8 /1 |
| Quantization | compressed-tensors pack-quantized:4-bit symmetric group32 on targeted Linear weights; ignored modules retained separately | FP8 e4m3,128x128 block weights, dynamic activations; modules_to_not_convert exceptions |
| Base dtype | bfloat16 | bfloat16 |
| Exact total / activated parameter count | Unknown from checked config | Unknown from checked config |

All values are apparent stat bytes, not allocated disk blocks, parameter counts, device memory or pure tensor logical bytes. Safetensors shard bytes include file headers; Store physical bytes include alignment. No payload content read or new hash scan. Current findmnt:both assets are local XFS /dev/nvme1n1. Kimi source/control remains shared NFS; equal device does not make all IO or software identical.

GLM source checkpoint142 shards stat-matched against frozen manifest; Store80part sizes stat-matched against frozen raw-disk-verification receipt,22metadata sizes against store-metadata-inputs. Inactive MTP Store assets outside activeTP8 excluded. Kimi188files matched frozen dev/inode/bytes/mtime/uid exactly.

JSON: kimi-glm-final-asset-and-configuration-table-20261107-v1.json
SHA256: a0534de2d1ca92bbf70d6545c3a4ce0693be3a8fc2cfb1de8bc913e6e7e47851
