# v65 drafting contract implementation checklist

- [x] Reproduce current parser and validator behavior on provider-free fixtures; existing citecheck suite: 83 passed, 1 skipped.
- [x] Add canonical drafting contract and explicit structure diagnostics.
- [x] Bind handoff receipts to report, package, journal, and boundary identity.
- [x] Add provider-free regression tests; focused drafting, trust, orchestration, and cite multi suite: 28 passed.
- [x] Exercise draft and two repair handoffs with distinct session identities and preserved artifacts.
- [x] Enforce the two-check repair cap in ordinary `check.py` calls; third call rejects before validation.
- [ ] Full `verify.sh` needs a local Qwen CLI; this host lacks `/Users/a/.local/bin/qwen`.
- [ ] Complete original-v64 replay identity remains unproved because the captured run metadata omitted package/code/input bindings.
