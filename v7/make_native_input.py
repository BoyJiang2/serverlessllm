"""Derive the native (Store-free) GLM reference input from the frozen chain input."""
import hashlib
import json
from pathlib import Path

V7 = Path('/home/boyjiang/sllm-v4-pro/cache-competition-20261109/v7')
source = json.loads((V7 / 'candidates-v2/glm-input.json').read_bytes())
document = dict(source)
document['scenario'] = 'native'
document['record_only'] = True
document['source'] = 'derived from candidates-v2/glm-input.json requests; only the weight source differs'
config = dict(source['config'])
config['load_format'] = 'auto'
config['pretrained_model_name_or_path'] = '/data/brick1/boyjiang/glm5.1-fp8/checkpoint'
config.pop('model_loader_extra_config', None)
document['config'] = config
path = V7 / 'glm-native-input.json'
path.write_text(json.dumps(document, indent=2) + '\n')
print(json.dumps(dict(
    path=str(path),
    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    requests=[item['name'] for item in document['requests']],
    load_format=config['load_format'],
    model=config['pretrained_model_name_or_path'],
), indent=2))
