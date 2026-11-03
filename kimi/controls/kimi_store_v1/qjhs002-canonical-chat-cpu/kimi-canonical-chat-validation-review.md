# S5 canonical chat CPU validation proposal — not executed

Candidate: `work/kimi_canonical_chat_cpu.py`, SHA256 `14151988e5729112bb32094f89cc873fef954a38210f4b96d9cf52db4985d700`. AST parse/compile only. It reuses the canonical CPU probe's seven-small-asset gate, two reviewed source hashes, immutable factory SHA `0d620af05b7c8b86e5c7d3c34db180ad058c4564694dea35911e476ad31df480`, gold reference SHA, explicit `--allow-reviewed-local-code`, and normal get_tokenizer entry with trust_remote_code=True. No tokenizer_cls bypass, production edits or new tokenizer implementation.

## Minimum cases and success contract

Only three cases, all single plain-text user message `你好 Kimi`, add_generation_prompt=True:

1. Omit thinking: official override defaults thinking=True, final assistant prefix ends `<think>`.
2. thinking=False: prefix ends `<think></think>`.
3. Default thinking with one JSON Schema declaration `echo_text` with no parameters. Required because the official override has a unique tools branch: deep_sort_dict -> reviewed encode_tools_to_typescript_style -> tools_ts_str -> template tool_declare block. No Python callable/tool is supplied or invoked, no tool calls/results or model-generated arguments are tested. This is declaration formatting only.

The small no-tools rendered strings and tool declaration are specified exactly in the probe. For each case, compare actual official-template rendered text with the expected string; compare apply_chat_template(tokenize=True) exact IDs against existing gold-pattern/ranks/special-ID tiktoken.Encoding over that same independently specified text. Also verify native encode(rendered,add_special_tokens=False) and decode without stripping special tokens. Use allowed_special='all' for the core reference: chat text contains explicit control tokens, unlike the earlier ordinary-text core cases using disallowed_special=(). No extra BOS/EOS insertion or padding/truncation/tensors are requested. HF apply_chat_template internally sets add_special_tokens=False, so the template's control tokens appear once.

Probe passes options through actual vLLM resolve_chat_template_kwargs and checks none of the intended kwargs are dropped. It records actual apply_chat_template implementations/signatures in the runtime MRO. No repeated nested/pickle/core12 tests: those already have separate evidence.

## Source observations / TF5 compatibility to test

Actual official override signature:

`apply_chat_template(self,conversation,tools=None,tokenize=False,add_generation_prompt=True,thinking=True,preserve_thinking=False,**kwargs)`.

It wraps the superclass, builds tools_ts_str when tools are present, then forwards all flags. TF5.5 PreTrainedTokenizerBase's method has `tokenize=True`, `add_generation_prompt=False`, **return_dict=True**, tokenizer_kwargs=None plus other optional parameters. The official override explicitly supplies tokenize/generation/thinking and accepts other HF parameters through kwargs. It does not reject return_dict. A runtime error would be evidence requiring investigation; do not change signatures/flags to mask it.

vLLM0.19.1 `renderers/params.py:ChatParams.get_apply_chat_template_kwargs` explicitly merges return_dict=False. The probe mirrors that contract so tokenized output must be list[int], not TF5's default BatchEncoding. It also prints method MRO/signatures to distinguish the cached official class from the generic native backend. `renderers/hf.py:safe_apply_chat_template` calls the tokenizer override with conversation/tools/chat_template/tokenize and resolved kwargs, matching these tested arguments.

Selected template, HF/vLLM method source and SHA evidence: `kimi-canonical-chat-source.json`; full reviewed two Python sources and TF superclass are in `kimi-official-class-source.json`.

## Important boundary of the test

This test exercises tokenizer.apply_chat_template using the tokenizer's official chat_template, plus vLLM kwargs filtering. It does not instantiate ModelConfig/LLM/renderers or perform default template discovery. Source shows vLLM resolve_chat_template prefers AutoProcessor when tools=None before tokenizer.get_chat_template. The actual full snapshot has a preprocessor_config with additional Python auto_map; those assets are outside the reviewed seven-file scope. Thus a pass must not be called default native HTTP renderer/AutoProcessor readiness. That discovery path needs its own narrowly reviewed evidence if it will be used; no need to widen this first CPU test with image/video processors.

No history reasoning preservation, tool-result loop, JSON-schema variants, tool-call parsing, assistant masks, multimedia, batching, streaming or model inference in this minimum plan. Default thinking and disabled thinking here concern prompt prefix only, not model reasoning behavior.

## Proposed outer scope

Reuse the already reviewed canonical factory overlay and same immutable existing image; runc, no GPU devices, network none, readonly root and seven named checkpoint assets under3MiB, gold readonly, bounded /tmp256MiB, memory4GiB/cpu1/pids256. Explicit existing reviewed-code flag, HF offline variables and HF_MODULES_CACHE/HF_HOME/XDG_CACHE_HOME/TIKTOKEN_CACHE_DIR under /tmp before imports. Inner timeout60s/kill10s and external bounded observer/cleanup at90s; passive cuda.is_initialized checks before/after. Bind the SHA-reviewed hf.py overlay readonly, not a library installation or production change. Do not retry after failure, install dependencies, execute tools, construct models or expose weights/index. No GPU/container execution occurred during this preparation.

Success label is `CANONICAL_KIMI_TOKENIZER_CPU_CHAT_CASES_PASS`: only these three text-chat prompt paths and exact ID cases in this environment. It does not establish full native service/model/GPU readiness or resolve the model README TF<5 discrepancy globally.
