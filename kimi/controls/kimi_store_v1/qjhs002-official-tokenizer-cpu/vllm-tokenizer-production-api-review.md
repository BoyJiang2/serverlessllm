# vLLM0.19.1 official tokenizer production API / minimum fix review

## Conclusion

No existing no-code CLI/LLM/ModelConfig path passes an actual Python tokenizer class into native KimiK25 serving or LLM conversion. The low-level `vllm.tokenizers.get_tokenizer(tokenizer_cls=ActualClass,...)` is useful for the CPU probe but is not itself production integration. A passing direct-class test must not be reported as native serve/LLM factory success.

Canonical factory: **`vllm/tokenizers/hf.py`, `CachedHfTokenizer.from_pretrained`**, not deprecated `vllm/transformers_utils/tokenizer.py` (only a forwarding alias). Exact selected wheel sources/SHA and callsite evidence are in `vllm-tokenizer-production-api-source.json`.

## Actual call chain and extension boundaries

- `vllm/entrypoints/llm.py:224–227` exposes tokenizer path/string, tokenizer_mode and trust_remote_code; :346–378 passes them plus kwargs to EngineArgs. `tokenizer_cls` is not an EngineArgs/ModelConfig field; arbitrary kwargs are not a passthrough to the tokenizer factory.
- `vllm/engine/arg_utils.py:386–387` carries mode/trust. CLI flags at:695–699 are --tokenizer, --tokenizer-mode, --trust-remote-code. :1404–1407 forwards these to ModelConfig.
- `vllm/config/model.py:124–127` tokenizer is a name/path string and tokenizer_mode is a mode string. Modes can be extended via plugins, but no dotted-class path auto-registration exists here; registration requires new code. slow/use_fast still reaches AutoTokenizer and does not select the official Kimi class.
- Serve `entrypoints/openai/api_server.py:453`, AsyncLLM `v1/engine/async_llm.py:135`, LLMEngine `v1/engine/llm_engine.py:92` all call `renderer_from_config(config)` without class kwargs. `renderers/registry.py:83` calls cached_tokenizer_from_config(model_config,**kwargs); its extra-kwargs facility is not exposed by these production callers.
- `tokenizers/registry.py:256–267` cached_tokenizer_from_config passes path, runner_type, mode, revision and explicit trust to cached_get_tokenizer, with no actual class from ModelConfig. `get_tokenizer:195` has tokenizer_cls, but its callers use default except the separate KimiAudio model's hardcoded KimiAudioTokenizer (`models/kimi_audio.py:641`). There is no KimiK25 class-selection callsite. This0.19.1 wheel contains no TokenizerGroup implementation; renderer/tokenizer registry is the applicable architecture.
- Default auto resolves to hf and loads CachedHfTokenizer. Its from_pretrained calls AutoTokenizer, then retains sentence-transformer special-token behavior and returns get_cached_tokenizer(tokenizer). Those final steps must stay intact.

## Smallest targeted source change to propose after official-class CPU validation

Modify only the tokenizer-construction assignment in canonical `CachedHfTokenizer.from_pretrained`, retaining its signature:

`(cls,path_or_repo_id: str|Path,*args,trust_remote_code: bool=False,revision: str|None=None,download_dir: str|None=None,**kwargs) -> HfTokenizer`.

Select the direct official-class construction only when all targeted facts hold: installed Transformers version exactly5.5.1; the caller explicitly supplied trust_remote_code=True; path is an existing local directory; local config.json has model_type=kimi_k25; tokenizer_config.json has tokenizer_class=TikTokenTokenizer and exact local auto_map.AutoTokenizer=[tokenization_kimi.TikTokenTokenizer,None]. Do not accept a repo-qualified -- reference, arbitrary module/class or implicit truthy trust value. For this intended local Kimi/trust/version branch, malformed/mismatched mapping should raise an informative exception rather than silently select the known-bad generic fallback. Other model/version/trust paths keep original behavior.

Inside that branch, use official Transformers `get_class_from_dynamic_module('tokenization_kimi.TikTokenTokenizer',path_or_repo_id,...)` then its `.from_pretrained(path_or_repo_id,*args,...)`. Do not change trust to True internally. The loader executes reviewed Python immediately and has no trust gate of its own: branch selection must enforce the explicit caller authorization before calling it. A production source change does not grant permission to execute any newly changed checkpoint Python; version/source binding and runtime authorization remain external review requirements.

Preserve both calls' applicable arguments: revision to dynamic loader and tokenizer; code_revision from existing kwargs to dynamic loader and unchanged kwargs to tokenizer; cache_dir=download_dir matching the existing factory contract; local_files_only/token/proxy/force_download inputs to dynamic loader where provided and unchanged tokenizer kwargs to `.from_pretrained`. Use a separate filtered loader-kwargs view so tokenizer-specific kwargs are not consumed/dropped. Local-only test must set HF_MODULES_CACHE explicitly under bounded /tmp; cache_dir alone does not control dynamic-module code cache. Do not mutate kwargs in a way that changes the old AutoTokenizer branch.

Let dynamic-load/import/from_pretrained exceptions propagate with cause. The known-target branch must not catch such an exception and retry AutoTokenizer/generic conversion. The existing outer ValueError translation can be retained if it preserves the cause and is not a fallback; do not replace an actual direct-class import or mapping failure with a misleading suggestion to enable already-enabled trust.

After tokenizer construction, continue the **existing shared tail**: get_sentence_transformer_tokenizer_config, optional lowercasing of special tokens, then `get_cached_tokenizer(tokenizer)`. An early return of the official class would lose this canonical factory behavior. No AutoTokenizer global register, replacement tokenizer assets, codec, extra loader framework, plugin/wrapper launcher, regex switch or separate serving implementation is required.

This is a source-fix proposal, not an implemented patch. Exact production diff should follow only a successful independently reviewed CPU official-class test and separate source review; do not install/apply to the immutable image during measurement.

## Necessary evidence after a candidate exists

Use the same bounded CPU image/small readonly assets and explicit trusted-code permission to exercise the **canonical patched factory** under default tokenizer mode, in addition to the low-level class probe. Compare all23 named IDs,256 reserved entries and the same exact-ID corpus; confirm returned cached class and official tiktoken pattern/ID state. Include negative cases: trust=False does not execute checkpoint code; different TF/model unchanged; targeted incorrect auto_map rejects; direct-class load failure propagates without generic fallback. Test revision/code_revision/cache/local arguments with focused source-level call assertions instead of building model/LLM objects merely to inspect forwarding. Then verify actual production CLI/LLM uses that same source SHA via the demonstrated unchanged call chain; that is still source/CPU evidence, not model/GPU/service approval.

No production/library/checkpoint files were modified by this review. No containers/models/GPU/Store or dependency installations were executed. Active adaptation root remains `/data/brick1/boyjiang/kimi-new/ServerlessLLM`; old official root remains readonly.
