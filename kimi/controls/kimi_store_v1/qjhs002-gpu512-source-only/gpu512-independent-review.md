# GPU512 independent source review

SOURCE_REVIEW_PASS_CONDITIONAL_ON_FRESH_ADMISSION

Reviewed controller SHA256: 7e8e2f04e22eaa0af78418b0d14e3fca78ae08d0509466d007a03796f482f2a7.
Plan SHA256: 57fc8a3d8d4afdabd51fcb3e55a1c048f8c10f9caaef93659478b740434b1a48.
Guard SHA256: 9387374428ae285f9740a4fe6f638aec2ba781384a5a9fdc01b47f6b2249ea3d.
Probe SHA256: 9aca03c25354ae794430e876d4fb7032ead92e4e7e37578835818c8e2770c629.

No remaining blocking source findings in this revision. This review inspected local source only; it did not access the remote host, operate containers/GPU, or run Git/synchronization.

The reviewed revision resolves owned Docker client cleanup and exit race, atomic GO publication, observer baseline settling, independent observer/guard liveness, observer UUID/peak/quiet validation, and actual successful service exits. Docker calls and service queries are bounded; root guard remains independent of Docker and observer. ARM/GO acknowledgement precedes the only nonblocking start. Failure requests ABORT and retains independent cleanup/release.

Root must still verify fresh booking/resources with the full 180-second budget, stopped container's complete profile, and deployed hashes before this single start. Booking refresh/create/full profile checks are explicitly external steps. Root must export exact-CID and time-window kernel logs after release and preserve prior GPU128 and CPU fault evidence. No retry, model, Store, TP8, Git or synchronization during the measured operation and release.

The old plan's statement that baseline mismatch immediately aborts is superseded by the controller's quiet-reset behavior: post-exit settling resets the 60-second interval within the unchanged budget; exceeding 2048 MiB or query failure still aborts. This is a source admission result, not CUDA or model runtime proof.
