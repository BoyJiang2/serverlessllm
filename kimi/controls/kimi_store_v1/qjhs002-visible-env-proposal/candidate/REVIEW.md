# Visible-device fix candidate — no execution authorization

Source baseline reported by root: b868. Candidate files are work drafts only; compare against the corresponding active-source files before application. `visible-env.patch` records exact changes against the available work source snapshots, not a claim that remote b868 was independently fetched.

Only prepare adds two explicit environment arguments: `CUDA_VISIBLE_DEVICES=GPU-736d7f37-f647-b82d-ab11-693753ac6822` and `NVIDIA_VISIBLE_DEVICES=GPU-736d7f37-f647-b82d-ab11-693753ac6822`. Fixed UUID, Pids512 request, image, resources, probe and guard remain unchanged. Nonce and controller evidence BASE/systemd units change to the visible attempt so old CID/evidence remain frozen.

Existing offline tests update the expected nonce and assert each mask occurs exactly once with the fixed UUID. Suggested local-only validation, not executed in preparing this candidate:

`python -B -m unittest discover -s work/gpu512-visible-candidate/tests -p test_prepare_cuda_micro.py -v`

The test mocks subprocess and p0 paths, and therefore contacts no Docker daemon or GPU. It checks construction only and does not establish CUDA runtime success. Review the exact diff and SHA256SUMS.json, then validate patch applicability against active b868 source. No create/start is authorized by this candidate.

The previous GPU512 attempt stays FAILED. Its PYTHON_ENTERED marker plus the recorded empty CUDA mask/void NVIDIA mask and pure parser check support this minimal repair, but do not prove the next run would pass. Separately recorded issue: guard's first raw OCI capture can precede a later runtime rewrite; this patch does not change guard capture or claim the first raw OCI is the final runtime configuration.
