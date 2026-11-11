"""Compare the Store-restored GLM outputs with the native 0.20.1 reference.

The earlier run stopped at fixed32, so the added question has no Store response
in that run; equality is therefore computed only over requests both paths
produced, and the added question is quoted from the passing chain instead.
"""
import json
import sys
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
STORE_RUN = 'v7-store-20261110T125209Z'
CHAIN_RUN = 'v7-store-20261110T140421Z'
NATIVE_RUN = sys.argv[1] if len(sys.argv) > 1 else 'v7-native-glm-20261110T135319Z'
NAMES = ('business_arithmetic', 'business_translation', 'fixed32', 'v7_added_arithmetic')


def load(path):
    document = json.loads(path.read_bytes())
    response = document['response']
    if 'error' in response:
        return dict(error=response['error'])
    choice = response['choices'][0]
    return dict(
        content=choice['message']['content'],
        token_ids=choice.get('token_ids'),
        finish_reason=choice['finish_reason'],
        usage=response['usage'],
    )


rows = []
for name in NAMES:
    store_path = V7 / 'runs' / STORE_RUN / '01-fixed-glm' / f'before-{name}-response.json'
    native_path = V7 / 'runs' / NATIVE_RUN / 'backend' / f'before-{name}-response.json'
    store = load(store_path) if store_path.exists() else dict(absent=True)
    native = load(native_path) if native_path.exists() else dict(absent=True)
    shared = not store.get('absent') and not native.get('absent')
    rows.append(dict(
        name=name,
        shared=shared,
        store_absent=store.get('absent', False),
        native_absent=native.get('absent', False),
        same_tokens=(store.get('token_ids') == native.get('token_ids')) if shared else None,
        same_text=(store.get('content') == native.get('content')) if shared else None,
        store_tokens=(store.get('token_ids') or [])[:12],
        native_tokens=(native.get('token_ids') or [])[:12],
        store_text=(store.get('content') or '')[:60],
        native_text=(native.get('content') or '')[:60],
        store_usage=store.get('usage'),
        native_usage=native.get('usage'),
    ))

# The added question was answered by the passing chain on the Store path.
chain_answer = V7 / 'runs' / CHAIN_RUN / '01-fixed-glm' / 'before-v7_added_arithmetic-response.json'
chain_row = load(chain_answer) if chain_answer.exists() else {}
shared_rows = [row for row in rows if row['shared']]
document = dict(
    store_run=STORE_RUN,
    chain_run=CHAIN_RUN,
    native_run=NATIVE_RUN,
    comparison_scope='requests produced by both the Store-restored run and the native run',
    rows=rows,
    all_shared_requests_equal=all(row['same_tokens'] and row['same_text'] for row in shared_rows),
    shared_requests=[row['name'] for row in shared_rows],
    added_question=dict(
        store_chain_run=CHAIN_RUN,
        store_content=chain_row.get('content'),
        store_token_ids=chain_row.get('token_ids'),
        native_content=next((row['native_text'] for row in rows
                             if row['name'] == 'v7_added_arithmetic'), None),
        note='the Store measurement run stopped at fixed32 before this request; '
             'the value above comes from the passing chain run',
    ),
)
(V7 / 'native-reference-comparison.json').write_text(
    json.dumps(document, indent=2, ensure_ascii=False) + '\n')
print(json.dumps({key: document[key] for key in
                  ('all_shared_requests_equal', 'shared_requests', 'added_question')},
                 indent=2, ensure_ascii=False))
