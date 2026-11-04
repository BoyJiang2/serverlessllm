# Protocol correction requested by user

The final Qwen cold/warm run and Llama random/cached run BOTH used the fox prompt and generate(max_new_tokens=50). Hello belonged only to the original Qwen single-forward experiment. Prompt parity does not imply loading-protocol parity.

Prior Qwen cold/warm used BF16,3 trials per cache condition, explicit page-cache eviction/residency checks or cache warmup, and CUDA-synchronized loading timing. It is a supplementary cache experiment, not the official random/cached reproduction.

Official pinned9f50241 protocol uses FP16, random30 physical independent model replicas loaded once each in random order, and cached5 measured loads of one replica after warmup. Random does not enforce cold cache. Both formats use the original API wall-clock load timer without an added CUDA synchronization endpoint. The existing generation-config, buffer-dtype and private-API compatibility corrections remain required on this runtime.

New Qwen official-protocol files are prepared, uploaded and SHA verified. FP16 actual SafeTensors CPU buffer comparisons pass both SDK constructors. GPU probe and random30/cached5 remain pending at this preparation milestone. New native bounds are600seconds probe and3600seconds formal run, each with60seconds release observation and180seconds total cleanup/evidence allowance. Final result will report mean, median, standard deviation andn for both models. Earlier runs/failures remain intact.
