# Scoped task: qualify the selected Sherlock package

## Problem and hypothesis

The harness launcher and auditor hardcode v44 and bind only its SKILL.md bytes.
Full admission for v45 requires the canonical package digest and exactly matching
settings/tool identities. The old launcher also creates different absolute skill
paths and timeout settings. Its receipt cannot qualify the selected v45 run.

## Scope

Root owns run-harness-qualification.sh, harness-qualification.py and dedicated
harness tests/report. No runtime package, gate semantics, controller, target probe
or proxy edits. This is the harness portion of full-monitored-admission-spec.

## Contract

- Preserve the legacy one-output-argument launcher and legacy receipts.
- Add an explicit selected-package mode accepting a sealed target input directory.
  Validate its package, target profile and settings before secrets/provider use;
  snapshot the package and use only that copy throughout execution and audit.
- Copy exact target settings bytes. Stable lifecycle hook commands read per-run
  observer identity from environment; no run-specific settings rewrite. Bind
  settings and helper identities independently. A shared immutable skill catalogue
  contains only the selected generic package, never investigation state.
- Subscription qualification changes provider/model fields to broker gpt-5.5,
  while retaining the selected package, gate, tools, settings and Qwen identities.
  Generic synthetic corpus/answer key remain evaluation-only; no target findings.
- Preserve canonical whole-package/tool-tree digests. The legacy receipt field
  skill_v44_sha256 is an explicitly documented selected-package digest slot.
  Legacy v44 SKILL.md hash behavior remains only for old inputs without a selector.
- Audit derives package selection from sealed inputs, never ARM label alone.
  Wrong package, changed tree, settings, helper or bindings refuses qualification.
  Existing signature, terminal seal, matrix and independent gate checks remain.
- Preserve approved monitored execution and 600-second request watchdog in new
  mode. Do not insert aggregate caps to simplify compatibility. Controller
  integration owns supervision and null budgets.

## Acceptance

Reproduce hardcoded-package incompatibility in provider-free launcher/audit tests;
prove selected package/settings binding and refusal before provider contact;
legacy suite remains green. Review ready code once with Claude, record outcomes.
Actual subscription qualification follows integrated supervisor/runner tests.

## Timeline

- 2026-09-05 — Root read launcher/auditor and paid admission: v44-only paths,
  SKILL.md digest and divergent settings make the new matching receipt impossible.
  Lifecycle implementer confirmed stable settings plus per-run environment as
  the integration contract. No package or admission rule will be weakened.

## Final critique rulings

Codex gpt-5.6-sol low identified ambiguous selected-mode syntax, snapshot and
identity sources, settings/provider separation and missing adversarial tests.
Accepted. Raw final verdict is retained under run-reports.

Selected syntax is `run-harness-qualification.sh OUTPUT --target-input INPUT`.
INPUT is an existing target prepare directory (same required files/schema as
`target-contract-probe.py::_verify_package`); validate using that read-only helper.
No target nonce is consumed by subscription qualification. Validate before the
launcher checks SHERLOCK_API_KEY or calls Qwen/health/controller. Stage inputs into
an exclusive output directory and use `version-gate.py` verified registration,
canonical tree digest and snapshot machinery; reject links/hardlinks and verify
after copying. Revalidate the source and destination bindings before launch.

The selected target profile is copied with only broker URL/route/secret reference,
requested/returned model and identity assurance changed for subscription. Settings
have no provider/model selector rewrite: copy bytes exactly. Require its generation
timeout600000 and monitored profile request_read_timeout_s600. The selected full
budget uses schema2, all four aggregate limits null and request_timeout_ms600000.
All other settings must remain consistent with the copied profile.

Selected catalogue contains only a copy of the frozen runtime package. Controller
provides SHERLOCK_OBSERVER_DIR, SHERLOCK_RUN_NONCE, SHERLOCK_BOOT_ID; hook commands
read these plus working directory. The lifecycle spec governs validated identity,
permissions, atomic publication and fault handling. Lifecycle implementer owns the
signed launch/terminal receipt schema; selected qualification must use it too.

The selected-package record and target profile are bound by the controller input
manifest and terminal seal. Auditor checks canonical package and tools trees,
individual gate hashes, helper and settings identities against these sealed bytes.
`tool-schema.json` in selected mode is the canonical version-gate tree row preimage,
so its SHA256 equals the profile tool-tree digest. Legacy representation remains
only in legacy mode. Add copy mutation, alias, changed selector, settings and
post-snapshot source mutation tests. Never accept a caller-written hex-only proof.
