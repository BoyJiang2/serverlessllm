# S5 minimal tokenizer support path selection — read-only

**Choose A for the next independently reviewed CPU validation; do not modify production or downgrade dependencies now.** Reuse the checkpoint's official tokenizer class directly through the existing vLLM `tokenizer_cls` argument. This is an explicit trusted-code path, not a change to default trust policy. Source feasibility only: the official class has not yet been instantiated under TF5.5.1 in this review.

## Evidence and scope

The actual snapshot `docs/deploy_guidance.md:23` includes `--trust-remote-code` in its vLLM0.19.1 TP8 example. That documents the expected trusted-code deployment mode; it does not itself authorize executing arbitrary snapshot code or a GPU/service run. The readonly CPU validation must receive explicit scope authorization for the two reviewed files before invoking the dynamic loader.

Fully read `tokenization_kimi.py` (13,462B, SHA256 `2ab1ffb6f5c4380758bd8d9752ff1041c09024182676a4311528fbdf92fb9599`) and its sole relative import `tool_declaration_ts.py` (16,517B; exact SHA in `kimi-official-class-source.json`). The tokenizer imports os/collections/logging/pathlib/shutil/typing, tiktoken, tokenizers.AddedToken, transformers bytes_to_unicode and PreTrainedTokenizer; its relative helper imports only dataclasses/json/logging/collections.abc/typing. Reviewed module top level defines constants/functions/classes and obtains logger objects; the helper has no further relative/third-party imports, network calls, subprocess or model creation. Tokenizer init reads local ranks, builds tiktoken.Encoding and a163840-entry decoder/encoder; save_vocabulary's copyfile is only called when that save method is explicitly invoked. Do not call save methods. Tool rendering executes only if apply_chat_template receives tools; exclude it from this first test.

TF5.5.1 has a backwards-compatible import path despite the absent literal tokenization_utils.py wheel entry: `transformers/__init__.py:823` aliases tokenization_utils to tokenization_utils_sentencepiece, whose line29 imports PreTrainedTokenizer from tokenization_python; that module line1418 aliases PreTrainedTokenizer=PythonBackend. The official tokenizer therefore subclasses the Python backend, not the generic TokenizersBackend that produced compacted IDs. from_pretrained processes added_tokens_decoder as AddedToken objects before calling the class initializer. Runtime subclass/init compatibility remains to be tested.

## Existing APIs, no new framework

`get_class_from_dynamic_module(class_reference, pretrained_model_name_or_path, cache_dir=None, force_download=False, proxies=None, token=None, revision=None, local_files_only=False, repo_type=None, code_revision=None, **kwargs) -> type` (TF dynamic_module_utils:476).

This API has **no trust_remote_code gate**: calling it copies reviewed local Python+relative imports into HF_MODULES_CACHE and executes them immediately. Passing trust_remote_code=True to it is not an authorization mechanism. Assert an externally supplied explicit trusted-source authorization flag and both source SHA256 values *before* this call. Set HF_MODULES_CACHE to bounded tmpfs before importing transformers. `cache_dir` alone does not relocate the dynamic module cache.

Inherited tokenizer `from_pretrained(cls, pretrained_model_name_or_path, *init_inputs, cache_dir=None, force_download=False, local_files_only=False, token=None, revision='main', trust_remote_code=False, **kwargs)`.

Existing `vllm.tokenizers.get_tokenizer(tokenizer_name, *args, tokenizer_cls=TokenizerLike, trust_remote_code=False, revision=None, download_dir=None, **kwargs)` selects the passed tokenizer_cls directly, calls its `.from_pretrained`, and returns it. `tokenizer_mode` is accepted through kwargs. `vllm/tokenizers/hf.py CachedHfTokenizer.from_pretrained` always uses AutoTokenizer; it has no class-selection parameter. Do not patch that factory merely to bypass it. AutoTokenizer under TF5.5 may still force generic fallback for kimi_k25 even with trust=True, so direct class selection is essential here.

After an explicit reviewed-source authorization gate, the minimal proposed calls are:

```python
from transformers.dynamic_module_utils import get_class_from_dynamic_module
Official = get_class_from_dynamic_module(
    'tokenization_kimi.TikTokenTokenizer', '/checkpoint', local_files_only=True)
from vllm.tokenizers import get_tokenizer
tokenizer = get_tokenizer(
    '/checkpoint', tokenizer_cls=Official, tokenizer_mode='hf',
    trust_remote_code=True, local_files_only=True)
```

Using the class directly as `Official.from_pretrained('/checkpoint',local_files_only=True,trust_remote_code=True)` is an even smaller construction-only first phase; the existing vLLM factory call above should be the second phase if its integration contract is the intended validation. Do not instantiate two copies simultaneously unnecessarily. No monkeypatch, AutoTokenizer registration override, codec, loader, regex flag or changed model asset is needed for this experiment.

## Independently reviewable CPU admission proposal

Use the same immutable image, runc, no GPU devices, network none, readonly root, memory4GiB/cpu1/pids256, bounded /tmp256MiB cache, inner timeout60s/kill10s and bounded external cleanup/observer. HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, HF_MODULES_CACHE/HF_HOME/XDG_CACHE_HOME/TIKTOKEN_CACHE_DIR under /tmp, PYTHONDONTWRITEBYTECODE=1. Bind only the six existing small tokenizer/config assets plus the reviewed tool_declaration_ts.py (total under3MiB) as readonly files; do not expose model Python, weights, index or whole snapshot. Assert exact filename set, sizes and SHA, no /dev/nvidia*, torch.cuda.is_initialized()==False before/after. Trusted-source scope must name both executable files and their hashes explicitly; the previous trust=False permission does not cover this execution.

Capture phase markers, actual class+MRO+module paths, source/cache file SHA, vocab/len/base vocab, all23 named IDs and256 reserved IDs, and the same12 exact-ID cases against the already defined AST/tiktoken core reference. Expect PAD163839/UNK163838 and n_vocab163840; check, do not assume. Match special-token/add_special_tokens semantics explicitly. A passing corpus establishes only this official class construction and selected tokenizer IDs on this image; it does not waive the README's TF<5 bound, prove full wrapper/chat-template behavior, or authorize production/GPU/model/Store. Any import/init exception stops the test without installing, editing checkpoint code or switching versions.

## Why B is not the minimum next action

The exact4.57.1 PyPI release metadata was read (no wheel download): https://pypi.org/pypi/transformers/4.57.1/json . Its10 default dependencies evaluate9 satisfied /1 conflict against the recorded runtime inventory. Conflict is huggingface-hub>=0.34.0,<1.0 versus installed1.14.0. Installed tokenizers0.22.2 satisfies candidate>=0.22.0,<=0.23.0. This is evidence for4.57.1 specifically, not every4.57.x version.

A TF4.57.1 downgrade alone would leave an incompatible hub version; downgrading hub adds a second package change whose impact on other installed clients needs an entire active dependency/source audit. Existing vLLM0.19.1 direct constraint accepts4.57.1 and tokenizers0.22.2, but that alone does not prove its TF5-era imported APIs have v4 fallbacks on every relevant path. Native config/toolchain/runtime tests must be repeated for any chosen version. Current downloads/maxdepth1 regular-file listing contains only transformers5.5.1 and no4.57.x, huggingface_hub or tokenizers wheel. Thus B has offline asset and dependency-closure gaps and is a separately planned candidate, not an approved direct downgrade.

All new artifacts are local work evidence only; no production files, old official readonly root, Git, packages, environments or containers were changed/executed by this review. The active adaptation root is `/data/brick1/boyjiang/kimi-new/ServerlessLLM`; this review did not write either root.
