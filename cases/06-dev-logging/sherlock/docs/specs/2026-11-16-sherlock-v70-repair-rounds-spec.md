# Sherlock v70: repair rounds count only after an edit

Goal: a repair session must not spend its 2-round budget on re-reading output.
Evidence: v69 small run a (`references/sherlock-runs/2026-11-16-v69-small-repair-quit-diagnosis.md`):
`check.py | tail -80`, then `| head -60` with no edit between; cap hit; partial handoff with 0 edits, 54 mechanical blockers left.

Hypothesis: counting only rounds that follow a report edit, plus unhideable output, lets the session fix items before the cap.

Files (v70 only; v69 untouched): `skills/v70/tools/check.py`, `skills/v70/SKILL.md` (after the repair-phase paragraph), version strings in hostgate/draft-contract/report-format. Tests: `tools/tests/test_v70_repair_rounds.py`; v69 tests honor `SHERLOCK_PKG`.

Changes
1. `begin_repair_round`: a round counts only when `work/report.md` sha256 differs from the sha stored after the last check (`validation/repair-check-shas.json`). The sha is taken AFTER the check runs, because check.py rewrites the report itself. A no-edit rerun re-runs, does not increment, and prints "no edit since last check — not counted; fix the listed items, then re-check". Missing report counts (cannot compare). Cap 2 and key `repair-<boundary_seq>` unchanged; a third EDITED round is refused.
2. check.py writes its complete output to `work/validation/last-check.txt`; first and last line are `ИТОГ: <verdict> — full output: work/validation/last-check.txt`.
3. SKILL.md: never pipe check.py through head/tail/grep; read last-check.txt; «вставь строкой ниже» census lines are pasted verbatim; `not-shown` refs are fixed with `cite.py <file>:<line>`.

Non-goals: triage/draft rules, cap value, handoff logic.
Acceptance: v70 tests (9), v69 reason-gate/draft/weak-quote/boundary tests against v70 (36), harness v2 tests (6) pass. No paid run.
Cross-model critique: skipped (codex subscription expired).
