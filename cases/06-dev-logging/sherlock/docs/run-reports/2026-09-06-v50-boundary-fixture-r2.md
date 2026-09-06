# V50 boundary fixture r2

Status: passed fixture; no provider contact.

## Identity

Execution code `46549d8`; v50 package SHA `0f95b5a59dcc41500168d3f2dc6df3e237b8c68e9d3666bf181716983d902ee7`; driver `c31be32f3d52a59423ad40b157eb550ba097e89833e47339eecbf15516b436b0`; helper `861593a235903f6de92c53a0b27d9bde5888aaead2d06acf97f3c3529c0fc2cb`.

## Timeline

- 2026-09-06 — Fresh v50 boundary fixture r2 completed with `passed: true` using the loopback provider only. No external or paid provider was contacted.
- 2026-09-06 — Eight requests were recorded: normal indices `0,1,2,5,6,7` and auxiliary suggestions `3,4`. The pending-stage denial had no post-tool event, the sentinel was unchanged, and a fresh-stage edit was allowed.
- 2026-09-06 — The fixture proved a fresh clear (`SessionStart(clear)` with a new session and combined reseed invocation) and recorded the intentional fixture stop after the second pause. The driver exited `143` because the fixture watcher terminated it after the proof; this is expected for this fixture.
- 2026-09-06 — Local and remote inventories matched at 179/179 files after excluding directory names exactly `home` and `updates`; the remote source tree was left untouched.

## Evidence

Raw result, driver events, launch streams, and inventories are under `docs/run-reports/artifacts/2026-09-06-v50-boundary-fixture-r2/`.
