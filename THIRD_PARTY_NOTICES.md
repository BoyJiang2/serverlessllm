# Sources and licensing

Upstream dependency sources are referenced by exact commits and patches; environments, model weights, Store payloads and compiled binaries are excluded.

ServerlessLLM integration patch provenance is in `patches/integration-candidate-ledger-v5.json`: upstream commit `9f50241baa5386e06a9321c51f19a9ef5f964c2b`. vLLM's exact upstream commit and file hashes are in the same ledger. Earlier patch revisions are preserved as history, not cumulative instructions to apply every patch.

Applicable upstream licenses and copyright headers govern their respective patch contexts/code. Repository ownership does not relicense upstream material. `LICENSES/` retains the license from the runtime ServerlessLLM source copy and vLLM's exact upstream revision; `dependencies/upstream-source-pins.json` records their provenance. Do not assume a new repository-wide license for all content.

The fixed model is `zai-org/GLM-5.1-FP8`, revision `f396cf805182f4ca10fa675e1a99815b3ca384db`. Only lightweight hashes, token outputs and validation records are retained here; model data is not distributed. Users must separately comply with the model's applicable terms.
