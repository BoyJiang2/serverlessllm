# Unified loading boundary audit

The user boundary covers fresh construction/storage allocation through assignment/restore and necessary loader processing return. Immediately before/after model_loader.load_model covers this scope: model construction occurs inside that method for both SF and Store, not in loader-object selection. Starting at load_weights/load_dict would omit fresh model allocation.

All8-rank span requires max(host end)-min(host begin), authenticated ownership and common host clock domain. Old f7 provides a local logged duration without an identified rank or absolute endpoints; the collected log is not a set of eight identified rank durations and cannot retrospectively provide the exact all-rank host span.

Active f7 timer4750-4810 surrounds factory4753, loader4754-4756 and optional LoRA/drafter/aux. Metadata evidence capture begins after timer end. Store fresh model101, restore123, assignment126, generated-buffer device move128, runtime hook132, attention138 and return141 are inside load_model. SF BaseModelLoader similarly creates model55 before load_weights64, postprocess81 and return83.

All38 qualified frozen configs omit LoRA/speculative enablers. Exactcommit EngineArgs explicitly defaults enable_lora=False and only creates LoRAConfig when true; no default was guessed. Installed arg_utils byte identity and per-worker lora object state are not newly recovered. Two saved actual SFhot/Storecold engine configs explicitly show speculative_config=None; f7 constructs drafter only under that config, while aux startsFalse and is enabled only within speculative branches. These are configuration/source conclusions, not38 new runtime inspections.

No source, runtime, GPU, container, Store, weights or Git operation was performed. Exact source paths/SHA/line references and38spec refs are in the JSON.
