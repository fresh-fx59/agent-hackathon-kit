# V50 launch preparation

Prepared from the reviewed v49 templates without launching a process or contacting a provider. Every `v49` arm, package version, skill path, and root name was changed to `v50`; helper and interactive-driver pins were preserved from the templates.

Validation: `bash -n` passed for all seven scripts. The scripts are templates only and have not been executed.

## Unresolved inputs before launch

- The v50 runtime/package commit and skill SHA must be supplied by the implementation lane and checked against the target package before any launch.
- Fresh v50 target and harness roots must be absent at launch; the scripts intentionally fail if a root already exists.
- The v50 target probe must produce a new `probe-manifest.json`; the paid/full scripts require a generated harness acceptance receipt and target-contract receipt.
- The full Winevtx and independent input manifests, rate snapshot, provider identity, and paid admission receipt must be generated/verified for v50. No v49 receipt or output may be reused.
- The independent scripts still use the reviewed corpus layout (`.../independent` for full input and the sibling Winevtx corpus for target calibration); confirm those paths exist on the remote host.
