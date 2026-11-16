# Sherlock v69 — reason gate (spec)

Branch `sherlock/v69-reason-gate`, new immutable tree `skills/v69` (copy of v68, no
`__pycache__`). Draft rules from v68 are unchanged. No paid runs.

## Evidence
- `references/sherlock-runs/2026-11-16-v68-full-stock-reasons-diagnosis.md` (vault):
  v68 full run failed the trusted triage gate with 159 `stock_reasons` blocking:
  143 rows bulk-closed by rules with the mark `N #R4 фон` (the teaching example
  `N #R1 фон` in bulk-closure.md), plus 16 named rows sharing one reason. The human
  render never printed `stock_reasons`; `worklist.py verdict` never checked reason
  text; `checkpoint.py handoff` returned rc=0 with 159 blocking.
- `.../2026-11-16-v68-journal-receipt-diagnosis.md`: the draft piped
  `checkpoint.py handoff` through `head -c 900`, which cut off the RECEIPT line.

## Changes
1. `triagecheck.stock_reasons(rows, exempt=())`: rows in `exempt` are not counted.
   `analyse()` passes the ids of rows held by a LIVE `#Rn` rule (rule exists, parses,
   its condition still covers the row), through the new single owner `live_cover()`
   (analyse's own loop uses it too). Rule `why` is already validated (>= 3 own
   words), so a bulk closure is not a pasted template. Named rows keep
   `max(3, int(5 % of rows))`. A dead tag (unknown rule, or rule no longer covering)
   is not exempt (and is blocked elsewhere as "без опоры").
2. `render()` prints a block when `stock_reasons` is non-empty: normalised reason,
   count, limit, first 10 row ids, action "give each row its own reason, or close
   them through one bulk rule with a specific why". JSON gains `stock_limit`.
   `BLOCKING_KEYS` is now a module constant (same members as before).
3. `worklist.py verdict`: after citation validation and before any write,
   `check_reason_limit()` simulates the batch and refuses (SystemExit non-zero,
   ledger and provenance untouched) if a normalised reason the batch touches exceeds
   the limit. Normaliser, limit and message are imported from triagecheck. Choice:
   at write time every row carrying any `#Rn` tag is exempt (rules.tsv may be
   written after the verdicts; a non-live tag is blocked by triagecheck). Old
   over-limit reasons the batch does not touch are left to triagecheck.
4. `checkpoint.py handoff --done triage` (full): runs `triage_gate()` after the
   "rows still open" check and before any state change; refuses with `✗ ...`
   plus the blocking summary on stdout, exit 1, no stage change, no receipt, no
   handoff.txt. Semantics kept/decided:
   - Before v69 handoff checked ONLY open rows; it ran no gate. v69 refuses on every
     class in `triagecheck.BLOCKING_KEYS` (the same set that makes triagecheck exit 1);
     no class is allowed through.
   - `--partial` is NOT gated (a mid-triage pause, rows still open by design).
   - Other stages are not gated.
   - Corpus: `--corpus`, else the active marker's corpus, else `<work>/corpus`,
     else `<work>/../corpus`. With a corpus: full `triagecheck.analyse` per
     authoritative worklist, rules `<work>/rules.tsv`. Without one: only the
     corpus-free class (repeated named reasons), and the message says so (other
     classes cannot be judged without the corpus; no fake pass).
   - `analyse()` is called directly: no checkpoint refresh side effect.
5. `handoff` prints `RECEIPT id=... sha256=... seq=...` first, then `RECEIPT_META`,
   then the block (or `--json` row). `journalcheck.printed_receipts` (package and
   harness v2) uses `finditer` over the whole output, so position does not matter;
   tested with output cut to 900 bytes.
6. `reference/bulk-closure.md`, `SKILL.md`, `reference/tools.md`: `N #R1 фон`
   replaced by `N #R1 каталог формы покрыт окном`; row-level limit stated; SKILL.md
   has one line that the tools enforce the stock-reason limit.
Version strings changed in v69 only: hostgate tool name, reportappend and
report-format `(v69)`, draft-contract title, worklist module name. The SKILL.md
label `DRAFT RULES (v68` is kept (rules unchanged).

## Tests
`tools/tests/test_v69_reason_gate.py` (17): exemption, liveness, limit, fixture 159 -> 16
(v68 = 159 asserted, v69 = 16), human render, harness v2 `triagegate` = 16, worklist
refusal (nothing written, accumulated batches, tag exemption), handoff refusal (no stage
change), pass + receipt first, partial ungated, full corpus gate, journalcheck.
Copies: `test_v69_boundary_contract`, `test_v69_draft_reuse_contract`,
`test_worklist_cursor_v69_weak_quote`. `test_gate_gaps_v56` made version-aware: from v69
the scripted-reason fixture rows sit under rules and are exempt (the rules themselves
are still refused). Fixture: `tools/tests/fixtures/v68-full-reasons/` (worklist.tsv,
rules.tsv of run `sherlock-v68-full-20261116a`, read-only copy).
Known, pre-existing (same on v68): `test_gate_gaps_v56` G1Journal
`test_valid_journal_passes` and `test_journalcheck_receipts_both_ways` fail (stale
package digest in fixture).
