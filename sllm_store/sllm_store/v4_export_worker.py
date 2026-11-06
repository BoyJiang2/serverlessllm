"""Named vLLM worker RPC: return control metadata, never a model or tensor."""
import json

from sllm_store.v4_grouped_state import FORMAT
from sllm_store.v4_state import validate_identity

RPC_METHOD = "sllm_v4_export_receipt"
WORKER_EXTENSION = "sllm_store.v4_export_worker.V4ExportWorkerExtension"


class V4ExportWorkerExtension:
    def sllm_v4_export_receipt(self, identity):
        import torch

        from sllm_store.vllm_v4_loader import grouped_export_result

        validate_identity(identity)
        if (self.load_config.load_format != "sllm_v4_grouped_export"
                or self.load_config.model_loader_extra_config["identity"] != identity):
            raise ValueError("Worker is not exporting the requested grouped Store")
        result = grouped_export_result(self.get_model())
        if (set(result) != {"format", "identity", "rank", "grouped_state_sha256"}
                or result["format"] != FORMAT or result["identity"] != identity
                or type(result["rank"]) is not int or result["rank"] != self.rank):
            raise ValueError("Export worker receipt identity/rank mismatch")
        if len(json.dumps(result, allow_nan=False).encode()) > 16384:
            raise ValueError("Export receipt exceeds the control-message limit")
        torch.cuda.synchronize(self.device)
        return result
