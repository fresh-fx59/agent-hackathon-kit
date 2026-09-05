# v45 finalization repair — focused verification

**Scope:** portable report finalization, runtime instructions, Stop integration,
and probe prompt. The version gate, registration, controller, manifest, and
paid/target execution are outside this report.

## Result

Candidate `skills/v45` is copied from unchanged `v44`. It adds
`tools/finalize.py`, which runs the four shipped validators with the reviewed
arguments and `sys.executable`. Every invocation exclusively creates
`work/validation/<attempt-id>/`, writes its receipt before a child starts and
after each child completes, and stores raw stdout/stderr for all four gates.
The receipt contains argv, per-tool hashes, exit codes, parsed blocking counts,
and identities for every manifest-referenced work input and all corpus evidence.
The package tree hash is labelled `version-gate.tree_digest/v1`: it uses the
same canonical sorted JSON file-list and SHA-256 algorithm as the version gate.
The helper runs children from `work.parent`, applies one 60-second default
deadline across the sequence (or the tighter Stop budget), and returns nonzero when an exit, timeout, parsed count, output
shape, package identity, or before/after input identity is not clean. It never
removes evidence.

`stopcheck.py` invokes that helper after its existing checks, blocks on a failed
attempt, and has a recursion guard. It does not implement the separately scoped
lifecycle snapshots or supervision behavior.

The probe no longer seeds four findings or fixture conclusions. It tells the
runtime to use the selected package finalizer, repair failures, retain validation
artifacts, and send only the report's contents to the user.

## Timeline

- 2026-09-05: The initial focused test was run before the helper existed. It
  failed with `FileNotFoundError` for `skills/v45/tools/finalize.py`, confirming
  the test exercised the new behavior rather than v44.
- 2026-09-05: Initial synthetic coverage passed (`5 tests in 0.350s`), but
  review correctly found it was insufficient: it used substitute gates and did
  not prove the actual validators, Stop path, mutation detection, or durable
  deadline receipt.
- 2026-09-05: The first canonical actual-gate fixture failed `citecheck`:
  the report cited `Security.jsonl:3` but the deliberately constructed
  worklist omitted that row. The fixture was repaired by adding the real source
  row; validator code and its semantics were unchanged.
- 2026-09-05: Actual `reportcheck`, `citecheck`, `statecheck`, and
  `triagecheck` then each returned exit `0` and parsed `blocking: 0` on the
  canonical fixture. A known broken report with its worklist removed returned a
  blocking attempt and retained all four raw output pairs.
- 2026-09-05: The focused suite proved a mutating gate makes an otherwise green
  receipt blocking, and a `0.01s` global deadline records a `124` result plus
  artifacts and receipt rows for every gate.
- 2026-09-05: Actual v45 `stopcheck.py` allowed the canonical fixture and
  retained its clean attempt. It visibly blocked an unfinished report. A second
  actual Stop test added an unreported service-install state group: legacy Stop
  gates accepted the otherwise complete report, v45 finalization blocked on
  `statecheck`, and `work/validation/<attempt>/metadata.json` remained with a
  positive statecheck blocking count.
- 2026-09-05: `python3 -m unittest tools.tests.test_finalize_v45 && python3
  -m py_compile skills/v45/tools/finalize.py skills/v45/tools/stopcheck.py
  tools/tests/test_finalize_v45.py` passed: `Ran 12 tests in 1.775s`, `OK`.
- 2026-09-05: Root independently reran the full version/probe suite against
  the current prompt: `84 tests in 59.376s`, passing.
- 2026-09-05: Review found that v45's helper unconditionally selected
  `work/worklist.tsv`, conflicting with the shipped multi-host marker contract.
  The helper now calls the existing safe marker selection and composition logic,
  moves the resulting ledger into its unique validation attempt, and gives that
  retained file to citecheck and triagecheck. An actual canonical two-host
  fixture passed all four gates and retained the selected-ledger receipt. A
  missing canonical ledger now produces a blocking durable receipt before gates
  can run against a guessed path. Direct finalizer tests also reject duplicate
  and outside-marker ledger declarations before gate execution. Actual v45
  Stop also allowed the canonical multi-host marker and retained its composed
  ledger. The full focused run observed `Ran 15 tests in 2.175s`, `OK`.

## Limits and handoff

This candidate has not been registered and no Sherlock model was invoked. Root
must first land the independently owned version-gate work, review this candidate,
register and bind v45, then qualify the final package. The v45 helper's standard
contract is one `work/worklist.tsv` and `work/rules.tsv`, exactly as the reviewed
finalization specification defines.

## Root review, verification and registration

- 2026-09-05T20:43:58.692503+00:00 — Scoped Claude review PASS; root found and required the multi-host compatibility correction described above, then inspected retained composition and shared strict selector semantics. Root independently reran all15 finalizer tests: passed in2.371s. Existing four gate files remain byte-identical to v44. Registered immutable v45 with canonical digest `36a0dfe2c674ac11a13d1dc1b6a0b8c3e720fdb08577c8c9ec5d386cd317e40e`. No model invocation or corpus acceptance is claimed. Future runtime change requires a new version.
