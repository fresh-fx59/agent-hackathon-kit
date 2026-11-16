# Sherlock v68 spec: draft reuses closed work

## Problem
In the v67 full run (`sherlock-v67-triage-full-20260929a`) the DRAFT session
ended with `wall_budget` (exit 55) at 3605 s and never assembled `work/report.md`
(793-byte stub kept). Investigate had already closed 250/250 worklist rows
(`checkpoint.json`: ready_for_synthesis).

## Evidence
Source: vault note `references/sherlock-runs/2026-11-16-v67-full-draft-stall-review.md`.
- 107 calls, 167,488 completion tokens (77% reasoning), 48 tok/s; model time 97% of wall.
- 2 context compressions (~135 s each, 269.9 s total); 4 mid-stream server errors (194.5 s).
- Evidence re-walk: 22 `cite.py`, 35 `read_file`; `report-format.md` read 5x.
- Report written as `rp1.md` (28.7 KB, one 205 s call) and `rp2.md` (16.2 KB, one 273.5 s call);
  coverage.txt/rollover.txt generated but never pasted. Tools used only 54.7 s.

## Change (skills/v68, prompt + one helper; investigate stage untouched)
1. Hard rule block "DRAFT RULES (v68)" in SKILL.md step 3: synthesis from closed work only;
   no corpus walking; `report-format.md` read once.
2. Write only `work/report.md`, one `### Н-n` block or one section per call, via new
   `tools/reportappend.py append` (replaces init stub on first write, keeps the
   `СИНТЕЗ НЕ ЗАВЕРШЁН` line last until `finish`; refuses empty, >12000 B, marker text;
   writes no other path).
3. `reportappend.py coverage|rollover` paste covermap.py / rollover.py output as
   «Покрытие» / «Окно записей» (deterministic, not model prose).
4. Gates unchanged (draftcheck, reportcheck, citecheck, checkpoint handoff, cite format).
   Version strings v67 -> v68 inside the tree.

## Acceptance
- Provider-free: `tools/tests/run.sh` green incl. `test_v68_draft_reuse_contract.py`.
- Small fixture gate passes on v68.
- Full run: draft finishes < 3600 s wall and assembles `work/report.md` with the
  marker removed; check.py verdict recorded. No paid run was made for this spec.

## Not covered
`thinking: disabled` not honoured by the route (77% reasoning) — harness/config, separate task.
