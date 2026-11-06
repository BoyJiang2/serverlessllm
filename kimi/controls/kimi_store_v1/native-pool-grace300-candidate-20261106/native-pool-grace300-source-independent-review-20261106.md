# Independent source-only review: bounded Store grace300

Verdict: ACCEPT_SOURCE_ONLY. No GPU/container/Store execution, real candidate publication, ELF rebuild, or Git action was performed by this reviewer. Review time: 2026-11-06T05:37:51.127367+00:00.

Reviewed current existing generator and focused test:
- prepare_native_stop_diagnostic.py SHA256 82a3dbc0f19e59896b397e3b4752834f8414e2af053a1fd0f2baf799681fa465
- python/test_prepare_native_stop_diagnostic.py SHA256 39fb6b78d9df6fefe24e70e123824b7e6f17a46e8d3ab1f23a07dedf574893be

The explicit --pool-grace300 path derives immutable spec539f4c68239f73d9cd48cb7cd9c7143acc8b22d0f40f1c7f6b041aba8c12c0b4 and commandb72f4d3d29e2e44f796f5ec41c26bb738825f5b206452e9cb193e4d10e8afa5f. It preserves the nine consumed role bodies, requests except distinct names, actual overlays/mounts, source/asset pins, capacity, AAB gold, physical ownership, observer and guard configurations. Existing ELF9fcb remains unchanged. Legacy preparation tests remain intact.

Before controller_shutdown or any final stop, actual generated code admits a complete600-second serial final-stop slot,120-second cleanup/release reserve, and180-second outer evidence reserve. It checks actual unit activation/native RuntimeMax, observer duration, authenticated GO CID/nonce/deadline, outer deadline and started+6600. A late admission fails rather than shortening waits. Store alone receives a fresh strict300-second deadline; API30/controller125/Ray40x2 remain. Both normal_exit calls are followed by the aggregate stop_end assertion before the observer normal-completion marker. Full budget7200 is unchanged; phase sum6540 is bookkeeping, not a simultaneous-maxima success guarantee. Actual guard overall uses GO; using the earlier activation for this check is conservative.

Independent command: python3 -m unittest discover -s ROOT/sllm_store/tests/python -p test_prepare_native_stop_diagnostic.py -v. Actual14/14 tests PASS,0 skips,0.299s. New cases execute AST extracted from the actual generated final block, verify caps125+30+300+40+40+17+17=569, reject missing actual time slots/identity/runtime drift before shutdown, reject combined exits beyond600, and invert the exact authorized body delta. They also check native build provenance and consumed mount/pin identity.

Limitations:569 is the mocked call-cap arithmetic, not a measured or hard bound for all Docker inspect calls, scheduling, cleanup or OS overhead. The600 aggregate and existing absolute guards may still reject a slow run. This change does not prove Store shutdown finishes within300, explain all driver blocking, or promote prior failed lifecycle runs. Named Store timeout remains only in this new mode. Original failures and immutable command data remain unchanged. Actual post-commit rendered artifact SHA/source cleanliness and fresh complete runtime admission still require separate review before any user-approved GPU run.
