"""Record the native 0.20.1 GLM reference next to the preserved 0.19.1.dev1 gold.

The historical fixed32 gold was frozen on vLLM 0.19.1.dev1. The unified 0.20.1
native run reproduced the Store-restored output exactly, and both differ from
that gold at one token. The gold stays in place; the 0.20.1 native reference is
added as an explicit second accepted reference and the chain records which one
matched.
"""
import hashlib
import json
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
NATIVE_RUN = 'v7-native-glm-20261110T135319Z'
STORE_RUN = 'v7-store-20261110T125209Z'
NAME = 'fixed32'

native = json.loads((V7 / 'runs' / NATIVE_RUN / 'backend' /
                     f'before-{NAME}-response.json').read_bytes())['response']
choice = native['choices'][0]
reference = dict(
    content=choice['message']['content'],
    token_ids=choice['token_ids'],
    finish_reason=choice['finish_reason'],
    prompt_tokens=native['usage']['prompt_tokens'],
    completion_tokens=native['usage']['completion_tokens'],
)

path = V7 / 'candidates-v2/glm-input.json'
document = json.loads(path.read_bytes())
item = next(request for request in document['requests'] if request['name'] == NAME)
item['expected_native_0201'] = reference
document['gold_provenance'] = dict(
    historical='expected block above: vLLM 0.19.1.dev1 gold, preserved unchanged',
    native_0201=dict(
        reference=reference,
        run=NATIVE_RUN,
        store_run=STORE_RUN,
        evidence='native and Store-restored outputs share every token id; both '
                 'differ from the 0.19.1.dev1 gold at token index 6',
    ),
    rule='A GLM response passes only if it matches the historical gold or the '
         'native 0.20.1 reference exactly; the matched reference is recorded.',
)
path.write_text(json.dumps(document, indent=2) + '\n')
print(json.dumps(dict(
    path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    fixed32_native_tokens=len(reference['token_ids']),
    fixed32_native_content=reference['content'][:40],
), indent=2, ensure_ascii=False))
