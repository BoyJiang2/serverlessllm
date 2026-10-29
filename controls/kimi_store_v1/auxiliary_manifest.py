"""Bind explicit Marlin scratch normalization recipes to exported layouts."""
import json

from contract import CONFIG_SHA256
from marlin_aux import METHOD_CLASS, METHOD_MODULE, NAMES, PROFILE, SOURCE_SHA256
from runtime_inventory import is_moe_auxiliary


def _layout(width):
    return dict(shape=[384, width], stride=[max(width, 1), 1], dtype='torch.int32',
                storage_offset=0, storage_bytes=384 * width * 4)


def validate_auxiliary_receipts(receipts, layouts, rank):
    expected_names = {n for n in layouts if is_moe_auxiliary(n)}
    if not receipts and not expected_names:
        return []
    if type(rank) is not int or rank not in range(8) or not isinstance(receipts, (list, tuple)):
        raise ValueError('auxiliary receipts require an explicit TP8 rank and list')
    # Copy JSON metadata so a caller cannot mutate receipts after validation.
    normalized = json.loads(json.dumps(receipts))
    covered = set()
    for receipt in normalized:
        layer = receipt['layer']
        names = {layer + '.' + leaf for leaf in NAMES}
        if (not names <= expected_names or names & covered
                or any(layouts[n] != _layout(0) for n in names)):
            raise ValueError('auxiliary receipt coverage/layout mismatch')
        before = {leaf: _layout(7168 if leaf.startswith('w13') else 256) for leaf in NAMES}
        expected = dict(layer=layer, rank=rank, phase='checkpoint_loaded_before_postprocess',
                        action='normalize_to_empty_before_postprocess', config_sha256=CONFIG_SHA256,
                        method_module=METHOD_MODULE, method_class=METHOD_CLASS,
                        method_source_sha256=SOURCE_SHA256, kernel_backend='Marlin', actorder=None,
                        profile=dict(PROFILE), vllm_version='0.19.1', before=before,
                        after={leaf: _layout(0) for leaf in NAMES}, scratch_payload_read=False,
                        actual_backend_initialized=False, all_layers_verified=False,
                        native_loader_verified=False, runtime_admitted=False)
        if json.dumps(receipt, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError('changed auxiliary normalization recipe')
        covered.update(names)
    if covered != expected_names:
        raise ValueError('missing auxiliary normalization receipt')
    return normalized
