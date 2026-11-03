# Chat CPU fixture correction — source-backed, not executed

The first script's tool-text fixture omitted a terminal LF. This was my fixture error; preserve its rc1 and original SHA `14151988e5729112bb32094f89cc873fef954a38210f4b96d9cf52db4985d700` unchanged. Default thinking and thinking=False completed11/12-token checks before the tool-text assertion failed; the failed tool case has not yet passed its ID checks.

Independent source evidence is the fully reviewed official `tool_declaration_ts.py`, SHA `97e09ed262f1cd38f87584eaa46c5d4d5b3c9725dd299b21642f389040794ebc`: lines475–477 append the namespace opener, functions_str plus LF, and then `result += "}\n"`; line479 returns result unchanged. Therefore the official declaration ends with LF by construction. The official tokenizer passes this tools_ts_str into the unchanged Jinja template, whose tools block inserts it immediately before `<|im_end|>`. The terminal LF belongs to the inserted string; Jinja whitespace controls around template blocks do not remove characters within that variable's value.

`work/kimi_canonical_chat_cpu_corrected.py` changes only tool_text's literal suffix from `...\n}'` to `...\n}\n'`. It does not infer expected IDs from actual native output, alter assertions/flags/cases, modify production/source assets, or change the gold reference SHA `0870a824e31d178bdeb9a04f93fee2289ba2bd8815b49dacb252189935b7d664`.

Corrected script SHA256: `fb9029dcda2919af5e64bdfbf931200d4d0331759351d84cf61142aec879ef66`. AST parse/compile passed; original bytes were rechecked unchanged. No container/tokenizer execution performed. Root can independently review and authorize one complete three-case CPU rerun with the same outer controls, while retaining first-run failure evidence.
