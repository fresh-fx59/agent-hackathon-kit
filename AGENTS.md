# AGENTS.md — agent-hackathon-kit

Working agreements for any coding agent in this repo.

## Harness: we continue with Claude Code

**All case-06 implementation happens in Claude Code** (decision 2026-07-28).
Codex was used for cross-review only; its artifacts are archived under
`cases/6-log-analisys-codex/` (research, case summary, OpenSpec MVP increment,
`.codex/` skills) and are **planning/review documents, not build targets**.
Their accepted ideas are already folded into the authoritative spec via the
cross-review — do not code from the OpenSpec change directly.

## Case 06 — what we are actually building (operator, 2026-07-28)

Not a hackathon prop. **An internal tool the whole company uses**, invoked from
the corporate **Qwen Coder CLI** by developers and DevOps/SRE engineers. Every
design decision is judged against the four requirements below; if a feature
does not serve one of them, it does not ship.

**R1 — Zero configuration. This is the primary requirement.**
The happy path is: *copy the skill folder (plus any rules/MCP it brings) into
Qwen Coder's skills directory, and start using it.* Nothing else. No env vars,
no config file to edit, no install step, no service to run, no credentials to
supply. An engineer who has never heard of this project must get a useful
answer on their first try.
Consequences, and they are binding:
- The skill must work **standalone**, with only the tools the CLI already has.
- Any helper script ships **inside the skill folder**, pure `python3` stdlib,
  no `pip install`, and the skill must still work correctly if the script
  cannot run at all (**graceful degradation, never a hard dependency**).
- Never require an MCP server for the happy path. MCP is an *optional*
  upgrade for teams that have a log backend.
- Never ask the engineer to configure a model, a key, or a base URL — the
  corporate CLI is already configured.

**R2 — Any log format.** Any language, any framework, any team's bespoke
format, any human language, including formats nobody anticipated. This is why
the model reads the logs and code does not parse them: measured 100 % recall on
a bespoke pipe-delimited format with an invented severity vocabulary, and on a
Russian-language log, with no schema and no parser.

**R3 — Any size.** From a pasted 20-line snippet to multi-GB dumps. Measured:
100 % recall on a 649 MB / 4.26 M-line corpus while reading 0.09 % of it.
The binding constraint is **coverage** (which files got opened), not context
window — recall went 100 % → 73 % → 18 % on the *same* corpus and model purely
by sampling policy.

**R4 — Extensible with ease.** Adding a new log source must not touch the core.
One contract — `resolve(spec, window) -> local bytes + manifest`, implementations
must not parse content. New backends (Elasticsearch, Loki, Grafana, Kubernetes)
arrive as **existing MCP servers**, i.e. configuration, not code.

Authoritative spec: `cases/06-dev-logging/docs/specs/2026-07-28-sherlock-simplest-approach-design.md`
(supersedes `claude-code/docs/specs/2026-07-28-design.md`, whose deterministic
parsing spine is retired — see the spec's evidence section for why).

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
