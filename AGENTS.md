# AGENTS.md — agent-hackathon-kit

Working agreements for any coding agent in this repo.

## Harness: we continue with Claude Code

**All case-06 implementation happens in Claude Code** (decision 2026-07-28).
Codex was used for cross-review only; its artifacts are archived under
`cases/6-log-analisys-codex/` (research, case summary, OpenSpec MVP increment,
`.codex/` skills) and are **planning/review documents, not build targets**.
Their accepted ideas are already folded into the authoritative spec via the
cross-review — do not code from the OpenSpec change directly.

## Folder rules

- `cases/06-dev-logging/claude-code/` — the case-06 solution (the submittable
  deliverable). All implementation code, docs, and specs live here.
  Authoritative docs: `docs/specs/2026-07-28-design.md` (design incl.
  cross-review amendments) and `docs/specs/2026-07-28-cross-review-codex.md`
  (pinned team contracts).
- `cases/6-log-analisys-codex/` — Codex artifacts only (review/planning
  archive). Must never contain `benchmark.py` or `test_*.py`:
  `scripts/verify.sh` autoglobs those repo-wide into the CI gate.
- Everything else — the rehearsal kit (mocks, MCP lib, rehearsal cases,
  scaffolder, CI). Contract for every case benchmark: `--self-test` (exit
  0/1) and `--score-only` (one numeric line); `ci/gate.sh` prints the
  `benchmark score: N (min M)` line.

## Constraints

- Pure Python stdlib, py≥3.9, agent-agnostic (must run under the corporate
  GigaCode agent unchanged). No pip installs.
- Russian prose for jury-facing artifacts (SKILL.md, README, workflows,
  reports); English code and identifiers.
- Repo stays **private until the hackathon ends** (answer keys live here).
- `bash scripts/verify.sh` must stay green; run it before pushing.
